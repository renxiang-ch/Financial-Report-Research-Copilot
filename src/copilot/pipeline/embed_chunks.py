"""
Generate and store local embeddings for all text_chunks.

Uses BAAI/bge-small-en-v1.5 (384 dims) via sentence-transformers.
No API key required — model is downloaded once and cached locally (~130 MB).

Usage:
    python -m copilot.pipeline.embed_chunks
    python -m copilot.pipeline.embed_chunks --ticker AAPL
    python -m copilot.pipeline.embed_chunks --batch-size 64
"""

import argparse

from sentence_transformers import SentenceTransformer

from copilot.storage.db import get_conn
from copilot.storage.schema import migrate_add_embedding

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_BATCH = 64


def fetch_unembedded(conn, ticker: str | None, batch_size: int) -> list[dict]:
    with conn.cursor() as cur:
        if ticker:
            cur.execute(
                """
                SELECT id, text FROM text_chunks
                WHERE embedding IS NULL AND ticker = %s
                ORDER BY id LIMIT %s
                """,
                (ticker.upper(), batch_size),
            )
        else:
            cur.execute(
                "SELECT id, text FROM text_chunks WHERE embedding IS NULL ORDER BY id LIMIT %s",
                (batch_size,),
            )
        return [dict(row) for row in cur.fetchall()]


def store_embeddings(conn, rows: list[dict], embeddings) -> None:
    with conn.cursor() as cur:
        for row, vec in zip(rows, embeddings):
            cur.execute(
                "UPDATE text_chunks SET embedding = %s WHERE id = %s",
                (vec.tolist(), row["id"]),
            )
    conn.commit()


def count_unembedded(conn, ticker: str | None) -> int:
    with conn.cursor() as cur:
        if ticker:
            cur.execute(
                "SELECT COUNT(*) FROM text_chunks WHERE embedding IS NULL AND ticker = %s",
                (ticker.upper(),),
            )
        else:
            cur.execute("SELECT COUNT(*) FROM text_chunks WHERE embedding IS NULL")
        return cur.fetchone()["count"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default=None, help="Restrict to one ticker")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    args = parser.parse_args()

    print(f"Loading model {EMBED_MODEL} (downloads ~130 MB on first run)...")
    model = SentenceTransformer(EMBED_MODEL)

    conn = get_conn()
    migrate_add_embedding(conn)

    total = count_unembedded(conn, args.ticker)
    print(f"Chunks to embed: {total}")
    if total == 0:
        print("Nothing to do — all chunks already embedded.")
        return

    processed = 0
    while True:
        rows = fetch_unembedded(conn, args.ticker, args.batch_size)
        if not rows:
            break

        texts = [r["text"] for r in rows]
        # bge models work best with a query prefix for passage encoding
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        store_embeddings(conn, rows, embeddings)

        processed += len(rows)
        print(f"  {processed}/{total} embedded...")

    conn.close()
    print(f"Done. {processed} chunks embedded with {EMBED_MODEL}.")


if __name__ == "__main__":
    main()
