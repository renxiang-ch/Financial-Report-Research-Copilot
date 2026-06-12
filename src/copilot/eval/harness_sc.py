"""
SC-DisclosureQA research harness.

Runs the 125-question SC benchmark under three ablation conditions to measure
error-layer separability:

  Condition A — Naive RAG        : retrieve_text only (no SQL, no graph)
  Condition B — SQL-locked       : query_financials + compute + retrieve_text (no graph)
  Condition C — Full system      : all tools (default)

Scoring per type:
  T1  : numeric ±2%  (query_financials expected)
  T2  : numeric ±0.5% + input_values check
  T4/T5 graph_fact    : numeric ±2% + traversal_ground_truth check
  T4   graph_lookup   : set_match — all expected supplier tickers present
  T4/T5 graph_trend   : per-year value check ±2%
  T4/T5 graph_comparison : LLM judge ≥ 2
  T4   graph_negative : keyword refusal (no named customer / does not disclose)
  T5   graph_compute  : numeric ±2% + graph_query + query_financials called
  T6   : refusal keyword detection

Usage:
  python -m copilot.eval.harness_sc                                    # Condition C
  python -m copilot.eval.harness_sc --condition B --out data/sc_B.json
  python -m copilot.eval.harness_sc --condition A --out data/sc_A.json
  python -m copilot.eval.harness_sc --types T1 T2 --limit 10           # quick smoke test
"""

import argparse
import json
import re
import time
from pathlib import Path

from openai import OpenAI

from copilot.eval.harness import (
    _INPUT_COST_PER_M,
    _OUTPUT_COST_PER_M,
    _extract_number,
    _is_refusal,
    _within_tolerance,
    _check_input_values,
)

# ── Tool-name constants ───────────────────────────────────────────────────────

_TOOL_QUERY   = "query_financials"
_TOOL_COMPUTE = "compute"
_TOOL_METRICS = "list_metrics"
_TOOL_RETRIEVE = "retrieve_text"
_TOOL_GRAPH   = "graph_query"

_SQL_TOOLS    = {_TOOL_QUERY, _TOOL_COMPUTE, _TOOL_METRICS}
_ALL_TOOLS    = _SQL_TOOLS | {_TOOL_RETRIEVE, _TOOL_GRAPH}


# ── Ablation: tool stripping ──────────────────────────────────────────────────

def _set_condition(condition: str) -> list:
    """
    Strip tools from TOOL_SCHEMAS according to ablation condition.
    Returns the original schemas list so it can be restored.

    A — Naive RAG:   keep only retrieve_text
    B — SQL-locked:  remove graph_query only
    C — Full system: no changes
    """
    import copilot.agent.agent as _agent
    original = _agent.TOOL_SCHEMAS[:]

    if condition == "A":
        _agent.TOOL_SCHEMAS = [
            s for s in _agent.TOOL_SCHEMAS
            if s["function"]["name"] == _TOOL_RETRIEVE
        ]
        print(f">> CONDITION A: only {_TOOL_RETRIEVE} enabled\n")
    elif condition == "B":
        _agent.TOOL_SCHEMAS = [
            s for s in _agent.TOOL_SCHEMAS
            if s["function"]["name"] != _TOOL_GRAPH
        ]
        print(f">> CONDITION B: {_TOOL_GRAPH} disabled\n")
    else:
        print(">> CONDITION C: all tools enabled\n")

    return original


def _restore_tools(original: list) -> None:
    import copilot.agent.agent as _agent
    _agent.TOOL_SCHEMAS = original


# ── Step helpers ──────────────────────────────────────────────────────────────

def _tool_called(steps: list[dict], name: str) -> bool:
    return any(s.get("tool") == name for s in steps)


def _collect_graph_edges(steps: list[dict]) -> list[dict]:
    edges = []
    for step in steps:
        if step.get("tool") == _TOOL_GRAPH:
            for e in step.get("output", {}).get("edges", []):
                edges.append(e)
    return edges


def _last_compute_result(steps: list[dict]) -> float | None:
    for step in reversed(steps):
        if step.get("tool") == _TOOL_COMPUTE:
            raw = step.get("output", {}).get("result")
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
    return None


# ── Traversal ground truth check ──────────────────────────────────────────────

def _check_traversal(steps: list[dict], expected: list[dict]) -> tuple[bool, list]:
    """
    Verify graph_query returned all edges listed in traversal_ground_truth.
    Returns (all_found, missing_list).
    Empty expected list → trivially passes (used by graph_negative).
    """
    if not expected:
        return True, []
    returned = _collect_graph_edges(steps)
    missing = []
    for exp in expected:
        found = any(
            e.get("supplier") == exp.get("supplier")
            and e.get("customer") == exp.get("customer")
            and e.get("fiscal_year") == exp.get("fiscal_year")
            and abs((e.get("revenue_pct") or 0) - (exp.get("revenue_pct") or 0)) <= 1.0
            for e in returned
        )
        if not found:
            missing.append(exp)
    return len(missing) == 0, missing


# ── LLM judge ─────────────────────────────────────────────────────────────────

_judge_client: OpenAI | None = None


def _llm_judge(question: str, expected_value, agent_answer: str, sc_subtype: str) -> dict:
    global _judge_client
    from copilot.config import settings
    if not settings.openai_api_key:
        return {"score": -1, "reason": "no API key"}
    if _judge_client is None:
        _judge_client = OpenAI(api_key=settings.openai_api_key)

    if sc_subtype == "graph_comparison" and isinstance(expected_value, str):
        criteria = f"The correct answer is {expected_value}. Agent should identify this company as having the highest/correct value."
    elif isinstance(expected_value, dict):
        lines = []
        for k, v in expected_value.items():
            if isinstance(v, (int, float)) and abs(v) > 1e6:
                lines.append(f"  {k}: ${v/1e9:.2f}B")
            else:
                lines.append(f"  {k}: {v}")
        criteria = "Expected values:\n" + "\n".join(lines)
    else:
        criteria = f"Expected: {expected_value}"

    prompt = f"""You are evaluating a financial research assistant's answer.

Question: "{question}"

Correct answer criteria:
{criteria}

Agent's answer:
{agent_answer}

Score 0–3:
3 = Fully correct — right companies/ranking, correct values cited, no fabrications
2 = Mostly correct — right conclusion, minor omission (missing one citation or caveat)
1 = Partially correct — right direction but wrong company or significant omission
0 = Incorrect — wrong answer, fabricated numbers, or refused when should answer

Respond ONLY with valid JSON: {{"score": <0|1|2|3>, "reason": "<one sentence>"}}"""

    resp = _judge_client.chat.completions.create(
        model="gpt-4o-mini", max_tokens=128,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return json.loads(resp.choices[0].message.content.strip())
    except Exception:
        return {"score": -1, "reason": "parse error"}


# ── Trend value checker ───────────────────────────────────────────────────────

_NEGATIVE_REFUSAL_PHRASES = [
    "no named", "does not disclose", "did not disclose", "no single customer",
    "no customer", "cannot determine", "not available", "no disclosure",
    "does not name", "did not name", "no >10%", "no 10%",
]


def _is_negative_correct(answer_text: str) -> bool:
    low = answer_text.lower()
    return any(phrase in low for phrase in _NEGATIVE_REFUSAL_PHRASES)


def _check_trend_values(answer_text: str, steps: list[dict],
                        expected: dict, tolerance_pct: float) -> tuple[bool, dict]:
    """
    For graph_trend: verify each year's value appears (in answer text or graph edges).
    Returns (all_correct, {year: got_value}).
    """
    returned_edges = _collect_graph_edges(steps)
    got: dict = {}

    for year, exp_val in expected.items():
        year_int = int(year)
        # Try matching from graph edges first
        for e in returned_edges:
            fy = e.get("fiscal_year")
            pct = e.get("revenue_pct")
            if fy == year_int and pct is not None:
                got[year] = pct
                break
        else:
            # Fallback: scan answer text for the year then extract nearby number
            # pattern: "2022" followed by number within 60 chars
            pattern = rf'\b{year}\b.{{0,60}}?(\d+(?:\.\d+)?)'
            m = re.search(pattern, answer_text)
            if m:
                try:
                    got[year] = float(m.group(1))
                except ValueError:
                    pass

    all_correct = all(
        year in got and _within_tolerance(got[year], expected[year], tolerance_pct)
        for year in expected
    )
    return all_correct, got


# ── Tool trace builder ────────────────────────────────────────────────────────

def _build_tool_trace(steps: list[dict]) -> list[str]:
    trace = []
    for step in steps:
        tool = step.get("tool", "?")
        inp  = step.get("input", {})
        out  = step.get("output", {})

        if tool == _TOOL_GRAPH:
            parts = []
            if inp.get("supplier"): parts.append(f"supplier={inp['supplier']}")
            if inp.get("customer"): parts.append(f"customer={inp['customer']}")
            parts.append(f"fiscal_year={inp.get('fiscal_year', '?')}")
            n = out.get("edge_count", 0)
            status = f"{n} edges" if out.get("found") else "NOT FOUND"
            trace.append(f"graph_query({', '.join(parts)}) -> {status}")
            for t in out.get("traversal_trace", []):
                trace.append(f"  edge: {t}")

        elif tool == _TOOL_QUERY:
            ticker = inp.get("ticker", "?")
            metric = inp.get("metric", "?")
            fy     = inp.get("fiscal_year", "?")
            if out.get("found"):
                trace.append(f"query_financials({ticker}, {metric}, FY{fy}) -> {out.get('value')}")
            else:
                trace.append(f"query_financials({ticker}, {metric}, FY{fy}) -> NOT FOUND")

        elif tool == _TOOL_COMPUTE:
            trace.append(f"compute({inp.get('expression', '?')}) -> {out.get('result')}")

        elif tool == _TOOL_RETRIEVE:
            n = len(out.get("results", []))
            trace.append(f"retrieve_text('{inp.get('query', '?')[:60]}') -> {n} chunks")

        else:
            trace.append(f"{tool}({str(inp)[:80]}) -> {str(out)[:80]}")

    return trace


# ── Per-question scorer ───────────────────────────────────────────────────────

def score_item(item: dict, agent_result: dict) -> dict:
    answer = agent_result.get("answer", "")
    steps  = agent_result.get("steps", [])

    sc_type    = item["sc_type"]
    sc_subtype = item.get("sc_subtype", "")
    base = {"id": item["id"], "sc_type": sc_type, "sc_subtype": sc_subtype,
            "answerable": item.get("answerable", True)}

    tol = item.get("tolerance_pct", 2.0) or 2.0

    # ── T6: unanswerable / refusal ────────────────────────────────────────────
    if sc_type == "T6" or not item.get("answerable", True):
        correct = _is_refusal(answer)
        return {**base, "correct": correct, "refusal_detected": correct,
                "got_text": answer[:200]}

    traversal_gt = item.get("traversal_ground_truth", [])

    # ── T1: direct SQL numeric lookup ─────────────────────────────────────────
    if sc_type == "T1":
        got = _extract_number(answer)
        exp = item["expected_value"]
        correct = got is not None and _within_tolerance(got, exp, tol)
        return {
            **base,
            "correct":  correct,
            "expected": exp,
            "got_raw":  got,
            "sql_called": _tool_called(steps, _TOOL_QUERY),
            "got_text": answer[:200],
        }

    # ── T2: SQL + compute (ratio / YoY) ──────────────────────────────────────
    if sc_type == "T2":
        got = _last_compute_result(steps)
        if got is None:
            got = _extract_number(answer)
        exp = item["expected_value"]
        correct = got is not None and _within_tolerance(got, exp, tol)
        input_ok, input_detail = _check_input_values(
            steps, item.get("input_values", {}), tol
        )
        return {
            **base,
            "correct":       correct,
            "expected":      exp,
            "got_raw":       got,
            "input_values_ok": input_ok,
            "input_detail":  input_detail,
            "sql_called":    _tool_called(steps, _TOOL_QUERY),
            "compute_called": _tool_called(steps, _TOOL_COMPUTE),
            "got_text":      answer[:200],
        }

    scoring  = item.get("scoring", "numeric")
    graph_ok = _tool_called(steps, _TOOL_GRAPH)
    fin_ok   = _tool_called(steps, _TOOL_QUERY)

    # ── T4 graph_fact ─────────────────────────────────────────────────────────
    if sc_subtype == "graph_fact":
        got = _extract_number(answer)
        exp = item["expected_value"]
        traversal_ok, missing = _check_traversal(steps, traversal_gt)
        correct = (got is not None
                   and _within_tolerance(got, exp, tol)
                   and traversal_ok
                   and graph_ok)
        return {
            **base, "correct": correct,
            "expected": exp, "got_raw": got,
            "graph_called": graph_ok,
            "traversal_ok": traversal_ok, "missing_edges": missing,
            "got_text": answer[:200],
        }

    # ── T4 graph_lookup ───────────────────────────────────────────────────────
    if sc_subtype == "graph_lookup":
        expected_list = item["expected_value"]   # list of ticker strings
        returned_edges = _collect_graph_edges(steps)
        returned_suppliers = {e.get("supplier") for e in returned_edges}
        # also scan answer text for ticker mentions
        answer_upper = answer.upper()
        found = [t for t in expected_list
                 if t in returned_suppliers or t in answer_upper]
        all_found = set(found) == set(expected_list)
        correct = all_found and graph_ok
        return {
            **base, "correct": correct,
            "expected": expected_list,
            "found_in_response": found,
            "missing": list(set(expected_list) - set(found)),
            "graph_called": graph_ok,
            "got_text": answer[:300],
        }

    # ── T4 graph_trend / T5 graph_trend ──────────────────────────────────────
    if sc_subtype == "graph_trend":
        expected_dict = item["expected_value"]   # {year: value}
        traversal_ok, missing = _check_traversal(steps, traversal_gt)
        all_correct, got_map = _check_trend_values(answer, steps, expected_dict, tol)
        correct = all_correct and graph_ok and traversal_ok
        return {
            **base, "correct": correct,
            "expected": expected_dict, "got_map": got_map,
            "graph_called": graph_ok,
            "traversal_ok": traversal_ok, "missing_edges": missing,
            "got_text": answer[:300],
        }

    # ── T4 graph_comparison / T5 graph_comparison ────────────────────────────
    if sc_subtype == "graph_comparison":
        traversal_ok, missing = _check_traversal(steps, traversal_gt)
        judge = _llm_judge(item["question"], item["expected_value"], answer, sc_subtype)
        judge_score = judge.get("score", -1)
        correct = judge_score >= 2 and graph_ok and traversal_ok
        return {
            **base, "correct": correct,
            "graph_called": graph_ok,
            "traversal_ok": traversal_ok, "missing_edges": missing,
            "judge_score": judge_score, "judge_reason": judge.get("reason", ""),
            "got_text": answer[:300],
        }

    # ── T4 graph_negative ─────────────────────────────────────────────────────
    if sc_subtype == "graph_negative":
        # Correct = agent says "no disclosure" / "does not disclose"
        # graph_query may or may not be called (agent might check and find nothing)
        refusal_ok = _is_negative_correct(answer) or _is_refusal(answer)
        correct = refusal_ok
        return {
            **base, "correct": correct,
            "refusal_ok": refusal_ok,
            "graph_called": graph_ok,
            "got_text": answer[:200],
        }

    # ── T5 graph_compute (numeric) ────────────────────────────────────────────
    if sc_subtype == "graph_compute":
        traversal_ok, missing = _check_traversal(steps, traversal_gt)
        got = _last_compute_result(steps) or _extract_number(answer)
        exp = item["expected_value"]
        correct = (got is not None
                   and _within_tolerance(got, exp, tol)
                   and graph_ok and fin_ok
                   and traversal_ok)
        return {
            **base, "correct": correct,
            "expected": exp, "got_raw": got,
            "graph_called": graph_ok, "financials_called": fin_ok,
            "traversal_ok": traversal_ok, "missing_edges": missing,
            "got_text": answer[:200],
        }

    # fallback
    return {**base, "correct": False, "error": f"unhandled sc_subtype={sc_subtype!r}",
            "got_text": answer[:200]}


# ── Aggregate stats ───────────────────────────────────────────────────────────

def _acc(lst: list) -> float | None:
    if not lst:
        return None
    return round(sum(r["correct"] for r in lst) / len(lst) * 100, 1)


def _build_summary(results: list[dict], condition: str, dataset_meta: dict,
                   total_input: int, total_output: int,
                   total_latency: float) -> dict:
    def by_sub(sub):
        return [r for r in results if r.get("sc_subtype") == sub]
    def by_type(t):
        return [r for r in results if r.get("sc_type") == t]

    cost = (total_input / 1e6 * _INPUT_COST_PER_M
            + total_output / 1e6 * _OUTPUT_COST_PER_M)

    judge_scores = [r["judge_score"] for r in results
                    if r.get("judge_score", -1) >= 0]

    return {
        "dataset":          dataset_meta.get("built_at", ""),
        "dataset_version":  dataset_meta.get("version"),
        "condition":        condition,
        "n_total":          len(results),
        # ── per type ──────────────────────────────────────────────────────────
        "T1_accuracy":          _acc(by_type("T1")),
        "T2_accuracy":          _acc(by_type("T2")),
        "T4_accuracy":          _acc(by_type("T4")),
        "T5_accuracy":          _acc(by_type("T5")),
        "T6_accuracy":          _acc(by_type("T6")),
        # ── per subtype ───────────────────────────────────────────────────────
        "graph_fact_accuracy":       _acc(by_sub("graph_fact")),
        "graph_lookup_accuracy":     _acc(by_sub("graph_lookup")),
        "graph_trend_accuracy":      _acc(by_sub("graph_trend")),
        "graph_comparison_accuracy": _acc(by_sub("graph_comparison")),
        "graph_negative_accuracy":   _acc(by_sub("graph_negative")),
        "graph_compute_accuracy":    _acc(by_sub("graph_compute")),
        # ── overall ───────────────────────────────────────────────────────────
        "overall_accuracy":     _acc(results),
        "avg_judge_score":      round(sum(judge_scores) / len(judge_scores), 2) if judge_scores else None,
        # ── cost / latency ────────────────────────────────────────────────────
        "avg_latency_s":        round(total_latency / len(results), 2) if results else 0,
        "total_latency_s":      round(total_latency, 1),
        "input_tokens":         total_input,
        "output_tokens":        total_output,
        "estimated_cost_usd":   round(cost, 4),
        # ── per-question detail ───────────────────────────────────────────────
        "results":              results,
    }


# ── Main eval loop ────────────────────────────────────────────────────────────

def run_eval(dataset_path: Path, condition: str = "C",
             types_filter: list[str] | None = None,
             limit: int | None = None) -> dict:
    from copilot.agent.agent import ask

    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)

    items = data["items"]
    if types_filter:
        items = [q for q in items if q["sc_type"] in types_filter]
    if limit:
        items = items[:limit]

    original = _set_condition(condition)

    results       = []
    total_input   = 0
    total_output  = 0
    total_latency = 0.0

    print(f"SC-DisclosureQA  condition={condition}  n={len(items)}\n")

    try:
        for idx, item in enumerate(items, 1):
            sc_type = item["sc_type"]
            sc_sub  = item.get("sc_subtype", "")
            label   = f"[{sc_type}/{sc_sub}]" if sc_sub else f"[{sc_type}]"
            print(f"[{idx:03d}/{len(items)}] {label} {item['id']}")
            print(f"         Q: {item['question'][:100]}")

            t0 = time.time()
            try:
                result = ask(item["question"])
            except Exception as e:
                result = {"answer": f"ERROR: {e}", "steps": [], "citations": [], "usage": {}}
            elapsed = time.time() - t0
            total_latency += elapsed

            total_input  += result.get("usage", {}).get("input_tokens",  0)
            total_output += result.get("usage", {}).get("output_tokens", 0)

            scored = score_item(item, result)
            scored["latency_s"]  = round(elapsed, 2)
            scored["tool_trace"] = _build_tool_trace(result.get("steps", []))
            results.append(scored)

            status = "PASS" if scored["correct"] else "FAIL"

            # one-line result print
            if sc_type == "T6" or not item.get("answerable", True):
                print(f"         {status}  refusal={'yes' if scored.get('refusal_detected') else 'NO'}  ({elapsed:.1f}s)")
            elif sc_type in ("T1", "T2") or sc_sub in ("graph_fact", "graph_compute"):
                exp = item.get("expected_value", "?")
                got = scored.get("got_raw", "?")
                if isinstance(exp, (int, float)) and abs(exp) > 1e6:
                    exp_s = f"${exp/1e9:.3f}B"
                    got_s = f"${got/1e9:.3f}B" if isinstance(got, (int, float)) else str(got)
                else:
                    exp_s, got_s = str(exp), str(got)
                extra = ""
                if sc_sub == "graph_compute":
                    extra = f"  graph={scored.get('graph_called')}  fin={scored.get('financials_called')}"
                print(f"         {status}  expected={exp_s}  got={got_s}{extra}  ({elapsed:.1f}s)")
            elif sc_sub in ("graph_lookup", "graph_trend"):
                miss = scored.get("missing_edges") or scored.get("missing", [])
                miss_s = f"  missing={miss}" if miss else ""
                print(f"         {status}  graph={scored.get('graph_called')}  traversal={'ok' if scored.get('traversal_ok') else 'MISS'}{miss_s}  ({elapsed:.1f}s)")
            elif sc_sub == "graph_comparison":
                print(f"         {status}  graph={scored.get('graph_called')}  judge={scored.get('judge_score')}/3  ({elapsed:.1f}s)")
            elif sc_sub == "graph_negative":
                print(f"         {status}  refusal_ok={scored.get('refusal_ok')}  graph={scored.get('graph_called')}  ({elapsed:.1f}s)")
            else:
                print(f"         {status}  ({elapsed:.1f}s)")

            for line in scored["tool_trace"]:
                print(f"           > {line}")
            print()

    finally:
        _restore_tools(original)

    return _build_summary(results, condition, data, total_input, total_output, total_latency)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SC-DisclosureQA research harness")
    parser.add_argument("--dataset",   default="data/eval_set_sc.json")
    parser.add_argument("--condition", default="C", choices=["A", "B", "C"],
                        help="A=Naive RAG  B=SQL-locked  C=Full system (default)")
    parser.add_argument("--types",     nargs="+", default=None,
                        help="Filter by sc_type, e.g. --types T1 T4")
    parser.add_argument("--limit",     type=int, default=None)
    parser.add_argument("--out",       default=None, help="Save results JSON")
    args = parser.parse_args()

    summary = run_eval(
        Path(args.dataset),
        condition=args.condition,
        types_filter=args.types,
        limit=args.limit,
    )

    w = 56
    print("=" * w)
    print(f"  SC-DisclosureQA  CONDITION {summary['condition']}")
    print("=" * w)
    print(f"  Total questions      : {summary['n_total']}")
    print(f"  {'─'*40}")
    print(f"  T1 SQL numeric       : {summary['T1_accuracy']}%")
    print(f"  T2 SQL+compute       : {summary['T2_accuracy']}%")
    print(f"  T4 graph relation    : {summary['T4_accuracy']}%")
    print(f"    graph_fact         :   {summary['graph_fact_accuracy']}%")
    print(f"    graph_lookup       :   {summary['graph_lookup_accuracy']}%")
    print(f"    graph_trend        :   {summary['graph_trend_accuracy']}%")
    print(f"    graph_comparison   :   {summary['graph_comparison_accuracy']}%")
    print(f"    graph_negative     :   {summary['graph_negative_accuracy']}%")
    print(f"  T5 cross-entity      : {summary['T5_accuracy']}%")
    print(f"    graph_compute      :   {summary['graph_compute_accuracy']}%")
    print(f"  T6 unanswerable      : {summary['T6_accuracy']}%")
    print(f"  {'─'*40}")
    print(f"  Overall accuracy     : {summary['overall_accuracy']}%")
    if summary['avg_judge_score'] is not None:
        print(f"  Avg judge score      : {summary['avg_judge_score']}/3")
    print(f"  {'─'*40}")
    print(f"  Avg latency          : {summary['avg_latency_s']}s / question")
    print(f"  Estimated cost       : ${summary['estimated_cost_usd']}")
    print("=" * w)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
