"""
Round 2 of retrieval-recall experiments. Round 1 (deleted, see git history at
commit 5050165^) established: company-name query expansion is a validated
win (MRR 0.214->0.386); RRF pool-dilution punishes a chunk strong in only
one retriever; a generic cross-encoder reranker fixes exactly the dilution
case and nothing else; the two genuinely stubborn misses (QRVO customer
concentration, AAPL product categories) survive every fix tried so far.

This round targets those two stubborn misses plus general robustness, via
three literature-grounded techniques, each mapped to a specific diagnosed
root cause (see chat for the survey):
  - HyDE            -> question phrasing != disclosure phrasing (the QRVO
                        "concentration risk" vs "accounted for X% of total
                        revenue" mismatch, confirmed by hand this session)
  - multi-query      -> a more robust version of "add the company name":
                        union several query variants instead of betting on
                        one being right
  - small-to-big     -> RRF pool dilution from near-duplicate boilerplate
                        across years; simulated in-memory (no new DB table)
                        against the item's own target-year document

All arms call through `retrieve_text` (or the same bm25/dense functions it
uses) so year-scoping behaves exactly as production does -- round 1's arms
bypassed it entirely, which was a real, disclosed gap.

Usage:
    uv run --active python research/retrieval_improvements/run_experiment.py
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

FINAL_K = 5


def load_probes() -> list[dict]:
    path = ROOT / "data" / "datasets" / "eval_set.json"
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    probes = []
    for it in items:
        if it.get("type") != "retrieval" or it.get("retired"):
            continue
        cite = it["golden_citations"][0]
        probes.append({
            "id": it["id"], "question": it["question"], "ticker": it.get("ticker"),
            "fiscal_year": it.get("fiscal_year"), "phrase": cite["key_phrase"],
        })
    return probes


def golden_rank(results: list[dict], phrase: str) -> int | None:
    needle = " ".join(phrase.lower().split())
    for i, r in enumerate(results, start=1):
        if needle in " ".join((r.get("text") or "").lower().split()):
            return i
    return None


def score(arm_name: str, probes: list[dict], get_results) -> dict:
    ranks = []
    for p in probes:
        results = get_results(p)
        ranks.append(golden_rank(results, p["phrase"]))
    hit5 = sum(1 for r in ranks if r and r <= FINAL_K)
    found = [r for r in ranks if r]
    mrr = sum(1.0 / r for r in found) / len(probes) if probes else 0.0
    print(f"{arm_name:28} hit@5={hit5}/{len(probes)}  MRR={mrr:.3f}")
    return {"arm": arm_name, "hit5": hit5, "n": len(probes), "mrr": round(mrr, 4),
            "per_item": {p["id"]: r for p, r in zip(probes, ranks)}}


# ---- arm implementations -----------------------------------------------

def arm_baseline(p: dict) -> list[dict]:
    from copilot.agent.tools import retrieve_text
    out = retrieve_text(p["question"], ticker=p["ticker"], fiscal_year=p["fiscal_year"])
    return out.get("results", [])


def arm_name_expansion(p: dict) -> list[dict]:
    from copilot.agent.tools import retrieve_text
    from copilot.pipeline.companies import CLUSTER_RESEARCH
    name = CLUSTER_RESEARCH.get(p["ticker"], "")
    q = f"{name} {p['question']}".strip() if name else p["question"]
    out = retrieve_text(q, ticker=p["ticker"], fiscal_year=p["fiscal_year"])
    return out.get("results", [])


_hyde_cache: dict[str, str] = {}


def _hyde_passage(question: str, ticker: str) -> str:
    """Ask the model to write the sentence a 10-K would use to answer this,
    in filing register, not question register. Cached per (question,ticker)
    so repeated arms don't re-pay for it."""
    key = f"{ticker}:{question}"
    if key in _hyde_cache:
        return _hyde_cache[key]
    from openai import OpenAI
    from copilot.config import settings
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None, timeout=60)
    prompt = (
        f"Write one or two sentences in the flat, factual register of an SEC "
        f"10-K filing (not a question, not an explanation) that would plausibly "
        f"be the filing's own answer to: \"{question}\" "
        f"(company: {ticker}). Do not hedge, do not say 'the filing states' -- "
        f"write it as if it IS the filing text itself."
    )
    resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}])
    text = resp.choices[0].message.content or ""
    _hyde_cache[key] = text
    return text


def arm_hyde(p: dict) -> list[dict]:
    from copilot.agent.tools import retrieve_text
    passage = _hyde_passage(p["question"], p["ticker"])
    out = retrieve_text(passage, ticker=p["ticker"], fiscal_year=p["fiscal_year"])
    return out.get("results", [])


def arm_multiquery(p: dict) -> list[dict]:
    """Union of: name-expanded query, raw question, and the HyDE passage --
    each searched independently, results merged by chunk identity (dedup),
    ranked by best (lowest) rank any single query gave it. Robuster than
    betting everything on one query variant being the right one."""
    from copilot.agent.tools import retrieve_text
    from copilot.pipeline.companies import CLUSTER_RESEARCH
    name = CLUSTER_RESEARCH.get(p["ticker"], "")
    variants = [p["question"]]
    if name:
        variants.append(f"{name} {p['question']}".strip())
    variants.append(_hyde_passage(p["question"], p["ticker"]))

    best_rank: dict[str, int] = {}
    meta: dict[str, dict] = {}
    for q in variants:
        out = retrieve_text(q, ticker=p["ticker"], fiscal_year=p["fiscal_year"])
        for i, r in enumerate(out.get("results", []), start=1):
            cid = r.get("accn", "") + "|" + (r.get("text") or "")[:60]
            if cid not in best_rank or i < best_rank[cid]:
                best_rank[cid] = i
                meta[cid] = r
    ranked = sorted(best_rank.items(), key=lambda x: x[1])[:FINAL_K]
    return [meta[cid] for cid, _ in ranked]


ARMS = {
    "baseline (production retrieve_text)": arm_baseline,
    "+ name expansion":                     arm_name_expansion,
    "+ HyDE":                                arm_hyde,
    "+ multi-query (name+HyDE+raw union)":   arm_multiquery,
}


def run() -> None:
    probes = load_probes()
    print(f"{len(probes)} active retrieval probes.\n")
    summaries = []
    for name, fn in ARMS.items():
        summaries.append(score(name, probes, fn))
    out = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "summaries": summaries}
    out_path = RESULTS_DIR / f"run_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    run()
