"""Database schema definitions and table creation."""

CREATE_TABLES_SQL = """
-- Companies in our cluster
CREATE TABLE IF NOT EXISTS companies (
    ticker      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    cik         TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- XBRL financial facts (one row per metric/period)
CREATE TABLE IF NOT EXISTS financial_facts (
    id          BIGSERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL REFERENCES companies(ticker),
    tag         TEXT NOT NULL,   -- XBRL tag e.g. RevenueFromContractWithCustomerExcludingAssessedTax
    label       TEXT NOT NULL,   -- human label e.g. Revenue
    value       NUMERIC NOT NULL,
    unit        TEXT NOT NULL,   -- USD, shares, etc.
    period_end  DATE NOT NULL,   -- end date of the period
    fiscal_year INT,
    fiscal_period TEXT,          -- FY, Q1, Q2, Q3
    form        TEXT,            -- 10-K, 10-Q
    accn        TEXT NOT NULL,   -- accession number (source citation)
    UNIQUE (ticker, tag, period_end, accn)
);

CREATE INDEX IF NOT EXISTS idx_facts_ticker_tag ON financial_facts (ticker, tag);
CREATE INDEX IF NOT EXISTS idx_facts_ticker_fy  ON financial_facts (ticker, fiscal_year);

-- 10-K filing metadata
CREATE TABLE IF NOT EXISTS filings (
    accn        TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL REFERENCES companies(ticker),
    form        TEXT NOT NULL,
    filed_date  DATE NOT NULL,
    fiscal_year INT,
    doc_url     TEXT NOT NULL
);

-- Text chunks from 10-K body (MD&A, Risk Factors, Business)
CREATE TABLE IF NOT EXISTS text_chunks (
    id          BIGSERIAL PRIMARY KEY,
    accn        TEXT NOT NULL REFERENCES filings(accn),
    ticker      TEXT NOT NULL,
    section     TEXT,                -- e.g. "Risk Factors", "MD&A"
    chunk_index INT NOT NULL,
    text        TEXT NOT NULL,
    token_count INT,
    UNIQUE (accn, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_ticker ON text_chunks (ticker);
CREATE INDEX IF NOT EXISTS idx_chunks_accn   ON text_chunks (accn);
"""


def create_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLES_SQL)
    conn.commit()
