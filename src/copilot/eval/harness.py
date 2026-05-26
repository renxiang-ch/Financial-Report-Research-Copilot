"""
Eval harness: run the agent against eval_set.json and score results.

Metrics:
  - tier1_accuracy     : % of Tier-1 numeric questions within tolerance
  - tier2_accuracy     : % of Tier-2 numeric questions within tolerance
  - input_value_hit    : % of Tier-2 questions where agent fetched correct raw values
  - passage_hit        : % of retrieval questions where key_phrase found in retrieved chunks
  - avg_judge_score    : mean LLM-judge score (0–3) for retrieval questions
  - retrieval_accuracy : % of retrieval questions where passage_hit AND judge>=2
  - refusal_accuracy   : % of unanswerable questions correctly refused
  - overall_accuracy   : across all questions
  - avg_latency_s      : mean wall-clock seconds per question
  - estimated_cost_usd : gpt-4o-mini API cost

Usage:
    python -m copilot.eval.harness
    python -m copilot.eval.harness --dataset data/eval_set.json --tier 1
    python -m copilot.eval.harness --out data/eval_results_latest.json
    python -m copilot.eval.harness --limit 5
"""

import argparse
import json
import re
import time
from pathlib import Path

from openai import OpenAI

# gpt-4o-mini pricing (USD per 1M tokens)
_INPUT_COST_PER_M  = 0.15
_OUTPUT_COST_PER_M = 0.60


# ── number utilities ──────────────────────────────────────────────────────────

def _extract_number(text: str) -> float | None:
    """
    Pull the first recognisable number out of an answer string.
    Handles: $391.0B, 46.2%, $93,736M, -0.72, 2.02%
    """
    text = text.replace(",", "").replace("$", "").replace("%", "")
    matches = re.findall(r"-?\d+\.?\d*", text)
    if not matches:
        return None
    for m in matches:
        val = float(m)
        if 1990 <= val <= 2030:  # skip years
            continue
        return val
    return None


def _within_tolerance(got: float, expected: float, tol_pct: float) -> bool:
    """True if got is within tol_pct% of expected, accounting for SI magnitude differences."""
    if expected == 0:
        return abs(got) < 1e-9
    ratio = got / expected
    if abs(ratio - 1.0) * 100 <= tol_pct:
        return True
    for scale in (1e9, 1e6, 1e3):
        if abs((got * scale) / expected - 1.0) * 100 <= tol_pct:
            return True
        if abs((got / scale) / expected - 1.0) * 100 <= tol_pct:
            return True
    return False


# ── refusal detection ─────────────────────────────────────────────────────────

def _is_refusal(text: str) -> bool:
    refusal_phrases = [
        "cannot determine", "can't determine", "unable to determine",
        "not available", "no data", "not in", "cannot find",
        "don't have", "do not have", "outside", "not found",
        "unable to answer", "cannot answer", "not tracked",
        "not in our database", "not covered",
    ]
    lower = text.lower()
    return any(p in lower for p in refusal_phrases)


# ── retrieval scoring helpers ─────────────────────────────────────────────────

def _check_key_phrase(steps: list[dict], golden_citations: list[dict]) -> bool:
    """
    Scan all retrieve_text output chunks in agent steps.
    Return True if any golden key_phrase appears in any retrieved chunk.
    """
    retrieved_texts: list[str] = []
    for step in steps:
        if step.get("tool") == "retrieve_text":
            for r in step.get("output", {}).get("results", []):
                retrieved_texts.append(r.get("text", ""))

    for gc in golden_citations:
        key_phrase = gc.get("key_phrase", "")
        if not key_phrase:
            continue
        for text in retrieved_texts:
            if key_phrase.lower() in text.lower():
                return True
    return False


# ── Tier-2 input value verification ──────────────────────────────────────────

def _check_input_values(steps: list[dict], input_values: dict,
                        tol_pct: float = 0.5) -> dict[str, bool]:
    """
    For Tier-2 questions: verify that query_financials calls returned the
    correct raw values (the ones needed for the formula).

    Returns a dict: {variable_name: hit (bool)}
    """
    qf_values: list[float] = []
    for step in steps:
        if step.get("tool") == "query_financials":
            out = step.get("output", {})
            if out.get("found") and out.get("value") is not None:
                try:
                    qf_values.append(float(out["value"]))
                except (TypeError, ValueError):
                    pass

    hits: dict[str, bool] = {}
    for var_name, expected_val in input_values.items():
        hits[var_name] = any(
            _within_tolerance(v, float(expected_val), tol_pct)
            for v in qf_values
        )
    return hits


# ── LLM judge ─────────────────────────────────────────────────────────────────

_judge_client: OpenAI | None = None

def _llm_judge(question: str, golden_answer: str, agent_answer: str) -> dict:
    """
    Use gpt-4o-mini to score agent_answer vs golden_answer.
    Returns {"score": 0–3, "reason": str}
    """
    global _judge_client
    from copilot.config import settings
    if not settings.openai_api_key:
        return {"score": -1, "reason": "no API key"}
    if _judge_client is None:
        _judge_client = OpenAI(api_key=settings.openai_api_key)

    prompt = f"""You are evaluating a financial research assistant's answer.

Question: "{question}"

Reference answer (ground truth from the 10-K filing):
{golden_answer}

Generated answer:
{agent_answer}

Score 0–3:
3 = Fully correct — all key facts from reference present, no fabrications
2 = Mostly correct — main point captured, minor omissions or slight inaccuracies
1 = Partially correct — misses important facts or has notable errors
0 = Incorrect or fabricated — contradicts reference or invents information

Respond with ONLY valid JSON:
{{"score": <0|1|2|3>, "reason": "<one concise sentence>"}}"""

    response = _judge_client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=128,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return json.loads(response.choices[0].message.content.strip())
    except Exception:
        return {"score": -1, "reason": "parse error"}


# ── tool trace ───────────────────────────────────────────────────────────────

def _build_tool_trace(steps: list[dict]) -> list[str]:
    """
    Build a compact human-readable trace of all tool calls for one question.
    Each entry is one line: "tool_name(args) → result_summary"
    """
    trace = []
    for step in steps:
        tool = step.get("tool", "?")
        inp  = step.get("input", {})
        out  = step.get("output", {})

        if tool == "query_financials":
            ticker = inp.get("ticker", "?")
            metric = inp.get("metric", "?")
            fy     = inp.get("fiscal_year", "latest")
            if out.get("found"):
                val  = out.get("value")
                cite = out.get("citation", "")
                trace.append(f"query_financials({ticker}, {metric}, FY{fy}) → {val}  [{cite}]")
            else:
                trace.append(f"query_financials({ticker}, {metric}, FY{fy}) → NOT FOUND")

        elif tool == "compute":
            expr   = inp.get("expression", "?")
            result = out.get("result")
            trace.append(f"compute({expr}) → {result}")

        elif tool == "list_metrics":
            ticker  = inp.get("ticker", "?")
            metrics = out.get("metrics", [])
            trace.append(f"list_metrics({ticker}) → {len(metrics)} metrics available")

        elif tool == "retrieve_text":
            query   = inp.get("query", "?")
            ticker  = inp.get("ticker", "all")
            results = out.get("results", [])
            chunk_summaries = [
                f"{r.get('ticker')}:{r.get('section')} score={r.get('score', 0):.3f}"
                for r in results[:5]
            ]
            trace.append(
                f"retrieve_text('{query}', ticker={ticker}) → "
                f"{len(results)} chunks: [{', '.join(chunk_summaries)}]"
            )

        else:
            trace.append(f"{tool}({inp}) → {out}")

    return trace


# ── item scorer ───────────────────────────────────────────────────────────────

def score_item(item: dict, agent_result: dict) -> dict:
    answer_text = agent_result.get("answer", "")
    steps       = agent_result.get("steps", [])

    # ── unanswerable ─────────────────────────────────────────────────────────
    if not item["answerable"]:
        correct = _is_refusal(answer_text)
        return {
            "id":               item["id"],
            "tier":             item["tier"],
            "type":             "unanswerable",
            "answerable":       False,
            "correct":          correct,
            "refusal_detected": correct,
            "got_text":         answer_text[:200],
        }

    # ── retrieval ─────────────────────────────────────────────────────────────
    if item.get("type") == "retrieval":
        passage_hit = _check_key_phrase(steps, item.get("golden_citations", []))
        judge       = _llm_judge(item["question"], item.get("golden_answer", ""), answer_text)
        judge_score = judge.get("score", -1)
        correct     = passage_hit and judge_score >= 2
        return {
            "id":           item["id"],
            "tier":         item["tier"],
            "type":         "retrieval",
            "answerable":   True,
            "correct":      correct,
            "passage_hit":  passage_hit,
            "judge_score":  judge_score,
            "judge_reason": judge.get("reason", ""),
            "got_text":     answer_text[:300],
        }

    # ── numeric (Tier-1 and Tier-2) ───────────────────────────────────────────
    # Prefer compute step output (exact, preserves sign) over text extraction
    # (text extraction loses negatives: "declined by 2.8%" → +2.8)
    got_raw = None
    for step in reversed(steps):
        if step.get("tool") == "compute":
            raw = step.get("output", {}).get("result")
            if raw is not None:
                try:
                    got_raw = float(raw)
                    break
                except (TypeError, ValueError):
                    pass
    if got_raw is None:
        got_raw = _extract_number(answer_text)

    correct = False
    if got_raw is not None:
        correct = _within_tolerance(got_raw, item["expected_value"], item["tolerance_pct"])

    scored: dict = {
        "id":        item["id"],
        "tier":      item["tier"],
        "type":      "numeric",
        "answerable": True,
        "correct":   correct,
        "expected":  item["expected_value"],
        "got_raw":   got_raw,
        "got_text":  answer_text[:200],
        "unit":      item["expected_unit"],
    }

    # Tier-2 extra: verify agent fetched the correct raw input values
    if item["tier"] == 2 and item.get("input_values"):
        iv_hits = _check_input_values(steps, item["input_values"])
        scored["input_values_hit"]    = iv_hits
        scored["all_inputs_fetched"]  = all(iv_hits.values())

    return scored


# ── main harness ──────────────────────────────────────────────────────────────

def run_eval(dataset_path: Path, tier_filter: int | None = None,
             limit: int | None = None) -> dict:
    from copilot.agent.agent import ask

    with open(dataset_path) as f:
        data = json.load(f)

    items = data["items"]
    if tier_filter:
        items = [i for i in items if i["tier"] == tier_filter]
    if limit:
        items = items[:limit]

    results = []
    total_input_tokens  = 0
    total_output_tokens = 0
    total_latency       = 0.0

    print(f"Running {len(items)} questions  (dataset v{data.get('version','?')})\n")

    for idx, item in enumerate(items, 1):
        print(f"[{idx:02d}/{len(items)}] {item['id']}")
        print(f"       Q: {item['question']}")

        t0 = time.time()
        try:
            result = ask(item["question"])
        except Exception as e:
            result = {"answer": f"ERROR: {e}", "steps": [], "citations": [], "usage": {}}
        elapsed = time.time() - t0
        total_latency += elapsed

        total_input_tokens  += result.get("usage", {}).get("input_tokens",  0)
        total_output_tokens += result.get("usage", {}).get("output_tokens", 0)

        scored               = score_item(item, result)
        scored["latency_s"]  = round(elapsed, 2)
        scored["steps"]      = result.get("steps", [])
        scored["citations"]  = result.get("citations", [])
        scored["tool_trace"] = _build_tool_trace(result.get("steps", []))
        results.append(scored)

        status = "PASS" if scored["correct"] else "FAIL"
        if not item["answerable"]:
            print(f"       {status} refusal={'yes' if scored.get('refusal_detected') else 'NO'} ({elapsed:.1f}s)")
        elif item.get("type") == "retrieval":
            print(f"       {status} passage={'hit' if scored.get('passage_hit') else 'MISS'}  judge={scored.get('judge_score')}/3 ({elapsed:.1f}s)")
        else:
            iv_ok = scored.get("all_inputs_fetched")
            iv_str = f"  inputs={'ok' if iv_ok else 'MISSING'}" if iv_ok is not None else ""
            print(f"       {status} expected={item['expected_value']}  got={scored.get('got_raw')}{iv_str} ({elapsed:.1f}s)")

        # always print tool trace so you can see exactly what the agent did
        for line in scored["tool_trace"]:
            print(f"         > {line}")
        print()

    # ── aggregate metrics ─────────────────────────────────────────────────────

    unanswerable = [r for r in results if not r["answerable"]]
    numeric      = [r for r in results if r.get("type") == "numeric"]
    retrieval    = [r for r in results if r.get("type") == "retrieval"]
    tier1        = [r for r in numeric if r["tier"] == 1]
    tier2        = [r for r in numeric if r["tier"] == 2]
    tier2_iv     = [r for r in tier2 if "all_inputs_fetched" in r]

    def acc(lst: list) -> float | None:
        return round(sum(r["correct"] for r in lst) / len(lst) * 100, 1) if lst else None

    cost_usd = (
        total_input_tokens  / 1e6 * _INPUT_COST_PER_M +
        total_output_tokens / 1e6 * _OUTPUT_COST_PER_M
    )

    passage_hit_acc = (
        round(sum(r.get("passage_hit", False) for r in retrieval) / len(retrieval) * 100, 1)
        if retrieval else None
    )
    judge_scores = [r["judge_score"] for r in retrieval if r.get("judge_score", -1) >= 0]
    avg_judge    = round(sum(judge_scores) / len(judge_scores), 2) if judge_scores else None

    input_fetch_acc = (
        round(sum(r["all_inputs_fetched"] for r in tier2_iv) / len(tier2_iv) * 100, 1)
        if tier2_iv else None
    )

    summary = {
        "dataset":              str(dataset_path),
        "dataset_version":      data.get("version"),
        "n_total":              len(results),
        "tier1_accuracy":       acc(tier1),
        "tier2_accuracy":       acc(tier2),
        "tier2_input_fetch":    input_fetch_acc,
        "retrieval_accuracy":   acc(retrieval),
        "passage_hit_accuracy": passage_hit_acc,
        "avg_judge_score":      avg_judge,
        "refusal_accuracy":     acc(unanswerable),
        "numeric_accuracy":     acc(numeric),
        "overall_accuracy":     acc(results),
        "avg_latency_s":        round(total_latency / len(results), 2) if results else 0,
        "total_latency_s":      round(total_latency, 1),
        "input_tokens":         total_input_tokens,
        "output_tokens":        total_output_tokens,
        "estimated_cost_usd":   round(cost_usd, 4),
        "results":              results,
    }

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/eval_set.json")
    parser.add_argument("--tier",  type=int, default=None, help="Filter to tier 1 or 2")
    parser.add_argument("--limit", type=int, default=None, help="Run only first N questions")
    parser.add_argument("--out",   default=None, help="Save full results JSON")
    args = parser.parse_args()

    summary = run_eval(Path(args.dataset), tier_filter=args.tier, limit=args.limit)

    print("=" * 60)
    print("EVAL SUMMARY")
    print("=" * 60)
    print(f"  Dataset version   : {summary['dataset_version']}")
    print(f"  Total questions   : {summary['n_total']}")
    print(f"  ── Numeric ─────────────────────────────")
    print(f"  Tier-1 accuracy   : {summary['tier1_accuracy']}%")
    print(f"  Tier-2 accuracy   : {summary['tier2_accuracy']}%")
    print(f"  Tier-2 input fetch: {summary['tier2_input_fetch']}%  (agent got correct raw values)")
    print(f"  ── Retrieval ───────────────────────────")
    print(f"  Passage hit       : {summary['passage_hit_accuracy']}%  (key_phrase in chunks)")
    print(f"  Avg judge score   : {summary['avg_judge_score']} / 3")
    print(f"  Retrieval accuracy: {summary['retrieval_accuracy']}%  (hit ∩ judge≥2)")
    print(f"  ── Other ───────────────────────────────")
    print(f"  Refusal accuracy  : {summary['refusal_accuracy']}%")
    print(f"  Overall accuracy  : {summary['overall_accuracy']}%")
    print(f"  ── Cost & Latency ──────────────────────")
    print(f"  Avg latency       : {summary['avg_latency_s']}s / question")
    print(f"  Total latency     : {summary['total_latency_s']}s")
    print(f"  Tokens (in/out)   : {summary['input_tokens']} / {summary['output_tokens']}")
    print(f"  Estimated cost    : ${summary['estimated_cost_usd']}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
