"""
Real-agent validation: does pinning `temperature` (and `seed`) reduce the
query-wording noise this session traced `retrieve_text`'s hit/miss verdicts
to?

This is the deferred step PROTOCOL.md's constraint 5 always pointed at: the
deterministic probes in run_experiment.py isolate the retrieval *layer*
from the agent's own query-generation noise on purpose, so they can never
answer "does fixing the agent's sampling help" -- only a real agent call
can generate the query string the way production actually does. This
script is that real-agent call, kept research-local: production
`agent.py` is not modified. `openai.resources.chat.completions.completions
.Completions.create` is monkeypatched for the duration of a pinned-arm run
to inject `temperature=0, seed=42` whenever the caller (agent.py) didn't
already set them -- agent.py's own call site is untouched.

"v3" cannot be literally re-run: this is a single-commit teaching-repo
snapshot, not the multi-commit history v3 was measured against, and the
system prompt / tools / routing here are far more evolved than v3's era
(slots, clarification, grounding verification, multi-turn -- none of
which existed at v3). What IS reproducible is a clean, single-variable
test: run the SAME current code twice, once with default (unpinned)
sampling and once with temperature+seed pinned, and see whether pinning
measurably reduces the run-to-run flip rate this session already proved
is real (see PROTOCOL.md and the ret_avgo_vmware_acquisition /
ret_glw_business_segments v2-vs-v3 trace comparison). v3's own historical
number is reported alongside purely as a reference point, not a target
this run can actually reproduce.

Usage:
    uv run --active python research/retrieval_recall_experiments/agent_temperature_validation.py
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

REPEATS = 5  # matches this project's established precedent for noisy-metric
             # validation (P3 multi-turn used 5 runs/arm) -- one run cannot
             # separate an effect from noise, this session's own thesis.


def load_retrieval_items() -> list[dict]:
    path = ROOT / "data" / "datasets" / "eval_set.json"
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    return [it for it in items if it.get("type") == "retrieval" and not it.get("retired")]


class pin_sampling:
    """Context manager: while active, every OpenAI chat.completions.create
    call gets temperature=0, seed=42 unless the caller already set them.
    Patches the class method (agent.py builds a fresh OpenAI() client per
    call, so there's no single instance to patch) and restores it on exit
    even if the run raises."""

    def __enter__(self):
        from openai.resources.chat.completions.completions import Completions
        self._cls = Completions
        self._original = Completions.create

        original = self._original

        def patched(self_, *args, **kwargs):
            kwargs.setdefault("temperature", 0)
            kwargs.setdefault("seed", 42)
            return original(self_, *args, **kwargs)

        Completions.create = patched
        return self

    def __exit__(self, *exc):
        self._cls.create = self._original
        return False


def run_arm(items: list[dict], pinned: bool, repeat_idx: int) -> dict:
    from copilot.agent.agent import ask
    from copilot.eval.harness import _build_tool_trace, _rates_for, score_item

    (rate_in, rate_out), cost_basis = _rates_for(None)  # default model, same as production

    rows = []
    total_in = total_out = 0
    t_start = time.time()

    ctx = pin_sampling() if pinned else _NullCtx()
    with ctx:
        for item in items:
            t0 = time.time()
            try:
                result = ask(item["question"])
            except Exception as e:
                result = {"answer": f"ERROR: {e}", "steps": [], "citations": [], "usage": {}}
            elapsed = time.time() - t0
            scored = score_item(item, result)
            scored["latency_s"] = round(elapsed, 2)
            scored["tool_trace"] = _build_tool_trace(result.get("steps", []))
            u = result.get("usage", {})
            total_in += u.get("input_tokens", 0)
            total_out += u.get("output_tokens", 0)
            rows.append(scored)
            print(f"  [{'pinned' if pinned else 'unpinned'} run {repeat_idx}] "
                  f"{item['id']:42} {'HIT ' if scored.get('passage_hit') else 'MISS'} "
                  f"correct={scored['correct']} ({elapsed:.1f}s)")

    cost = round(total_in / 1_000_000 * rate_in + total_out / 1_000_000 * rate_out, 5)
    n_correct = sum(1 for r in rows if r["correct"])
    return {
        "pinned": pinned, "repeat_idx": repeat_idx,
        "n": len(rows), "n_correct": n_correct,
        "hit_rate": round(100 * n_correct / len(rows), 1),
        "cost_usd": cost, "wall_s": round(time.time() - t_start, 1),
        "rows": [{"id": r["id"], "correct": r["correct"], "passage_hit": r.get("passage_hit"),
                   "judge_score": r.get("judge_score")} for r in rows],
    }


class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def run() -> dict:
    items = load_retrieval_items()
    print(f"{len(items)} active retrieval items, {REPEATS} repeats x 2 arms "
          f"= {len(items) * REPEATS * 2} real agent calls.\n")

    runs = []
    for pinned in (False, True):
        for r in range(1, REPEATS + 1):
            runs.append(run_arm(items, pinned, r))

    def summarize(pinned: bool) -> dict:
        arm_runs = [r for r in runs if r["pinned"] == pinned]
        hit_rates = [r["hit_rate"] for r in arm_runs]
        # per-item stability: how many of the REPEATS runs scored this item correct
        per_item = {}
        for it in items:
            flips = [next(row["correct"] for row in r["rows"] if row["id"] == it["id"])
                     for r in arm_runs]
            per_item[it["id"]] = {"correct_count": sum(flips), "of": len(flips),
                                    "stable": len(set(flips)) == 1}
        return {
            "mean_hit_rate": round(sum(hit_rates) / len(hit_rates), 1),
            "min_hit_rate": min(hit_rates), "max_hit_rate": max(hit_rates),
            "total_cost_usd": round(sum(r["cost_usd"] for r in arm_runs), 5),
            "per_item_stability": per_item,
            "n_unstable_items": sum(1 for v in per_item.values() if not v["stable"]),
        }

    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repeats_per_arm": REPEATS,
        "items": [it["id"] for it in items],
        "unpinned": summarize(False),
        "pinned": summarize(True),
        "runs": runs,
    }

    print("\n" + "=" * 70)
    for label, key in (("Unpinned (production default)", "unpinned"), ("Pinned (temp=0, seed=42)", "pinned")):
        s = out[key]
        print(f"{label:32} mean={s['mean_hit_rate']}%  range=[{s['min_hit_rate']}-{s['max_hit_rate']}]%  "
              f"unstable_items={s['n_unstable_items']}/{len(items)}  cost=${s['total_cost_usd']}")
    print("=" * 70)

    out_path = RESULTS_DIR / f"temperature_validation_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")
    return out


if __name__ == "__main__":
    run()
