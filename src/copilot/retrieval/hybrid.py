"""
Hybrid retrieval: BM25 + dense with Reciprocal Rank Fusion (RRF).

RRF formula: score(d) = Σ 1 / (k + rank_i)   where k=60 (standard constant)

Why RRF instead of score interpolation:
- Score distributions from BM25 and cosine similarity are not directly comparable.
- RRF is rank-based so it needs no normalisation and is robust to outliers.
- Falls back gracefully if embeddings are not yet generated (dense returns empty).
"""

from copilot.retrieval.bm25 import retrieve_text as bm25_retrieve
from copilot.retrieval.dense import retrieve_dense

RRF_K = 60


def _rrf_merge(
    bm25_results: list[dict],
    dense_results: list[dict],
    k: int,
) -> list[dict]:
    """
    Merge two ranked lists with RRF and return the top-k unique chunks.
    Chunks are identified by (accn, section, text[:80]) to handle duplicates
    across retrieval methods.
    """
    scores: dict[str, float] = {}
    meta: dict[str, dict] = {}

    def key(r: dict) -> str:
        return f"{r['accn']}::{r['section']}::{r['text'][:80]}"

    for rank, result in enumerate(bm25_results, start=1):
        k_ = key(result)
        scores[k_] = scores.get(k_, 0.0) + 1.0 / (RRF_K + rank)
        meta[k_] = result

    for rank, result in enumerate(dense_results, start=1):
        k_ = key(result)
        scores[k_] = scores.get(k_, 0.0) + 1.0 / (RRF_K + rank)
        if k_ not in meta:
            meta[k_] = result

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
    return [meta[k_] for k_, _ in ranked]


def retrieve_hybrid(query: str, ticker: str | None = None, k: int = 5) -> list[dict]:
    """
    Search text chunks with BM25 + dense retrieval fused via RRF.

    Fetches 2*k candidates from each method so the merge pool is large enough.
    Gracefully falls back to BM25-only if dense retrieval has no embeddings yet.
    """
    pool = k * 2

    bm25_results = bm25_retrieve(query, ticker=ticker, k=pool)
    dense_results = retrieve_dense(query, ticker=ticker, k=pool)

    if not dense_results:
        # Embeddings not yet generated — BM25 only
        return bm25_results[:k]

    return _rrf_merge(bm25_results, dense_results, k=k)
