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
- `financial_facts` — XBRL numbers (ticker, label, value, period, accn) — **7,368 rows** (Windows); Mac may differ depending on ingestion runs
- `filings` — 10-K metadata (accn, ticker, filed_date, doc_url) — 18 filings
- `text_chunks` — 10-K body text ~500 token chunks + `embedding vector(384)` — 969 rows, all embedded

### financial_facts: available metrics (10 labels)
Revenue, COGS, GrossProfit, OperatingIncome, NetIncome, EPS_Basic, EPS_Diluted, R&D, TotalAssets, LongTermDebt
— **Not available:** Free Cash Flow, Shareholders' Equity, geographic revenue splits, dividend yield

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
- **Run eval (Windows):** `$env:PYTHONUTF8="1"; uv run --active python -m copilot.eval.harness --out data/eval_results_baseline.json`
  — `PYTHONUTF8=1` required to avoid cp1252 encode errors on Windows terminal
- **start.ps1 uses `uv run`** — NOT `.venv\Scripts\uvicorn` directly; `copilot` package lives under `src/` and is only on the path when launched via `uv run`

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
- 7,368 financial facts across 6 companies, 10 metrics (Windows machine; includes 10-K annual + 10-Q quarterly going back to 2009–2018 depending on company)
- 18 filings (6 companies × 3 years)
- Note: DB contains some FY2025 filings (SWKS, QRVO) fetched automatically — eval questions use FY2024

---

## Question Tiers

| Tier | Type | Example |
|---|---|---|
| Tier 1 | Single-doc lookup | "Company A FY2023 revenue?" |
| Tier 2 | Single-company multi-step | "Company A gross-margin trend, last 4 quarters?" |
| Tier 3 (Stage 2) | Cross-company dependency/exposure | "If Apple cut orders 20%, which suppliers are most exposed?" |

---

## Evaluation Harness (Signature 2 — start Week 5–6)

### Eval Dataset — `data/eval_set.json` (v1.3, 33 questions)
- **10 Tier-1** numeric: direct XBRL lookup, 1 tool call each (covers all 6 companies)
- **10 Tier-2** numeric: ratios + YoY, multi-step (3 margins + 2 margins other cos + 5 YoY)
  - Each Tier-2 question has `input_values` (raw values needed) + `formula`
- **8 retrieval** qualitative: `golden_citations` with `key_phrase` for chunk-level hit detection
- **5 unanswerable**: FCF, China revenue %, D/E ratio, TSMC, dividend yield

All 20 numeric expected_values verified against live DB. Retrieval golden_answers quote actual text chunks.

### Harness Scoring
- **Numeric:** `_extract_number()` from answer text + SI-scale aware tolerance (±0.5%)
- **Tier-2 extra:** `_check_input_values()` verifies agent fetched correct raw values from DB
- **Retrieval:** `_check_key_phrase()` scans `retrieve_text` output chunks for `key_phrase`; LLM judge (0–3); correct = hit ∩ judge≥2
- **Refusal:** keyword detection ("cannot determine", "not available", etc.)
- **tool_trace:** human-readable per-question trace saved to results JSON and printed to console

### Baseline Results (v1.3, first run, 2026-05-26)
| Metric | Score |
|---|---|
| Tier-1 accuracy | **100%** (10/10) |
| Tier-2 accuracy | **80%** (8/10) |
| Tier-2 input fetch | 100% |
| Retrieval passage hit | 50% (4/8) |
| Avg judge score | 2.5/3 |
| Refusal accuracy | 80% (4/5) |
| **Overall** | **78.8%** |
| Cost | $0.02 / 33 questions |
| Avg latency | 3.96s / question |

### v2 Results (2026-05-26) — after negative extraction + FCF refusal fixes
| Metric | Score | Delta |
|---|---|---|
| Tier-1 accuracy | **100%** (10/10) | — |
| Tier-2 accuracy | **100%** (10/10) | +20% |
| Tier-2 input fetch | 100% | — |
| Retrieval passage hit | 62.5% (5/8) | +12.5% |
| Avg judge score | 2.62/3 | +0.12 |
| Refusal accuracy | **100%** (5/5) | +20% |
| **Overall** | **90.9%** | +12.1% |
| Cost | $0.018 / 33 questions | |
| Avg latency | 3.26s / question | |
Results saved: `data/eval_results_v2.json`

### Fixes Applied (v2)
1. **Negative YoY extraction fixed** — harness now reads result from `compute` step output dict first; falls back to text extraction. Captures -2.8005 correctly.
2. **FCF refusal fixed** — system prompt now explicitly lists the 10 available metrics and forbids proxy approximations for unavailable metrics.

### Bug Fixes (2026-05-27)
3. **api.py mock-mode check** — was checking `anthropic_api_key` (always empty); fixed to check `openai_api_key`.
4. **start.ps1 module path** — uvicorn launched via `.venv\Scripts\uvicorn` couldn't find `copilot` package; fixed to use `uv run uvicorn`.
5. **frontend.py timeout** — raised from 60s → 120s to survive first-request embedding model load.

### Remaining Retrieval Misses (3/8)
All three have judge=2/3 (agent answer is correct qualitatively, but exact key_phrase not in top-5 chunks):
- `ret_aapl_product_categories` — retrieves Risk Factors instead of Business section
- `ret_qrvo_customer_risk` — correct section (Risk Factors) but key_phrase chunk ranks below top-5
- `ret_glw_business_segments` — retrieves MD&A instead of Business section

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
- [x] Cross-doc synthesis + citation tracking — Tier-2 eval confirmed 100% (10/10)
- [x] Tier-2 eval score established — 100%
- [x] Parallel tool execution — `ThreadPoolExecutor` runs multiple tool calls per round concurrently (e.g. two `query_financials` in same LLM response run simultaneously)
- [x] Embedding model preloaded at FastAPI startup — `@app.on_event("startup")` warms `retrieve_hybrid` so first user request doesn't pay the 30-60s model load cost

### Weeks 6–8 (Eval & Observability — Signature 2)
- [x] Eval dataset built — `data/eval_set.json` v1.3, **33 questions**:
  - 10 Tier-1 numeric (direct XBRL lookup, all 6 companies, ground truth verified against DB)
  - 10 Tier-2 numeric (ratios + YoY; each has `input_values` + `formula` fields)
  - 8 retrieval qualitative (golden_citations with `key_phrase` for chunk-level detection)
  - 5 unanswerable (refusal correctness)
- [x] Eval harness built — `src/copilot/eval/harness.py`
  - Numeric: `_extract_number` + SI-scale tolerance (±0.5%)
  - Tier-2 extra: `_check_input_values` — verifies agent fetched correct raw values
  - Retrieval: `_check_key_phrase` (scans retrieve_text chunk outputs) + LLM-judge (0–3)
  - Refusal: keyword phrase detection
  - `tool_trace`: per-question readable trace saved to JSON + printed to console
  - Reports: tier1/2/retrieval/refusal accuracy + passage hit + avg judge + cost + latency
- [x] **First eval run complete — baseline established (2026-05-26)**
  - Overall 78.8%, Tier-1 100%, Tier-2 80%, Retrieval 50%, Refusal 80%
  - Results saved: `data/eval_results_baseline.json`
- [x] **v2 eval run — target ≥90% achieved (2026-05-26)**
  - Overall 90.9%, Tier-1 100%, Tier-2 100%, Retrieval 62.5%, Refusal 100%
  - Results saved: `data/eval_results_v2.json`
  - Fixes: harness reads compute output for negatives; system prompt forbids proxy metrics
- [ ] Improve retrieval passage hit from 62.5% → 75%+ (3 misses: AAPL products, QRVO risk, GLW segments)
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

---

## Stage 3+ — Future Directions (post Week 12)

Directions identified from team discussion (2026-05-29). Not yet scoped into sprints.

### Layer Architecture Vision

Upgrade from single-pass RAG to a multi-layer trusted pipeline:

```
User Question
    ↓
[Intent Layer]        classify question type + user role → route to tools/data
    ↓
[Data Layer]          XBRL + 10-K text + 8-K events + supply-chain graph
    ↓
[Agent Layer]         existing ReAct loop (built)
    ↓
[Verification ×3]     numeric check → citation check → cross-doc consistency
    ↓
[Confidence Layer]    score answer by source type, recency, corroboration
    ↓
Cited answer with confidence report + warnings
```

---

### Direction 1 — Data Layer Expansion

- **Scheduled ingestion**: EDGAR RSS feed triggers automatic 10-K/10-Q pull on new filings
- **8-K real-time events**: material contracts, CEO changes, earnings warnings — tag each fact with recency
- **Earnings call transcripts**: management forward-looking statements → text_chunks

---

### Direction 2 — Verification Layer (3 sub-layers)

**V1 — Numeric Verification**
Agent's stated number cross-checked against DB ground truth. Flag mismatches.

**V2 — Citation Verification**
Confirm the cited accession number's source document actually contains the stated number.
Currently only format-validates the accession string.

**V3 — Cross-document Consistency** ← highest priority, true differentiator
Supplier 10-K and customer 10-K should corroborate each other:
- QRVO says Apple = 46% → check if AAPL 10-K independently references QRVO
- Both confirm → high confidence; single-source → medium; contradiction → warning
Enabled by Stage 2 supply-chain graph. Generic RAG systems cannot do this.

---

### Direction 3 — Confidence & Trust Layer

Every answer gets a structured confidence report:

```json
{
  "confidence": 0.95,
  "evidence": {
    "source_type": "SEC 10-K",
    "data_recency": "FY2024",
    "corroboration": "single-source",
    "computation": "direct_lookup"
  },
  "warnings": ["Apple 10-K does not independently confirm this figure"]
}
```

Confidence factors: source type (SEC > news > inference) · recency · corroboration count · answer type (lookup vs computed vs retrieved).

---

### Direction 4 — Intent & User Role Layer

Different personas activate different tool subsets and answer formats:

| Role | Tools active | Answer focus |
|---|---|---|
| Investor | query_financials, compute, retrieve_text | Metrics, valuation, growth |
| Supply chain analyst | query_financials, graph_query, retrieve_text | Dependency exposure, concentration risk |
| Compliance | retrieve_text, graph_query | Risk disclosures, material events, exact quotes |

Implemented as persona config passed into agent system prompt at request time.

---

### Priority Order (post Stage 2)

| Priority | Direction | Rationale |
|---|---|---|
| P1 | V3 Cross-doc consistency | True differentiator; natural extension of Stage 2 graph |
| P2 | Confidence layer | Low implementation cost; reinforces "verifiable" identity |
| P3 | User role routing | High demo impact; product feel |
| P4 | 8-K real-time data | Adds recency; meaningful engineering effort |
| P5 | V1/V2 numeric + citation check | Straightforward but low marginal value given 100% Tier-1 accuracy |
