"""
Eval harness: run the agent against eval_set.json and score results.

Metrics:
  - numeric_accuracy  : % of answerable questions within tolerance
  - refusal_accuracy  : % of unanswerable questions correctly refused
  - overall_accuracy  : combined
  - avg_latency_s     : mean wall-clock seconds per question
  - total_cost_usd    : estimated Anthropic API cost (input + output tokens)

Usage:
    python -m copilot.eval.harness
    python -m copilot.eval.harness --dataset data/eval_set.json --tier 1
    python -m copilot.eval.harness --out data/eval_results_latest.json
"""

import argparse
import json
import re
import time
from pathlib import Path

from openai import OpenAI

# gpt-4o-mini pricing
_INPUT_COST_PER_M  = 0.15   # USD per 1M input tokens
_OUTPUT_COST_PER_M = 0.60   # USD per 1M output tokens


def _extract_number(text: str) -> float | None:
    """
    Pull the first recognisable number out of an answer string.
    Handles: $391.0B, 46.2%, $93,736M, -0.72, 2.02%
    """
    text = text.replace(",", "").replace("$", "").replace("%", "")

    # Match optional sign, digits, optional decimal
    matches = re.findall(r"-?\d+\.?\d*", text)
    if not matches:
        return None

    # Pick the first numeric token that looks like a real value (not a year)
    for m in matches:
        val = float(m)
        # Skip things that look like years (1990-2030)
        if 1990 <= val <= 2030:
            continue
        return val
    return None


def _normalise(value: float, unit: str) -> float:
    """
    Normalise raw agent value to match expected unit.
    The agent may return billions (391.0) when expected is full USD (391035000000).
    Detect by magnitude and scale accordingly.
    """
    if unit != "USD":
        return value  # %, USD/share — no scaling needed

    # If expected is > 1e9 but agent returned < 1e6, try common suffixes
    # We don't know expected here, so just return raw and compare ratios in score().
    return value


def _within_tolerance(got: float, expected: float, tol_pct: float) -> bool:
    if expected == 0:
        return abs(got) < 1e-9
    # Allow the agent to return in different units of magnitude (e.g. billions vs full)
    ratio = got / expected
    # Accept if raw match within tolerance
    if abs(ratio - 1.0) * 100 <= tol_pct:
        return True
    # Accept if off by a known SI multiplier (B, M, K)
    for scale in (1e9, 1e6, 1e3):
        if abs((got * scale) / expected - 1.0) * 100 <= tol_pct:
            return True
        if abs((got / scale) / expected - 1.0) * 100 <= tol_pct:
            return True
    return False


def _is_refusal(text: str) -> bool:
    """Detect whether the agent correctly refused to answer."""
    refusal_phrases = [
        "cannot determine", "can't determine", "unable to determine",
        "not available", "no data", "not in", "cannot find",
        "don't have", "do not have", "outside", "not found",
        "unable to answer", "cannot answer",
    ]
    lower = text.lower()
    return any(p in lower for p in refusal_phrases)


# ── scoring ───────────────────────────────────────────────────────────────────

# ── retrieval scoring ─────────────────────────────────────────────────────────

def _check_citation(agent_citations: list[str], golden_citations: list[dict]) -> bool:
    """
    Return True if at least one golden citation is found in the agent's citation list.
    Matching is loose: both ticker and section must appear in the citation string.
    """
    for gc in golden_citations:
        ticker  = gc.get("ticker", "").upper()
        section = gc.get("section", "").lower()
        for cite in agent_citations:
            cite_lower = cite.lower()
            if ticker.lower() in cite_lower and section in cite_lower:
                return True
    return False


_judge_client: OpenAI | None = None

def _llm_judge(question: str, golden_answer: str, agent_answer: str) -> dict:
    """
    Use gpt-4o-mini to score agent_answer vs golden_answer.
    Returns {"score": 0-3, "reason": str}
    Score: 3=fully correct, 2=mostly correct, 1=partial, 0=wrong/fabricated
    """
    global _judge_client
    from copilot.config import settings
    if not settings.openai_api_key:
        return {"score": -1, "reason": "no API key"}
    if _judge_client is None:
        _judge_client = OpenAI(api_key=settings.openai_api_key)

    prompt = f"""You are evaluating a financial research assistant's answer to this question:
"{question}"

Reference answer (ground truth from the 10-K filing):
{golden_answer}

Generated answer:
{agent_answer}

Score the generated answer on a scale of 0-3:
3 = Fully correct — covers all key facts from the reference, no fabrications
2 = Mostly correct — captures the main point but has minor omissions or slight inaccuracies
1 = Partially correct — misses important facts or contains notable errors
0 = Incorrect or fabricated — contradicts the reference or invents information

Respond with ONLY valid JSON, no explanation outside the JSON:
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


def score_item(item: dict, agent_result: dict) -> dict:
    answer_text = agent_result.get("answer", "")

    if not item["answerable"]:
        correct = _is_refusal(answer_text)
        return {
            "id": item["id"],
            "tier": item["tier"],
            "type": item.get("type", "numeric"),
            "answerable": False,
            "correct": correct,
            "expected": None,
            "got_text": answer_text[:200],
            "refusal_detected": correct,
        }

    # Retrieval question — citation check + LLM-judge
    if item.get("type") == "retrieval":
        citation_ok = _check_citation(
            agent_result.get("citations", []),
            item.get("golden_citations", []),
        )
        judge = _llm_judge(
            item["question"],
            item.get("golden_answer", ""),
            answer_text,
        )
        judge_score = judge.get("score", -1)
        # "correct" = citation hit AND judge score >= 2
        correct = citation_ok and judge_score >= 2
        return {
            "id":            item["id"],
            "tier":          item["tier"],
            "type":          "retrieval",
            "answerable":    True,
            "correct":       correct,
            "citation_ok":   citation_ok,
            "judge_score":   judge_score,
            "judge_reason":  judge.get("reason", ""),
            "got_text":      answer_text[:300],
        }

    # Numeric question — extract number and compare
    got_raw = _extract_number(answer_text)
    correct = False
    if got_raw is not None:
        correct = _within_tolerance(got_raw, item["expected_value"], item["tolerance_pct"])

    return {
        "id": item["id"],
        "tier": item["tier"],
        "type": "numeric",
        "answerable": True,
        "correct": correct,
        "expected": item["expected_value"],
        "got_raw": got_raw,
        "got_text": answer_text[:200],
        "unit": item["expected_unit"],
    }


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
    total_latency = 0.0

    print(f"Running {len(items)} questions...\n")

    for idx, item in enumerate(items, 1):
        print(f"[{idx:02d}/{len(items)}] {item['id']}")
        print(f"       Q: {item['question']}")

        t0 = time.time()
        try:
            result = ask(item["question"])
        except Exception as e:
            result = {"answer": f"ERROR: {e}", "steps": [], "citations": []}
        elapsed = time.time() - t0
        total_latency += elapsed

        # Token tracking (agent returns usage if available)
        total_input_tokens  += result.get("usage", {}).get("input_tokens",  0)
        total_output_tokens += result.get("usage", {}).get("output_tokens", 0)

        scored = score_item(item, result)
        scored["latency_s"] = round(elapsed, 2)
        scored["steps"]     = result.get("steps", [])
        scored["citations"] = result.get("citations", [])
        results.append(scored)

        status = "✓" if scored["correct"] else "✗"
        if item["answerable"]:
            print(f"       {status} expected={item['expected_value']} got={scored.get('got_raw')} ({elapsed:.1f}s)")
        else:
            print(f"       {status} refusal={'yes' if scored.get('refusal_detected') else 'NO'} ({elapsed:.1f}s)")
        print()

    # ── aggregate metrics ─────────────────────────────────────────────────────

    answerable   = [r for r in results if r["answerable"]]
    unanswerable = [r for r in results if not r["answerable"]]
    numeric  = [r for r in results if r.get("type") != "retrieval" and r["answerable"]]
    retrieval = [r for r in results if r.get("type") == "retrieval"]
    tier1 = [r for r in numeric if r["tier"] == 1]
    tier2 = [r for r in numeric if r["tier"] == 2]

    def acc(lst):
        return round(sum(r["correct"] for r in lst) / len(lst) * 100, 1) if lst else None

    cost_usd = (
        total_input_tokens  / 1e6 * _INPUT_COST_PER_M +
        total_output_tokens / 1e6 * _OUTPUT_COST_PER_M
    )

    # retrieval sub-metrics
    citation_acc = (
        round(sum(r.get("citation_ok", False) for r in retrieval) / len(retrieval) * 100, 1)
        if retrieval else None
    )
    judge_scores = [r["judge_score"] for r in retrieval if r.get("judge_score", -1) >= 0]
    avg_judge = round(sum(judge_scores) / len(judge_scores), 2) if judge_scores else None

    summary = {
        "dataset":              str(dataset_path),
        "n_total":              len(results),
        "numeric_accuracy":     acc(numeric),
        "tier1_accuracy":       acc(tier1),
        "tier2_accuracy":       acc(tier2),
        "retrieval_accuracy":   acc(retrieval),
        "citation_accuracy":    citation_acc,
        "avg_judge_score":      avg_judge,
        "refusal_accuracy":     acc(unanswerable),
        "overall_accuracy":     acc(results),
        "avg_latency_s":        round(total_latency / len(results), 2) if results else 0,
        "total_latency_s":      round(total_latency, 1),
        "estimated_cost_usd":   round(cost_usd, 4),
        "results":              results,
    }

    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/eval_set.json")
    parser.add_argument("--tier", type=int, default=None, help="Filter to tier 1 or 2")
    parser.add_argument("--limit", type=int, default=None, help="Run only first N questions")
    parser.add_argument("--out", default=None, help="Save full results to JSON file")
    args = parser.parse_args()

    summary = run_eval(Path(args.dataset), tier_filter=args.tier, limit=args.limit)

    print("=" * 60)
    print("EVAL SUMMARY")
    print("=" * 60)
    print(f"  Total questions   : {summary['n_total']}")
    print(f"  Numeric accuracy  : {summary['numeric_accuracy']}%")
    print(f"    Tier-1          : {summary['tier1_accuracy']}%")
    print(f"    Tier-2          : {summary['tier2_accuracy']}%")
    print(f"  Retrieval accuracy: {summary['retrieval_accuracy']}%  (citation∩judge≥2)")
    print(f"    Citation hit    : {summary['citation_accuracy']}%")
    print(f"    Avg judge score : {summary['avg_judge_score']} / 3")
    print(f"  Refusal accuracy  : {summary['refusal_accuracy']}%")
    print(f"  Overall accuracy  : {summary['overall_accuracy']}%")
    print(f"  Avg latency       : {summary['avg_latency_s']}s/question")
    print(f"  Estimated cost    : ${summary['estimated_cost_usd']}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
