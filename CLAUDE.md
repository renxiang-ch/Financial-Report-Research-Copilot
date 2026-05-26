# Financial-Report Research Copilot — Project Context

## What This Project Is

An end-to-end, deployable, evaluation-driven system that answers analyst-style questions over SEC filings with **verifiable numbers and citations**, extended with **supply-chain dependency reasoning** as the vertical differentiator.

This is a lab/solo project (~3 months). It competes on **engineering systems**, not on retrieval benchmarks.

---

## Two-Stage Structure

### Stage 1 (Weeks 0–8) — RAG Financial Extraction & QA
A **standalone, complete project**. A deployed agentic copilot that answers Tier 1–2 financial questions with verifiable numbers and citations.

### Stage 2 (Weeks 8–12) — Graph Knowledge Layer
Additive differentiator. Supply-chain dependency graph built from EDGAR disclosures; enables Tier 3 cross-company exposure reasoning. **Do not start until Stage 1 ships.**

---

## Non-Negotiable Constraints

- **Numbers come from SQL + code, NEVER from LLM computation.** This is the cardinal rule.
- **Honest refusal over fabrication.** When evidence is absent, the system says "cannot determine."
- **No fine-tuning, no training.** LLM is API-only.
- **No PDF table extraction.** Use XBRL structured data to bypass it entirely.
- **No investment advice.** Always position as research aid.

---

## Out of Scope (do not implement)

- SOTA accuracy chasing on FinanceBench / FinDER
- LLM training or fine-tuning
- PDF table extraction
- Real-time news ingestion (8-K is optional)
- Automatic source-credibility scoring
- Expanding company cluster beyond initial group
- Multiple industries

---

## Company Cluster

**Apple supplier cluster** (recommended):
- Hub: Apple (AAPL)
- Suppliers: Skyworks, Qorvo, Cirrus Logic, Corning, Broadcom
- Constraint: US-listed only (10-K filers); avoid foreign filers (TSMC, Foxconn file 20-F)
- Time span: last 3–4 years

Customer-concentration edges are explicitly disclosed in 10-Ks (>~10% of revenue rule), making them extractable and verifiable.

---

## Architecture Summary

```
User → Frontend (Streamlit) → FastAPI → Agent Orchestration Layer
                                              ↓           ↓           ↓
                                      Postgres (XBRL)  pgvector   Edge table (Stage 2)
                                              ↑
                                      Data Pipeline (EDGAR ETL)
```

### Agent Tools
- `list_metrics(ticker)` → available metrics + year ranges for a company ✅ built
- `query_financials(ticker, metric, fiscal_year)` → Postgres XBRL numbers ✅ built
- `compute(expression, variables)` → sandboxed eval, never model-computed ✅ built
- `retrieve_text(query, ticker)` → hybrid BM25 + pgvector retrieval (RRF fusion) ✅ built
- `graph_query(...)` → graph traversal (Stage 2 only)

### Database Tables (all in PostgreSQL 18)
- `companies` — ticker, name, CIK
- `financial_facts` — XBRL numbers (ticker, label, value, period, accn) — 7,368 rows
- `filings` — 10-K metadata (accn, ticker, filed_date, doc_url) — 18 filings
- `text_chunks` — 10-K body text ~500 token chunks + `embedding vector(384)` — 969 rows, all embedded

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.14 |
| Ingestion | EDGAR REST API + XBRL companyfacts/frames API |
| Storage | PostgreSQL + pgvector 0.8.2 |
| Embeddings | `BAAI/bge-small-en-v1.5` via sentence-transformers (local, 384 dims, no API key) |
| Retrieval | Hybrid BM25 + pgvector dense, fused with RRF (k=60) |
| LLM | **gpt-4o-mini** (agent + eval judge) via OpenAI API |
| Agent | Hand-written loop, 4 tools, multi-step system prompt |
| Eval / Observability | Custom harness (numeric + citation + LLM-judge) + Langfuse (wired, not yet active) |
| Graph (Stage 2) | Postgres edge table + SQL (no Neo4j unless learning graph DBs) |
| API | FastAPI |
| Frontend | Streamlit (current), Next.js / React (later) |
| Deploy | Docker + Render/Railway/Fly |

---

## Device Configuration

Two development machines share this repo. **Do not confuse their setups.**

### Mac (primary)
- **OS:** macOS
- **PostgreSQL:** 18 via Postgres.app
- **pgvector:** 0.8.2 (built-in with Postgres.app)
- **Python:** 3.14 via pyenv or system
- **Start app:** `./start.sh` (or equivalent)
- **DB name:** `financial_copilot`

### Windows (secondary)
- **OS:** Windows 11
- **PostgreSQL:** 17.10 (installed via EDB installer, path: `C:\Program Files\PostgreSQL\17`)
- **pgvector:** 0.8.2 — built from source using Visual Studio Build Tools 2026 (`C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools`), DLL copied manually to `C:\Program Files\PostgreSQL\17\lib\`
- **Python:** 3.14 via `.venv` in project root
- **Start app:** `.\start.ps1` in PowerShell (must run from project directory)
- **DB name:** `financial_copilot`
- **psql PATH:** `C:\Program Files\PostgreSQL\17\bin` added to user PATH

### Shared .env (never committed)
Both machines need a `.env` file in the project root with:
```
ANTHROPIC_API_KEY=...       # kept for reference, agent now uses OpenAI
OPENAI_API_KEY=...          # used by agent (gpt-4o-mini) + eval judge
DATABASE_URL=postgresql://postgres:<password>@localhost:5432/financial_copilot
```
config.py resolves `.env` via absolute path from `__file__`, so it works regardless of working directory.

### Database state (both machines in sync)
- 969 text chunks — all embedded with `BAAI/bge-small-en-v1.5` (run `python -m copilot.pipeline.embed_chunks` if cloning fresh)
- 10,075 financial facts across 6 companies
- 18 filings (6 companies × 3 years)

---

## Question Tiers

| Tier | Type | Example |
|---|---|---|
| Tier 1 | Single-doc lookup | "Company A FY2023 revenue?" |
| Tier 2 | Single-company multi-step | "Company A gross-margin trend, last 4 quarters?" |
| Tier 3 (Stage 2) | Cross-company dependency/exposure | "If Apple cut orders 20%, which suppliers are most exposed?" |

---

## Evaluation Harness (Signature 2 — start Week 5–6)

Metrics:
- Numeric accuracy (exact / tolerance match)
- Citation precision / recall
- Faithfulness (LLM-judge: no fabrication)
- Refusal correctness (unanswerable questions must be refused)
- Latency & cost per question

**Eval set is frozen.** Run regression on every change.

The headline result (Stage 2): baseline naive-RAG vs graph-augmented agent on Tier-3 questions → quantified vertical-accuracy delta.

---

## Engineering Principles

1. **Walking Skeleton first.** By end of Week 2, one thin thread through every layer (ugly but alive).
2. **Breadth across the stack, depth in two signatures only:** agent orchestration and eval harness.
3. **Do not go deep on retrieval** — that's the crowded trap.
4. **XBRL bypass** — structured numbers avoid PDF extraction entirely.
5. **Eval-first** — harness drives iteration from Week 5–6 onward.

---

## Acceptance Criteria ("done")

1. A Tier-3 dependency question → correct, cited, number-verifiable answer with reasoning steps.
2. An unanswerable question → correct refusal, no fabrication.
3. Quantified three-tier eval results (numeric / citation / faithfulness / refusal + cost/latency) **and the baseline-vs-graph ablation delta**.
4. Live deployment + scheduled ingestion running.

---

## Milestone Checklist

### Week 0
- [x] Repo, Python env, CI skeleton
- [x] EDGAR API + XBRL companyfacts API working
- [x] Lock company cluster (AAPL + SWKS / QRVO / CRUS / GLW / AVGO)

### Weeks 1–2 (Walking Skeleton)
- [x] Ingest one company, one 10-K XBRL (financial_facts table, 2211 rows for AAPL)
- [x] Postgres set up (pgvector deferred — not available for PostgreSQL 17 on Windows)
- [x] query_financials + compute tools built and tested
- [x] Agent main loop (Claude tool use, hand-written)
- [x] FastAPI /ask + /health endpoints
- [x] Streamlit frontend (answer + citations + reasoning steps)
- [x] End-to-end agent test confirmed working — Tier-1 queries return verified numbers + correct SEC .htm citation URLs

### Weeks 3–4 (Data Pipeline)
- [x] Batch-ingest full cluster XBRL (7,368 facts across 6 companies)
- [x] XBRL financials normalized into SQL (financial_facts table)
- [x] 10-K body text downloaded, sectioned, chunked (969 chunks, 18 filings)
- [x] text_chunks + filings tables in Postgres
- [x] BM25 retrieval built and wired into retrieve_text agent tool
- [x] Dense retrieval + pgvector — **unblocked on Mac (Postgres.app 18 + pgvector 0.8.2)**
  - Embedding model: `BAAI/bge-small-en-v1.5` (384 dims, local, no API key)
  - HNSW index on text_chunks.embedding, all 969 chunks embedded
  - Hybrid retrieval (BM25 + dense, RRF fusion) wired into retrieve_text tool
- [ ] Tier-1 eval runs automatically (harness built, pending first run with API key)

### Weeks 5–7 (Agent + Tools — Signature 1)
- [x] Tools: `query_financials` / `compute` / `retrieve_text` all wired into agent loop
- [x] `list_metrics(ticker)` tool added — agent can discover available metrics + year ranges before querying
- [x] Multi-step system prompt — explicit decomposition rules for ratios, YoY, cross-company
- [x] Token usage tracking added to agent return value
- [x] Model switched to `gpt-4o-mini` (OpenAI) — agent + eval judge both use OpenAI API
- [x] Circuit breaker — `for _ in range(MAX_ROUNDS=10)...else` pattern; returns error message if loop exhausts without a final answer
- [ ] Cross-doc synthesis + citation tracking (Tier-2 end-to-end not yet eval-verified)
- [ ] Tier-2 eval score established

### Weeks 6–8 (Eval & Observability — Signature 2)
- [x] Eval dataset built — `data/eval_set.json`, 59 questions:
  - 29 Tier-1 numeric (direct XBRL lookup, ground truth from DB)
  - 17 Tier-2 numeric (ratios + YoY, computed from DB)
  - 8 retrieval qualitative (golden citation + golden answer, LLM-judge scoring)
  - 5 unanswerable (refusal correctness)
- [x] Eval harness built — `src/copilot/eval/harness.py`
  - Numeric: value extraction + tolerance match (±0.5%, SI-scale aware)
  - Retrieval: citation hit-check + LLM-judge (Haiku, 0–3 score)
  - Refusal: phrase-based detection
  - Reports: accuracy by tier, citation %, avg judge score, latency, cost
- [ ] First eval run + baseline score established (OpenAI key configured, ready to run)
- [ ] Iterate agent against eval results
- [ ] **Stage 1 milestone: complete, deployable agentic QA copilot**

### Weeks 8–9 (Relationship Extraction)
- [ ] Extraction schema defined
- [ ] Extract >10% customer concentration + named suppliers/customers + risk-factor deps
- [ ] Directed edge table with attributes + source citations

### Weeks 9–10 (Graph Tool)
- [ ] `graph_query` tool added
- [ ] Agent traverses graph + combines with numbers + text
- [ ] Tier-3 dependency/exposure questions answerable

### Weeks 10–11 (Vertical Eval + Ablation)
- [ ] Tier-3 vertical eval set built
- [ ] Baseline-RAG vs graph-augmented measured
- [ ] Vertical-accuracy delta quantified

### Weeks 11–12 (Deploy + Polish)
- [ ] Frontend polish (exposure view / graph viz)
- [ ] Containerize + deploy (Render/Railway/Fly)
- [ ] Scheduled ingestion running
- [ ] README, demo script, recorded demo
