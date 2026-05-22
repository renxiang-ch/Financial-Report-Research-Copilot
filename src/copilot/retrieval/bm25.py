"""
BM25 retrieval over text_chunks stored in Postgres.

BM25Okapi implementation ported from:
  notebooks/stage2_bm25_hybrid.ipynb  (tested on FinDER benchmark)
  Results: BM25 Recall@5=0.29, MRR=0.222 — strong on financial term matching.

Index is built once at startup from the DB and held in memory.
969 chunks is small enough that this is instant.
"""

import math
from collections import Counter

import numpy as np

from copilot.storage.db import get_conn


class BM25Okapi:
    """
    BM25Okapi scoring from scratch.

    score(d, q) = Σ IDF(t) × TF_norm(t, d)
    IDF(t)      = log((N - df + 0.5) / (df + 0.5) + 1)
    TF_norm(t)  = f*(k1+1) / (f + k1*(1 - b + b*|d|/avgdl))
    k1=1.5, b=0.75 (standard hyperparameters)
    """

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b  = b
        self.corpus_size = len(corpus)
        self.avgdl = sum(len(doc) for doc in corpus) / max(self.corpus_size, 1)
        self.doc_freqs = [Counter(doc) for doc in corpus]
        self.doc_len   = [len(doc) for doc in corpus]

        df: dict[str, int] = {}
        for freq in self.doc_freqs:
            for term in freq:
                df[term] = df.get(term, 0) + 1

        self.idf = {
            term: math.log((self.corpus_size - n + 0.5) / (n + 0.5) + 1)
            for term, n in df.items()
        }

    def get_scores(self, query: list[str]) -> np.ndarray:
        scores = np.zeros(self.corpus_size)
        for term in query:
            if term not in self.idf:
                continue
            idf = self.idf[term]
            for i, freq in enumerate(self.doc_freqs):
                f = freq.get(term, 0)
                if f == 0:
                    continue
                dl = self.doc_len[i]
                tf = f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                scores[i] += idf * tf
        return scores


class ChunkIndex:
    """
    In-memory BM25 index built from text_chunks in Postgres.
    Supports optional per-ticker filtering.
    """

    def __init__(self):
        self._chunks: list[dict] = []       # full chunk rows from DB
        self._bm25:   BM25Okapi | None = None

    def build(self, ticker: str | None = None) -> None:
        """Load chunks from DB and build BM25 index."""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if ticker:
                    cur.execute(
                        "SELECT id, ticker, accn, section, chunk_index, text FROM text_chunks WHERE ticker = %s",
                        (ticker.upper(),),
                    )
                else:
                    cur.execute(
                        "SELECT id, ticker, accn, section, chunk_index, text FROM text_chunks"
                    )
                self._chunks = [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

        tokenized = [row["text"].lower().split() for row in self._chunks]
        self._bm25 = BM25Okapi(tokenized)

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Return top-k chunks with BM25 scores."""
        if self._bm25 is None or not self._chunks:
            raise RuntimeError("Index not built. Call build() first.")

        tokens = query.lower().split()
        scores = self._bm25.get_scores(tokens)
        top_idx = scores.argsort()[::-1][:k]

        results = []
        for idx in top_idx:
            chunk = dict(self._chunks[idx])
            chunk["score"] = float(scores[idx])
            results.append(chunk)
        return results


# Module-level index instances — one per ticker + one global
_global_index: ChunkIndex | None = None
_ticker_indexes: dict[str, ChunkIndex] = {}


def _get_index(ticker: str | None) -> ChunkIndex:
    global _global_index
    if ticker:
        ticker = ticker.upper()
        if ticker not in _ticker_indexes:
            idx = ChunkIndex()
            idx.build(ticker=ticker)
            _ticker_indexes[ticker] = idx
        return _ticker_indexes[ticker]
    else:
        if _global_index is None:
            _global_index = ChunkIndex()
            _global_index.build()
        return _global_index


def retrieve_text(query: str, ticker: str | None = None, k: int = 5) -> list[dict]:
    """
    Search text chunks using BM25.

    Args:
        query:  natural language query
        ticker: optional — restrict to one company
        k:      number of results to return

    Returns list of dicts with keys: text, ticker, section, accn, score
    """
    idx = _get_index(ticker)
    results = idx.search(query, k=k)
    return [
        {
            "text":    r["text"],
            "ticker":  r["ticker"],
            "section": r["section"],
            "score":   r["score"],
            "citation": f"SEC filing accession {r['accn']} — {r['section']}",
        }
        for r in results
    ]
