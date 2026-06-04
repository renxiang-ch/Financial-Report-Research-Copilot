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
import time
from typing import Literal

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, field_validator
from openai import OpenAI

from copilot.config import settings
from copilot.storage.db import get_conn
from copilot.storage.schema import create_tables

EDGAR_HEADERS = {"User-Agent": "financial-copilot research renxiangchao2678@gmail.com"}

# Note titles that signal customer concentration disclosures
_CONCENTRATION_NOTE_TITLES = [
    "concentration of credit risk",
    "major customer",
    "significant customer",
    "customer concentration",
    "concentrations",
]

# ── Item 8 HTML extraction ────────────────────────────────────────────────────

def _download_html(doc_url: str) -> str:
    resp = httpx.get(doc_url, headers=EDGAR_HEADERS, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser", from_encoding="utf-8")
    for tag in soup(["script", "style", "ix:nonfraction", "ix:nonnumeric"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = text.encode("ascii", errors="ignore").decode("ascii")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r" {2,}", " ", text).strip()


def _extract_item8_candidates(full_text: str) -> list[str]:
    """
    Find paragraphs containing customer concentration disclosures anywhere in the filing.

    Strategy: search for paragraphs near a concentration note title, rather than
    relying on Item 8 boundaries (which are unreliable due to Table of Contents
    entries matching the same regex as actual section headers).
    """
    paragraphs = [p.strip() for p in full_text.split("\n\n") if len(p.strip()) > 40]

    candidates: list[str] = []
    capturing = False

    for para in paragraphs:
        lower = para.lower()

        # Start capturing when we enter a concentration-related section
        if any(t in lower for t in _CONCENTRATION_NOTE_TITLES):
            capturing = True

        # Stop when a clearly unrelated section header appears
        if capturing and re.search(
            r"\b(goodwill|income tax|stock.based compensation|debt|leases|"
            r"pension|derivative|fair value|equity|commitments|subsequent events)\b",
            lower,
        ) and len(para) < 120:  # short lines are likely section headers
            capturing = False

        if capturing and _is_candidate(para):
            candidates.append(para)

    return candidates


def run_extraction_from_html(
    ticker_filter: str | None = None,
    dry_run: bool = False,
) -> None:
    """Extract supply-chain edges from Item 8 Financial Notes in 10-K HTML filings."""
    client = OpenAI(api_key=settings.openai_api_key)
    conn   = get_conn()
    _create_edges_table(conn)

    # Load filings from DB (skip AAPL — it's the customer, not a supplier)
    # Change it when we expand dataset
    with conn.cursor() as cur:
        query = "SELECT ticker, accn, fiscal_year, doc_url FROM filings WHERE ticker != 'AAPL'"
        params: list = []
        if ticker_filter:
            query += " AND ticker = %s"
            params.append(ticker_filter.upper())
        query += " ORDER BY ticker, fiscal_year DESC"
        cur.execute(query, params)
        filings = cur.fetchall()

    print(f"Processing {len(filings)} filings for Item 8 extraction\n")
    total_edges = 0

    for filing in filings:
        ticker     = filing["ticker"]
        accn       = filing["accn"]
        fiscal_year = filing["fiscal_year"]
        doc_url    = filing["doc_url"]

        print(f"[{ticker}] FY{fiscal_year}  {accn}")
        try:
            html_text  = _download_html(doc_url)
            candidates = _extract_item8_candidates(html_text)
        except Exception as e:
            print(f"  ERROR downloading: {e}")
            time.sleep(1)
            continue

        if not candidates:
            print("  → no concentration note found in Item 8")
            time.sleep(0.5)
            continue

        print(f"  {len(candidates)} candidate paragraphs")
        for para in candidates:
            edges = _extract_from_chunk(client, para, ticker)
            for edge in edges:
                customer = _normalize_customer(edge.customer_ticker, edge.customer_name)
                print(f"  → {ticker}→{customer}  {edge.revenue_pct}%  FY{edge.fiscal_year}  [{edge.disclosure_status}]")
                if not dry_run:
                    _upsert_edge(conn, ticker, edge, accn, chunk_id=None,
                                 source_text=edge.evidence_sentence or None,
                                 filing_fiscal_year=fiscal_year)
                    conn.commit()
                total_edges += 1

        time.sleep(0.5)  # be polite to EDGAR

    print(f"\nExtracted {total_edges} edges from Item 8.")
    if not dry_run:
        _run_regression(conn)
    conn.close()


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
    "apple, inc.": "AAPL",
    "apple, inc": "AAPL",
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
    # CRUS-style: "represented approximately 87 percent of...sales"
    re.compile(r"\d+\s+percent\s+(of\s+)?(our\s+)?(net\s+)?(revenue|sales)", re.IGNORECASE),
    re.compile(r"approximately\s+\d+\s+percent", re.IGNORECASE),
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
    threshold_only: bool = False
    evidence_sentence: str = ""

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
- If text gives an exact percentage (e.g. "accounted for 10% of revenue", "accounted for 10% and 12%"),
  set revenue_pct to that number and threshold_only to false — even if the number happens to be exactly 10.
  IMPORTANT: "accounted for 10%" is an exact figure, not a threshold. threshold_only must be false.
- If text gives only a vague lower bound with no specific number
  (e.g. "more than ten percent", "at least 10%", "over 10%", "constituted more than ten percent"),
  set revenue_pct to 10.0 AND threshold_only to true.
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
      "disclosure_status": "named" | "inferred" | "unnamed",
      "threshold_only": <true | false>,
      "evidence_sentence": "<copy the exact sentence(s) from the text that state this percentage for this customer>"
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
                source_text         TEXT,
                threshold_only      BOOLEAN DEFAULT FALSE,
                extracted_at        TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (supplier_ticker, customer_ticker, fiscal_year)
            );
            CREATE INDEX IF NOT EXISTS idx_edges_supplier ON supply_edges (supplier_ticker);
            CREATE INDEX IF NOT EXISTS idx_edges_customer ON supply_edges (customer_ticker);
            CREATE INDEX IF NOT EXISTS idx_edges_fy       ON supply_edges (fiscal_year);
        """)
    conn.commit()


def _normalize_customer(ticker: str, name: str) -> str:
    """Resolve to canonical ticker — tries ticker field first, then customer_name."""
    for candidate in (ticker, name):
        resolved = CUSTOMER_ALIASES.get(candidate.lower().strip())
        if resolved:
            return resolved
    return (ticker or name).strip()


def _upsert_edge(conn, supplier_ticker: str, edge: EdgeCandidate,
                 accn: str, chunk_id: int | None,
                 source_text: str | None = None,
                 filing_fiscal_year: int | None = None) -> bool:
    """
    filing_fiscal_year: the fiscal year of the filing being processed.
    When edge.fiscal_year == filing_fiscal_year, this is the primary filing for that
    relationship — accn and source_text are authoritative and always written.
    When a later filing references prior-year data (edge.fiscal_year < filing_fiscal_year),
    only update accn/source_text if no value exists yet (NULL guard).
    """
    customer = _normalize_customer(edge.customer_ticker, edge.customer_name)
    is_primary = (filing_fiscal_year is None or edge.fiscal_year == filing_fiscal_year)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO supply_edges
                (supplier_ticker, customer_ticker, revenue_pct, fiscal_year,
                 disclosure_status, threshold_only, accn, chunk_id, source_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (supplier_ticker, customer_ticker, fiscal_year)
            DO UPDATE SET
                revenue_pct       = EXCLUDED.revenue_pct,
                disclosure_status = EXCLUDED.disclosure_status,
                threshold_only    = EXCLUDED.threshold_only,
                accn        = CASE WHEN %s OR supply_edges.accn IS NULL
                                   THEN EXCLUDED.accn ELSE supply_edges.accn END,
                chunk_id    = CASE WHEN %s OR supply_edges.chunk_id IS NULL
                                   THEN EXCLUDED.chunk_id ELSE supply_edges.chunk_id END,
                source_text = CASE WHEN %s OR supply_edges.source_text IS NULL
                                   THEN EXCLUDED.source_text ELSE supply_edges.source_text END,
                extracted_at = NOW()
            RETURNING id
        """, (
            supplier_ticker, customer, edge.revenue_pct, edge.fiscal_year,
            edge.disclosure_status, edge.threshold_only, accn, chunk_id, source_text,
            is_primary, is_primary, is_primary,
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

    # 1. Load candidate chunks (join filing fiscal_year for primary-filing detection)
    with conn.cursor() as cur:
        query = """
            SELECT tc.id, tc.ticker, tc.section, tc.text, tc.accn,
                   fi.fiscal_year as filing_fiscal_year
            FROM text_chunks tc
            JOIN filings fi ON fi.accn = tc.accn
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
        chunk_id           = chunk["id"]
        ticker             = chunk["ticker"]
        section            = chunk["section"]
        accn               = chunk["accn"]
        text               = chunk["text"]
        filing_fiscal_year = chunk["filing_fiscal_year"]

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
                _upsert_edge(conn, ticker, edge, accn, chunk_id,
                             source_text=edge.evidence_sentence or None,
                             filing_fiscal_year=filing_fiscal_year)
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
    parser.add_argument("--ticker",  default=None, help="Extract only for this ticker")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing to DB")
    parser.add_argument("--source",  default="chunks", choices=["chunks", "html", "all"],
                        help="chunks=text_chunks table (default), html=Item 8 HTML, all=both")
    args = parser.parse_args()

    if args.source in ("chunks", "all"):
        run_extraction(ticker_filter=args.ticker, dry_run=args.dry_run)
    if args.source in ("html", "all"):
        run_extraction_from_html(ticker_filter=args.ticker, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
