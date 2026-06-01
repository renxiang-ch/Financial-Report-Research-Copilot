"""
Supply-chain edge extraction from 10-K text chunks.

Method: FinReflectKG single-pass schema-guided extraction (highest faithfulness).
Pipeline:
  1. Regex pre-filter  — narrow 969 chunks to ~30 candidates
  2. LLM single-pass   — gpt-4o-mini extracts structured edges per chunk
  3. Pydantic validate — schema compliance enforced at parse time
  4. DB insert         — deduplicated upsert into supply_edges
  5. Regression check  — verify known ground truth edges

Usage:
    python -m copilot.pipeline.extract_edges
    python -m copilot.pipeline.extract_edges --dry-run
    python -m copilot.pipeline.extract_edges --ticker QRVO
"""

import argparse
import json
import re
from typing import Literal

from pydantic import BaseModel, field_validator
from openai import OpenAI

from copilot.config import settings
from copilot.storage.db import get_conn
from copilot.storage.schema import create_tables

# ── Known ground truth for regression validation ──────────────────────────────
# Source: WRDS Supply Chain / manual verification against 10-K filings
GOLDEN_EDGES = [
    {"supplier": "QRVO", "customer": "AAPL", "revenue_pct": 46.0, "fiscal_year": 2024},
    # SWKS: 10-K text says "more than ten percent" only — exact % (~59%) is in financial notes
    # (not ingested). Regression checks for threshold disclosure (revenue_pct=10.0).
    {"supplier": "SWKS", "customer": "AAPL", "revenue_pct": 10.0, "fiscal_year": 2024},
    # CRUS: ~85% disclosure is in financial notes (Note 14), not in ingested text_chunks.
    # This is a known data coverage gap — not an extraction failure.
]

# ── Customer name → ticker resolution ────────────────────────────────────────
CUSTOMER_ALIASES: dict[str, str] = {
    "apple": "AAPL",
    "apple inc": "AAPL",
    "apple inc.": "AAPL",
    "skyworks": "SWKS",
    "skyworks solutions": "SWKS",
    "qorvo": "QRVO",
    "cirrus logic": "CRUS",
    "corning": "GLW",
    "broadcom": "AVGO",
    "samsung": "005930.KS",
    "samsung electronics": "005930.KS",
    "samsung electronics co., ltd.": "005930.KS",
    "samsung electronics co., ltd": "005930.KS",
    "005930": "005930.KS",
}

# ── Regex pre-filter patterns ─────────────────────────────────────────────────
_CONCENTRATION_PATTERNS = [
    re.compile(r"\d+\s*%\s+of\s+(our\s+)?(net\s+)?revenue", re.IGNORECASE),
    re.compile(r"accounted\s+for\s+\d+", re.IGNORECASE),
    re.compile(r"constituted\s+\d+\s*%", re.IGNORECASE),
    re.compile(r"represented\s+\d+\s*%\s+of\s+(net\s+)?revenue", re.IGNORECASE),
    re.compile(r"(10|ten)\s*%\s+(or\s+more\s+of\s+)?(our\s+)?(net\s+)?revenue", re.IGNORECASE),
    # SWKS-style: "constituted more than ten percent of our net revenue"
    re.compile(r"constituted\s+more\s+than\s+ten\s+percent", re.IGNORECASE),
    re.compile(r"more\s+than\s+ten\s+percent\s+of\s+(our\s+)?(net\s+)?revenue", re.IGNORECASE),
]

def _is_candidate(text: str) -> bool:
    return any(p.search(text) for p in _CONCENTRATION_PATTERNS)


# ── Pydantic output schema ────────────────────────────────────────────────────

class EdgeCandidate(BaseModel):
    customer_name: str
    customer_ticker: str
    revenue_pct: float
    fiscal_year: int
    disclosure_status: Literal["named", "inferred", "unnamed"]

    @field_validator("customer_ticker")
    @classmethod
    def resolve_ticker(cls, v: str) -> str:
        resolved = CUSTOMER_ALIASES.get(v.lower().strip())
        return resolved or v.upper().strip()

    @field_validator("revenue_pct")
    @classmethod
    def pct_range(cls, v: float) -> float:
        if not (0 < v <= 100):
            raise ValueError(f"revenue_pct {v} out of range (0, 100]")
        return v


class ExtractionResult(BaseModel):
    edges: list[EdgeCandidate]


# ── LLM extraction ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You extract supply-chain customer concentration disclosures from SEC 10-K filings.

A customer concentration disclosure states that a single customer accounts for a meaningful
percentage (typically ≥10%) of a company's revenue. These are required under ASC 280-10-50-42.

Rules:
- Extract ONLY explicit disclosures, do not infer or estimate.
- If text says "more than ten percent" or "at least 10%" with no specific number, set revenue_pct to 10.0
  and add a note that this is a threshold-only disclosure (exact % not stated).
- If no percentage or threshold is stated at all, do not extract that customer.

For disclosure_status:
- "named": customer is explicitly named (e.g. "Apple Inc.")
- "inferred": customer is identifiable but not named (e.g. "our largest customer, a smartphone OEM")
- "unnamed": percentage is disclosed but customer cannot be identified

Return valid JSON:
{
  "edges": [
    {
      "customer_name": "<name as in text, or 'unnamed'>",
      "customer_ticker": "<ticker if known, else empty string>",
      "revenue_pct": <float>,
      "fiscal_year": <int>,
      "disclosure_status": "named" | "inferred" | "unnamed"
    }
  ]
}

If no edges found, return {"edges": []}."""


def _extract_from_chunk(
    client: OpenAI,
    chunk_text: str,
    supplier_ticker: str,
) -> list[EdgeCandidate]:
    prompt = (
        f"Supplier company ticker: {supplier_ticker}\n\n"
        f"10-K text chunk:\n{chunk_text}"
    )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content or "{}"
    try:
        result = ExtractionResult.model_validate_json(raw)
        return result.edges
    except Exception as e:
        print(f"    [parse error] {e} | raw={raw[:200]}")
        return []


# ── DB helpers ────────────────────────────────────────────────────────────────

def _create_edges_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supply_edges (
                id                  SERIAL PRIMARY KEY,
                supplier_ticker     TEXT NOT NULL,
                customer_ticker     TEXT NOT NULL,
                revenue_pct         FLOAT,
                fiscal_year         INT,
                disclosure_status   TEXT DEFAULT 'named',
                accn                TEXT,
                chunk_id            INT,
                extracted_at        TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (supplier_ticker, customer_ticker, fiscal_year, accn)
            );
            CREATE INDEX IF NOT EXISTS idx_edges_supplier ON supply_edges (supplier_ticker);
            CREATE INDEX IF NOT EXISTS idx_edges_customer ON supply_edges (customer_ticker);
            CREATE INDEX IF NOT EXISTS idx_edges_fy       ON supply_edges (fiscal_year);
        """)
    conn.commit()


def _upsert_edge(conn, supplier_ticker: str, edge: EdgeCandidate,
                 accn: str, chunk_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO supply_edges
                (supplier_ticker, customer_ticker, revenue_pct, fiscal_year,
                 disclosure_status, accn, chunk_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (supplier_ticker, customer_ticker, fiscal_year, accn)
            DO UPDATE SET
                revenue_pct       = EXCLUDED.revenue_pct,
                disclosure_status = EXCLUDED.disclosure_status,
                chunk_id          = EXCLUDED.chunk_id,
                extracted_at      = NOW()
            RETURNING id
        """, (
            supplier_ticker,
            edge.customer_ticker or edge.customer_name,
            edge.revenue_pct,
            edge.fiscal_year,
            edge.disclosure_status,
            accn,
            chunk_id,
        ))
        return cur.fetchone() is not None
    conn.commit()


# ── Regression validation ─────────────────────────────────────────────────────

def _run_regression(conn, tol: float = 5.0) -> None:
    print("\n── Regression check (known ground truth) ──")
    with conn.cursor() as cur:
        for g in GOLDEN_EDGES:
            cur.execute("""
                SELECT revenue_pct FROM supply_edges
                WHERE supplier_ticker = %s
                  AND customer_ticker = %s
                  AND fiscal_year     = %s
                ORDER BY ABS(revenue_pct - %s) ASC
                LIMIT 1
            """, (g["supplier"], g["customer"], g["fiscal_year"], g["revenue_pct"]))
            row = cur.fetchone()
            if row:
                diff = abs(row["revenue_pct"] - g["revenue_pct"])
                status = "PASS" if diff <= tol else "WARN"
                print(f"  {status} {g['supplier']}→{g['customer']} FY{g['fiscal_year']}: "
                      f"expected {g['revenue_pct']}%  got {row['revenue_pct']}%")
            else:
                print(f"  MISS {g['supplier']}→{g['customer']} FY{g['fiscal_year']}: "
                      f"edge not found in DB")


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_extraction(
    ticker_filter: str | None = None,
    dry_run: bool = False,
) -> None:
    client = OpenAI(api_key=settings.openai_api_key)
    conn   = get_conn()
    _create_edges_table(conn)

    # 1. Load candidate chunks
    with conn.cursor() as cur:
        query = """
            SELECT tc.id, tc.ticker, tc.section, tc.text, tc.accn
            FROM text_chunks tc
            WHERE tc.ticker != 'AAPL'
        """
        params: list = []
        if ticker_filter:
            query += " AND tc.ticker = %s"
            params.append(ticker_filter.upper())
        query += " ORDER BY tc.ticker, tc.id"
        cur.execute(query, params)
        all_chunks = cur.fetchall()

    candidates = [c for c in all_chunks if _is_candidate(c["text"])]
    print(f"Pre-filter: {len(all_chunks)} chunks → {len(candidates)} candidates")

    # 2. Extract edges per candidate chunk
    total_edges = 0
    for chunk in candidates:
        chunk_id    = chunk["id"]
        ticker      = chunk["ticker"]
        section     = chunk["section"]
        accn        = chunk["accn"]
        text        = chunk["text"]

        print(f"\n[{ticker}] chunk {chunk_id} ({section})")
        edges = _extract_from_chunk(client, text, ticker)

        if not edges:
            print("  → no edges found")
            continue

        for edge in edges:
            customer = edge.customer_ticker or edge.customer_name
            print(f"  → {ticker}→{customer}  {edge.revenue_pct}%  "
                  f"FY{edge.fiscal_year}  [{edge.disclosure_status}]")
            if not dry_run:
                _upsert_edge(conn, ticker, edge, accn, chunk_id)
                conn.commit()
            total_edges += 1

    print(f"\nExtracted {total_edges} edges total.")

    # 3. Regression check
    if not dry_run:
        _run_regression(conn)

    conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker",   default=None, help="Extract only for this ticker")
    parser.add_argument("--dry-run",  action="store_true", help="Print without writing to DB")
    args = parser.parse_args()
    run_extraction(ticker_filter=args.ticker, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
