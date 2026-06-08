"""
Dense retrieval using pgvector cosine similarity.

Embeds the query with BAAI/bge-small-en-v1.5 (local, no API key needed),
then runs KNN search against the HNSW index on text_chunks.embedding.
"""

from functools import lru_cache

from copilot.storage.db import get_conn

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
# bge models use this prefix for queries (not passages) to improve retrieval quality
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer  # lazy: avoids loading PyTorch at import time
    return SentenceTransformer(EMBED_MODEL)


def embed_query(text: str) -> list[float]:
    vec = _model().encode(BGE_QUERY_PREFIX + text, normalize_embeddings=True)
    return vec.tolist()


def retrieve_dense(query: str, ticker: str | None = None, k: int = 5) -> list[dict]:
    """
    Return top-k text chunks by cosine similarity to the query.
    Returns empty list if no chunks have embeddings yet.
    """
    query_vec = embed_query(query)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if ticker:
                cur.execute(
                    """
                    SELECT ticker, accn, section, text,
                           1 - (embedding <=> %s::vector) AS score
                    FROM text_chunks
                    WHERE embedding IS NOT NULL AND ticker = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_vec, ticker.upper(), query_vec, k),
                )
            else:
                cur.execute(
                    """
                    SELECT ticker, accn, section, text,
                           1 - (embedding <=> %s::vector) AS score
                    FROM text_chunks
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_vec, query_vec, k),
                )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "text":     row["text"],
            "ticker":   row["ticker"],
            "section":  row["section"],
            "score":    float(row["score"]),
            "citation": f"SEC filing accession {row['accn']} — {row['section']}",
            "accn":     row["accn"],
        }
        for row in rows
    ]
