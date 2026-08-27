"""
Deterministic A/B harness for retrieval-layer recall improvements.

No LLM, no agent, no cost, no run-to-run variance -- see PROTOCOL.md for why
that is a deliberate design choice, not a shortcut. Every arm is scored by
handing the eval item's own fixed question text directly to a candidate
retrieval function; the only thing that varies between arms is the retrieval
code path, never the query-generation step (that's the agent's job, and this
session already proved it's noisy -- see PROTOCOL.md).

Extends eval/probe_retrieval.py's pattern (golden_rank / hit@k / MRR, no
LLM) rather than re-deriving a scoring method; production code
(retrieval/bm25.py, retrieval/dense.py, retrieval/hybrid.py) is imported and
called, never copy-pasted, so an arm that claims to be "current production
behavior" actually is.

Usage:
    uv run --active python research/retrieval_recall_experiments/run_experiment.py
"""

import json
import math
import time
from collections import Counter
from pathlib import Path

from copilot.pipeline.companies import CLUSTER_RESEARCH
from copilot.retrieval.bm25 import BM25Okapi, _get_index
from copilot.retrieval.dense import embed_query
from copilot.storage.db import get_conn

_CROSS_ENCODER = None  # lazy singleton -- one model load for the whole run, not per-question


def _cross_encoder():
    global _CROSS_ENCODER
    if _CROSS_ENCODER is None:
        from sentence_transformers import CrossEncoder
        # ms-marco-MiniLM-L-6-v2: standard, small (~80MB), local, deterministic --
        # no LLM call, same "zero-cost, zero-variance" bar as every other arm here.
        _CROSS_ENCODER = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _CROSS_ENCODER

ROOT = Path(__file__).resolve().parents[2]  # repo root
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

KS = (1, 3, 5, 10)
FINAL_K = 5          # what the agent actually receives -- headline hit@FINAL_K
PROD_RRF_K = 60      # retrieval/hybrid.py's constant, reproduced for the baseline arm


# ── shared scoring (same method as eval/probe_retrieval.py) ─────────────────

def golden_rank(results: list[dict], phrase: str) -> int | None:
    """1-based rank of the first result containing `phrase`, or None."""
    needle = " ".join(phrase.lower().split())
    for i, r in enumerate(results, start=1):
        if needle in " ".join((r.get("text") or "").lower().split()):
            return i
    return None


def load_probes() -> list[tuple[str, str, str, str]]:
    """(id, question, ticker, golden_key_phrase) for every non-retired retrieval item."""
    path = ROOT / "data" / "datasets" / "eval_set.json"
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    probes = []
    for it in items:
        if it.get("type") != "retrieval" or it.get("retired"):
            continue
        for cite in it.get("golden_citations", []):
            if cite.get("key_phrase"):
                probes.append((it["id"], it["question"], it.get("ticker"), cite["key_phrase"]))
                break
    return probes


# ── RRF, parameterized (production hybrid.py hardcodes RRF_K=60 and pool=k*2;
#    this reimplements the same formula with knobs exposed, so arms can vary
#    them without editing production code) ──────────────────────────────────

def rrf_merge(bm25_results: list[dict], dense_results: list[dict], k: int, rrf_k: int) -> list[dict]:
    scores: dict[int, float] = {}
    meta: dict[int, dict] = {}
    for rank, r in enumerate(bm25_results, start=1):
        cid = r["id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
        meta[cid] = r
    for rank, r in enumerate(dense_results, start=1):
        cid = r["id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
        meta.setdefault(cid, r)
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:k]
    return [meta[cid] for cid, _ in ranked]


# ── dense retrieval helper: same query text, optional expansion ─────────────

def dense_search(query: str, ticker: str | None, k: int) -> list[dict]:
    query_vec = embed_query(query)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            where = ["embedding IS NOT NULL", "COALESCE(chunk_type,'text') <> 'table'"]
            if ticker:
                where.append("ticker = %s")
            # Params must be positional-order-matched to the placeholders as they
            # appear in the SQL text: SELECT's %s comes first, then WHERE's
            # (if any), then ORDER BY's, then LIMIT's -- not the order they're
            # conceptually "about." Building this list in append order rather
            # than matching placeholder order was the first version's exact bug.
            params: list = [query_vec]
            if ticker:
                params.append(ticker.upper())
            params += [query_vec, k]
            cur.execute(
                f"""SELECT id, ticker, accn, section, text,
                           1 - (embedding <=> %s::vector) AS score
                    FROM text_chunks WHERE {' AND '.join(where)}
                    ORDER BY embedding <=> %s::vector LIMIT %s""",
                tuple(params),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def bm25_search(query: str, ticker: str | None, k: int, k1: float = 1.5, b: float = 0.75) -> list[dict]:
    """BM25 search with tunable Okapi hyperparameters (production always uses
    k1=1.5, b=0.75 -- see retrieval/bm25.py -- exposed here to test whether
    that choice is actually optimal for this corpus, not assumed)."""
    idx = _get_index(ticker, include_tables=False, fiscal_year=None)
    if (k1, b) == (1.5, 0.75):
        bm25 = idx._bm25  # reuse the cached production index -- identical result, no rebuild cost
    else:
        tokenized = [row["text"].lower().split() for row in idx._chunks]
        bm25 = BM25Okapi(tokenized, k1=k1, b=b)
    tokens = query.lower().split()
    scores = bm25.get_scores(tokens)
    top_idx = scores.argsort()[::-1][:k]
    return [dict(idx._chunks[i], score=float(scores[i])) for i in top_idx]


# ── arms ──────────────────────────────────────────────────────────────────
# Each arm: (question, ticker) -> ranked list of {"id":..., "text":...}, using
# FINAL_K as the number of chunks the agent would actually receive.

def arm_baseline(question: str, ticker: str | None) -> list[dict]:
    """Exact reproduction of retrieval/hybrid.py::retrieve_hybrid's current
    production behavior: pool=k*2, RRF_K=60, no query expansion."""
    pool = FINAL_K * 2
    bm = bm25_search(question, ticker, pool)
    dn = dense_search(question, ticker, pool)
    return rrf_merge(bm, dn, FINAL_K, PROD_RRF_K)


def arm_query_expansion_company_name(question: str, ticker: str | None) -> list[dict]:
    """Prepend the company's full legal name to the query before both
    retrievers. Motivated directly by this session's finding: the agent's own
    query for ret_avgo_vmware_acquisition dropped "Broadcom" on one run and
    flipped the verdict -- if the company name mattering that much is real,
    forcing it into the query every time should help, not just accidentally
    sometimes include it."""
    full_name = CLUSTER_RESEARCH.get(ticker, "") if ticker else ""
    expanded = f"{full_name} {question}".strip() if full_name else question
    pool = FINAL_K * 2
    bm = bm25_search(expanded, ticker, pool)
    dn = dense_search(expanded, ticker, pool)
    return rrf_merge(bm, dn, FINAL_K, PROD_RRF_K)


def arm_deeper_pool(question: str, ticker: str | None) -> list[dict]:
    """4x candidate pool instead of 2x before RRF fusion -- tests whether the
    golden passage is often present but just outside the pool each retriever
    contributes, rather than genuinely poorly ranked."""
    pool = FINAL_K * 4
    bm = bm25_search(question, ticker, pool)
    dn = dense_search(question, ticker, pool)
    return rrf_merge(bm, dn, FINAL_K, PROD_RRF_K)


def arm_rrf_k10(question: str, ticker: str | None) -> list[dict]:
    """Smaller RRF_K (10 vs production's 60) weights top ranks much more
    heavily in the fused score -- tests whether the standard IR constant
    (tuned on general web-search corpora) is actually right for a small,
    homogeneous 10-K corpus."""
    pool = FINAL_K * 2
    bm = bm25_search(question, ticker, pool)
    dn = dense_search(question, ticker, pool)
    return rrf_merge(bm, dn, FINAL_K, rrf_k=10)


def arm_bm25_retuned(question: str, ticker: str | None) -> list[dict]:
    """b=0.3 instead of 0.75 -- less length normalization. 10-K prose sections
    vary a lot in length; heavy length normalization can penalize a long
    section that happens to hold the golden sentence among much else."""
    pool = FINAL_K * 2
    bm = bm25_search(question, ticker, pool, k1=1.5, b=0.3)
    dn = dense_search(question, ticker, pool)
    return rrf_merge(bm, dn, FINAL_K, PROD_RRF_K)


def arm_query_expansion_ticker_and_name(question: str, ticker: str | None) -> list[dict]:
    """Both the ticker AND the full name, not just the name -- BM25 term
    matching benefits from the literal ticker (it may appear verbatim in a
    section heading or table row) even where dense retrieval mainly benefits
    from the full name."""
    full_name = CLUSTER_RESEARCH.get(ticker, "") if ticker else ""
    prefix = f"{ticker} {full_name}".strip() if ticker else ""
    expanded = f"{prefix} {question}".strip() if prefix else question
    pool = FINAL_K * 2
    bm = bm25_search(expanded, ticker, pool)
    dn = dense_search(expanded, ticker, pool)
    return rrf_merge(bm, dn, FINAL_K, PROD_RRF_K)


def arm_query_expansion_plus_deeper_pool(question: str, ticker: str | None) -> list[dict]:
    """Combine the two independently-tried ideas: does company-name expansion
    still help once the pool is deeper, or was deeper_pool_4x's regression
    actually company-name-shaped noise that expansion would have fixed too?"""
    full_name = CLUSTER_RESEARCH.get(ticker, "") if ticker else ""
    expanded = f"{full_name} {question}".strip() if full_name else question
    pool = FINAL_K * 4
    bm = bm25_search(expanded, ticker, pool)
    dn = dense_search(expanded, ticker, pool)
    return rrf_merge(bm, dn, FINAL_K, PROD_RRF_K)


RERANK_POOL = 20  # each retriever's own top-N, union'd -- not the RRF-fused top-5.
                   # Motivated directly by the per-retriever breakdown finding: all
                   # three persistent misses sit at rank <=11 in at least ONE
                   # retriever's own list, so a union this deep is known (not
                   # hoped) to contain them; RRF fusion is what buries them, and
                   # this arm skips RRF entirely in favor of a cross-encoder score.


def arm_rerank_cross_encoder(question: str, ticker: str | None) -> list[dict]:
    """Union of each retriever's own top-RERANK_POOL (company-name-expanded
    query, since that's already the best-performing retrieval query), then
    rescore every candidate with a cross-encoder against the RAW question
    (cross-encoders are trained on natural query-passage pairs, not
    keyword-expanded ones) and take the top FINAL_K by that score. No RRF
    involved -- tests whether a relevance model that actually reads both
    texts can recover items RRF's rank-sum formula buries."""
    full_name = CLUSTER_RESEARCH.get(ticker, "") if ticker else ""
    expanded = f"{full_name} {question}".strip() if full_name else question
    bm = bm25_search(expanded, ticker, RERANK_POOL)
    dn = dense_search(expanded, ticker, RERANK_POOL)
    pool: dict[int, dict] = {}
    for r in bm + dn:
        pool.setdefault(r["id"], r)
    candidates = list(pool.values())
    if not candidates:
        return []
    pairs = [(question, (c.get("text") or "")[:2000]) for c in candidates]
    scores = _cross_encoder().predict(pairs)
    ranked = sorted(zip(candidates, scores), key=lambda x: -x[1])
    return [c for c, _ in ranked[:FINAL_K]]


ARMS = {
    "baseline":                     arm_baseline,
    "query_expansion_company":      arm_query_expansion_company_name,
    "query_expansion_ticker+name":  arm_query_expansion_ticker_and_name,
    "query_expansion_deeper_pool":  arm_query_expansion_plus_deeper_pool,
    "deeper_pool_4x":               arm_deeper_pool,
    "rrf_k10":                      arm_rrf_k10,
    "bm25_retuned_b0.3":            arm_bm25_retuned,
    "rerank_cross_encoder":         arm_rerank_cross_encoder,
}


def diagnose_persistent_misses(probes, threshold_arm_fn, depth: int = 50) -> None:
    """For every probe still missing at FINAL_K under the best arm found so
    far, check whether the golden passage exists in the corpus at all (deep
    rank) or is genuinely absent (never chunked / phrase mismatch) --
    a materially different problem, and not one query expansion could fix
    either way."""
    print(f"\n--- diagnosing persistent misses (depth={depth}, not authorized to fix by re-chunking) ---")
    for qid, question, ticker, phrase in probes:
        full_name = CLUSTER_RESEARCH.get(ticker, "") if ticker else ""
        expanded = f"{full_name} {question}".strip() if full_name else question
        bm = bm25_search(expanded, ticker, depth)
        dn = dense_search(expanded, ticker, depth)
        merged = rrf_merge(bm, dn, depth, PROD_RRF_K)
        r = golden_rank(merged, phrase)
        if r is None or r > FINAL_K:
            print(f"  {qid:42} rank at depth {depth}: {r if r else 'NOT FOUND EVEN AT DEPTH ' + str(depth)}")


def diagnose_retriever_breakdown(probes, miss_ids: set[str], depth: int = 50) -> None:
    """For each persistent miss, rank it separately in BM25-only and
    dense-only (no RRF fusion) -- answers "is this buried in one retriever
    that the other could have rescued, or genuinely low in both" which the
    fused-rank diagnostic can't distinguish. Matters directly for whether a
    reranker (rescores an already-decent candidate list) or a fusion-weight
    change (lets a strong single-retriever signal survive fusion) is the
    right next lever."""
    print(f"\n--- per-retriever breakdown for persistent misses (depth={depth}) ---")
    for qid, question, ticker, phrase in probes:
        if qid not in miss_ids:
            continue
        full_name = CLUSTER_RESEARCH.get(ticker, "") if ticker else ""
        expanded = f"{full_name} {question}".strip() if full_name else question
        bm_rank = golden_rank(bm25_search(expanded, ticker, depth), phrase)
        dn_rank = golden_rank(dense_search(expanded, ticker, depth), phrase)
        bm_s = bm_rank if bm_rank else f">{depth}"
        dn_s = dn_rank if dn_rank else f">{depth}"
        print(f"  {qid:42} BM25={bm_s:>5}   dense={dn_s:>5}")


def run() -> dict:
    probes = load_probes()
    n = len(probes)
    print(f"{n} non-retired retrieval questions, deterministic, no LLM.\n")

    summary = {}
    per_question = {}
    for arm_name, arm_fn in ARMS.items():
        ranks, rows = [], []
        for qid, question, ticker, phrase in probes:
            results = arm_fn(question, ticker)
            r = golden_rank(results, phrase)
            ranks.append(r)
            rows.append({"id": qid, "rank": r})
        hits = {k: sum(1 for r in ranks if r and r <= k) for k in KS}
        found = [r for r in ranks if r]
        mrr = sum(1.0 / r for r in found) / n if n else 0.0
        summary[arm_name] = {
            "hit_at": hits, "mrr": round(mrr, 4), "found": len(found),
        }
        per_question[arm_name] = rows
        print(f"{arm_name:28} " + "  ".join(f"hit@{k}={hits[k]}/{n}" for k in KS)
              + f"  MRR={mrr:.3f}")

    out = {
        "n": n, "final_k": FINAL_K, "prod_rrf_k": PROD_RRF_K,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": summary, "per_question": per_question,
    }
    out_path = RESULTS_DIR / f"run_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")

    diagnose_persistent_misses(probes, None, depth=50)
    miss_ids = {
        qid for qid, question, ticker, phrase in probes
        if (lambda r: r is None or r > FINAL_K)(
            golden_rank(arm_query_expansion_company_name(question, ticker), phrase)
        )
    }
    diagnose_retriever_breakdown(probes, miss_ids, depth=50)
    return out


if __name__ == "__main__":
    run()
