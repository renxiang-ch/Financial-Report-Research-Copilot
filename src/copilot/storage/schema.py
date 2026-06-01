"""Database schema definitions and table creation."""

CREATE_TABLES_SQL = """
-- pgvector extension (required for dense retrieval)
CREATE EXTENSION IF NOT EXISTS vector;

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
    embedding   vector(384),        -- text-embedding-3-small; NULL until embed_chunks runs
    UNIQUE (accn, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_ticker ON text_chunks (ticker);
CREATE INDEX IF NOT EXISTS idx_chunks_accn   ON text_chunks (accn);
-- HNSW index for fast approximate nearest-neighbour search (cosine distance)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON text_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Supply-chain edges extracted from 10-K customer concentration disclosures (Stage 2)
CREATE TABLE IF NOT EXISTS supply_edges (
    id                  SERIAL PRIMARY KEY,
    supplier_ticker     TEXT NOT NULL REFERENCES companies(ticker),
    customer_ticker     TEXT NOT NULL,
    revenue_pct         FLOAT,               -- % of supplier revenue from this customer
    fiscal_year         INT,
    disclosure_status   TEXT DEFAULT 'named', -- 'named' | 'inferred' | 'unnamed'
    accn                TEXT,                -- SEC filing accession (citation)
    chunk_id            INT REFERENCES text_chunks(id),
    extracted_at        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (supplier_ticker, customer_ticker, fiscal_year, accn)
);

CREATE INDEX IF NOT EXISTS idx_edges_supplier ON supply_edges (supplier_ticker);
CREATE INDEX IF NOT EXISTS idx_edges_customer ON supply_edges (customer_ticker);
CREATE INDEX IF NOT EXISTS idx_edges_fy       ON supply_edges (fiscal_year);
"""

# Idempotent migration for databases created before pgvector was added.
MIGRATE_ADD_EMBEDDING_SQL = """
ALTER TABLE text_chunks ADD COLUMN IF NOT EXISTS embedding vector(384);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON text_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
"""


def create_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLES_SQL)
    conn.commit()


def migrate_add_embedding(conn) -> None:
    """Add embedding column + HNSW index to existing databases. Safe to re-run."""
    with conn.cursor() as cur:
        cur.execute(MIGRATE_ADD_EMBEDDING_SQL)
    conn.commit()
