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
- `graph_query(customer, supplier, fiscal_year, depth)` → supply-chain graph traversal ✅ built
  - `customer` and/or `supplier` filter edges; pass both to query a specific pair
  - `fiscal_year`: `'latest'` (per-company max), `'trend'` (all years), int year, `'YYYY-YYYY'` range
  - `depth=2` triggers recursive CTE for multi-hop
  - Returns per-edge: `revenue_pct`, `threshold_only`, `citation`, `source_text`, `traversal_trace`
  - Uniqueness guaranteed by DB constraint `(supplier, customer, fiscal_year)` — no query-layer dedup needed

### Database Tables (all in PostgreSQL 18)
- `companies` — ticker, name, CIK
- `financial_facts` — XBRL numbers (ticker, label, value, period, accn) — **7,368 rows** (Windows); Mac may differ depending on ingestion runs
- `filings` — 10-K metadata (accn, ticker, filed_date, doc_url) — 18 filings
- `text_chunks` — 10-K body text ~500 token chunks + `embedding vector(384)` — 969 rows, all embedded
- `supply_edges` — directed edges from 10-K customer concentration disclosures; includes `source_text TEXT` (verbatim LLM-quoted disclosure sentence per edge), `threshold_only BOOLEAN` (true = text said ">10%" only, exact % not stated — NOT a sentinel value, stored at extraction time by LLM)

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

### Database state

**Windows (research branch — SC-DisclosureQA dataset):**
- 7,444 text chunks — all embedded with `BAAI/bge-small-en-v1.5` (research cluster, 10 years)
- 42,244 financial facts across 15 companies, 20 metrics (FY2015-2025 depending on company)
- 148 filings (15 companies × ~9.9 years avg)
- supply_edges: **110 rows, 29 (supplier,customer) pairs** — chunks + HTML extraction complete, full cleanup done (2026-06-11)
- Run embed: `uv run --active python -m copilot.pipeline.embed_chunks`
- Run extraction: `$env:PYTHONUTF8="1"; uv run --active python -m copilot.pipeline.extract_edges --source all`

**Mac (V1 prototype):**
- 969 text chunks — all embedded
- 7,368 financial facts across 6 companies, 10 metrics
- 18 filings (6 companies × 3 years)
- Note: DB contains some FY2025 filings (SWKS, QRVO) — eval questions use FY2024

**Filing coverage per company (Windows research DB):**

| Ticker | Text coverage | XBRL coverage | Notes |
|---|---|---|---|
| AAPL | FY2016-2025 (10yr) | FY2009+ | Hub company |
| CRUS | FY2017-2026 (10yr) | FY2015+ | |
| QRVO | FY2017-2026 (10yr) | FY2016+ | IPO Jan 2015; first 10-K FY2016 |
| SWKS | FY2016-2025 (10yr) | FY2015+ | |
| **AVGO** | **FY2018-2025 (8yr)** | **FY2018+** | **Entity limit: CIK 0001730168 created 2018 when Avago Technologies acquired old Broadcom Corp and renamed. Pre-merger Avago 10-Ks exist under old CIK 0001549802 (different entity, different product mix — not included).** |
| QCOM | FY2016-2025 (10yr) | FY2015+ | FY2016-2017 fetched from EDGAR archive after pagination fix |
| GLW | FY2017-2026 (10yr) | FY2015+ | FY2018-2019 return 0 chunks (old HTML format); fetched via archive pagination |
| ADI | FY2016-2025 (10yr) | FY2015+ | FY2016-2017 fetched from archive; ADI merged with Linear Technology in 2017 (surviving entity, same CIK) |
| TXN | FY2017-2026 (10yr) | FY2015+ | 14 chunks/filing avg — Texas Instruments 10-Ks are minimalist |
| MCHP | FY2017-2026 (10yr) | FY2015+ | FY2017-2018 fetched from archive; acquired Microsemi in 2018 (surviving entity) |
| ON | FY2017-2026 (10yr) | FY2015+ | |
| LRCX | FY2016-2025 (10yr) | FY2015+ | FY2024-2025 required heading-regex fix (Item1.\nTitle format) |
| APH | FY2017-2026 (10yr) | FY2015+ | |
| JBL | FY2016-2025 (10yr) | FY2015+ | FY2016-2017 fetched from archive |
| SANM | FY2016-2025 (10yr) | FY2015+ | |

**Known text coverage gaps (Windows research DB):**
- AVGO: Genuinely only 8 years — new legal entity post-2018 merger, no archive exists
- GLW FY2018-2019: 0 chunks (old pre-iXBRL HTML format not parseable by section regex)
- TXN: ~14 chunks/filing (minimalist 10-K structure, not a parsing issue)
- SWKS: ~17 chunks/filing (very brief customer concentration disclosures, threshold-only Apple)
- QRVO: FY2016 10-K exists in EDGAR archive but not fetched (first year as merged entity, partial year); FY2017-2026 covers full trading history

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

### v3 Results (2026-06-03) — regression after Stage 2 system prompt additions
| Metric | Score | Delta vs v2 |
|---|---|---|
| Tier-1 accuracy | **100%** (10/10) | — |
| Tier-2 accuracy | **100%** (10/10) | — |
| Tier-2 input fetch | 100% | — |
| Retrieval passage hit | 50.0% (4/8) | -12.5% |
| Avg judge score | 2.5/3 | -0.12 |
| Refusal accuracy | **100%** (5/5) | — |
| **Overall** | **87.9%** | -3pp |
| Cost | $0.027 / 33 questions | |
| Avg latency | 4.15s / question | |
Results saved: `data/eval_results_v3.json`

**Retrieval drop explained (not a real regression):**
- `ret_swks_apple_concentration_2024`: agent now correctly uses `graph_query` instead of `retrieve_text` for Apple-dependency questions (better behavior), but retrieval harness only checks `retrieve_text` chunk output → false negative
- `ret_avgo_vmware_acquisition`: stochastic retrieval non-determinism (different top-5 chunks each run)
- `ret_glw_business_segments`: recovered (was miss in v2, now hit in v3) — also stochastic
- Tier-1/2/Refusal: **zero regression** — all numeric and refusal capabilities intact

### Remaining Retrieval Misses
Current state (v3, 4 misses):
- `ret_swks_apple_concentration_2024` — agent prefers `graph_query` (correct behavior, harness limitation)
- `ret_avgo_vmware_acquisition` — retrieval non-determinism
- `ret_aapl_product_categories` — retrieves Risk Factors instead of Business section
- `ret_qrvo_customer_risk` — correct section but key_phrase chunk ranks below top-5

**Eval set is frozen.** Run regression on every change.

---

### Tier-3 Ablation Results (2026-06-03) — Stage 2 core deliverable
Run commands:
```
$env:PYTHONUTF8="1"; uv run --active python -m copilot.eval.harness_tier3 --out data/eval_results_t3_graph.json
$env:PYTHONUTF8="1"; uv run --active python -m copilot.eval.harness_tier3 --no-graph --out data/eval_results_t3_baseline.json
```

| Metric | Graph-augmented | Baseline (no-graph) | Delta |
|---|---|---|---|
| **Overall accuracy** | **100%** (8/8) | **12.5%** (1/8) | **+87.5pp** |
| graph_lookup | 100% | 0% | +100pp |
| graph_fact | 100% | 0% | +100pp |
| graph_trend | 100% | 0% | +100pp |
| graph_comparison | 100% | 0% | +100pp |
| graph_compute | 100% | 0% | +100pp |
| Refusal accuracy | 100% | 100% | 0 |
| Avg latency | 5.45s | 10.33s | faster |
| Cost / run | $0.008 | $0.024 | cheaper |

**Interpretation:** The +87.5pp gap is the graph layer's contribution. Baseline naive-RAG cannot answer Tier-3 supply-chain questions — it doesn't fail gracefully, it uses wrong approaches (e.g. reversing Apple's revenue to estimate supplier share). Graph-augmented completes all 7 answerable questions correctly and refuses the 1 unanswerable one.

Results saved: `data/eval_results_t3_graph.json`, `data/eval_results_t3_baseline.json`

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
- [x] Extraction schema defined — `supply_edges` table in PostgreSQL
- [x] Extract >10% customer concentration — pipeline built and run (`src/copilot/pipeline/extract_edges.py`)
- [x] Directed edge table with attributes + source citations — named edges across QRVO/SWKS/CRUS/AVGO
- [x] Entity normalization — customer names unified to canonical tickers (AAPL, 005930.KS, etc.)
- [x] Regression validation — QRVO→AAPL FY2024 46% PASS against WRDS ground truth
- [x] WRDS Supply Chain validation — downloaded Compustat Segment data, confirmed QRVO/AVGO exact match
- [x] Item 8 HTML pipeline — direct HTML parsing for Financial Notes (bypasses text_chunks)
  - CRUS→AAPL now extracted: 87%/83%/79% (FY2024/2023/2022), matches WRDS exactly
  - Added text-form percent regex ("87 percent" vs "87%") for CRUS-style disclosures
  - CLI: `--source chunks|html|all`
- [x] `source_text` column — redesigned to per-edge LLM-quoted sentence (2026-06-03)
  - Original design: `_extract_matching_sentences()` returned all regex-hit sentences from the paragraph
    → multiple edges from same paragraph shared one source_text; AVGO stored distributor sentence instead of Apple sentence
  - New design: `EdgeCandidate.evidence_sentence` field — LLM quotes the exact sentence(s) that state
    the percentage for THIS customer. Each edge gets its own independent source_text.
  - `_extract_matching_sentences()` deleted; both pipelines now use `edge.evidence_sentence or None`
- [x] `threshold_only BOOLEAN` column added to `supply_edges` (2026-06-03)
  - Previous approach: `revenue_pct == 10.0` as a sentinel — bug: exact 10% disclosures (e.g. QRVO→Samsung)
    would be misclassified as threshold-only
  - Fix: LLM sets `threshold_only=true` only when text says "more than ten percent" / "at least 10%"
    with no exact figure. Exact "10%" → `threshold_only=false`.
  - LLM prompt explicitly contrasts: `"accounted for 10%"` (exact) vs `"more than ten percent"` (threshold)
  - `tools.py` reads `threshold_only` from DB column; no longer infers from value
  - Migration: `ALTER TABLE supply_edges ADD COLUMN IF NOT EXISTS threshold_only BOOLEAN DEFAULT FALSE`
  - SWKS (confirmed threshold from text) manually set to TRUE; QRVO→Samsung FY2025/2026 correctly FALSE
- [x] `CUSTOMER_ALIASES` extended — added `"apple, inc."` and `"apple, inc"` (with comma) variants
  - Root cause: LLM sometimes returns `"Apple, Inc."` (with comma) as customer_ticker, which was not
    in the alias dict → stored as literal string, creating duplicate rows with different customer_ticker
- [x] `supply_edges` UNIQUE constraint fixed — was `(supplier, customer, fiscal_year, accn)`, now `(supplier, customer, fiscal_year)`
  - Root cause: 10-K filings report 2-3 years of comparative data. SWKS→AAPL FY2024 appears in both
    the FY2024 10-K (current year) and the FY2025 10-K (prior-year comparison), each with a different accn.
    Including accn in the UNIQUE key treated them as different rows — wrong.
  - Natural key is (supplier, customer, fiscal_year). accn is a citation attribute, not an identity.
  - Migration: dropped old constraint, deleted 22 duplicate rows (kept latest extracted_at), added new constraint.
  - source_text write strategy: `is_primary = (edge.fiscal_year == filing_fiscal_year)`.
    Primary filing (current-year disclosure) always writes accn/source_text.
    Secondary filing (prior-year comparison) only writes if current value is NULL.
  - DISTINCT ON removed from graph_query SQL — DB constraint now guarantees uniqueness at write time.

### Weeks 9–10 (Graph Tool)
- [x] `graph_query` tool built and wired into agent
  - fiscal_year modes: latest (per-company), trend (all years), specific int, YYYY-YYYY range
  - customer+supplier both passed → filters specific pair via AND clause
  - DB constraint `(supplier, customer, fiscal_year)` guarantees uniqueness at write time
  - threshold_only flag: read from DB column, displayed as ">10%" when true
  - Returns source_text per edge for citation; traversal_trace for faithfulness eval
- [x] Agent traverses graph + combines with financials
  - Rule 6: never substitute other companies' data when asked company has no results
  - Rule 7: when question specifies a fiscal year, pass exact year to BOTH `query_financials` AND `graph_query` — never use `fiscal_year="latest"` when a specific year is given
  - Rule 8: `graph_query` data is supplier-perspective only — cannot answer customer procurement % questions (e.g. "what % of Apple's procurement is from QRVO?" is unanswerable)
  - System prompt example: "CRUS dependency on Apple" → `graph_query(supplier="CRUS")`, not `customer="CRUS"`
  - System prompt example: order-cut impact = `revenue * pct / 100 * cut` — must include the reduction factor, not just total exposure
- [x] Frontend: "Graph citations" collapsible panel — source_text per edge, separate from main answer
  - Main answer: concise facts + accession links only
  - Citations panel: supplier→customer, FY, %, accession, source_text verbatim
- [x] `threshold_only` correctly read from DB column in `tools.py` (not inferred from value)

### Weeks 10–11 (Vertical Eval + Ablation)
- [x] Tier-3 vertical eval set built — `data/eval_set_tier3.json` (8 questions, 4 types)
  - `graph_lookup`: which companies supply Apple in FY2024?
  - `graph_fact`: exact revenue_pct for a specific pair
  - `graph_trend`: CRUS→AAPL dependency across all available years
  - `graph_comparison`: which supplier has highest Apple concentration?
  - `graph_compute` (×2): dollar impact of 20% Apple order cut on QRVO / CRUS
  - `graph_compute` (ranking): rank all 3 suppliers by dollar exposure
  - `unanswerable`: Apple's procurement share from QRVO (Apple 10-K doesn't disclose)
- [x] Tier-3 harness built — `src/copilot/eval/harness_tier3.py`
  - Scoring: `_check_traversal_trace` (edge presence), numeric tolerance, LLM judge (graph_comparison)
  - `graph_compute` sub-type dispatch: `scoring="numeric"` (single expected_value) vs `scoring="llm_judge"` (ranking, expected_values dict)
  - `--no-graph` flag: strips `graph_query` from TOOL_SCHEMAS → baseline naive-RAG ablation
  - Run: `python -m copilot.eval.harness_tier3 [--no-graph] --out data/eval_results_t3_*.json`
- [x] **Baseline-RAG vs graph-augmented ablation run — delta quantified (2026-06-03)**
  - Graph-augmented: **100%** (8/8); Baseline no-graph: **12.5%** (1/8); **Delta: +87.5pp**
  - See "Tier-3 Ablation Results" table in Evaluation Harness section above
- [x] Tier-1/2 regression (v3) after Stage 2 changes — no numeric/refusal regression
  - Tier-1 100%, Tier-2 100%, Refusal 100%; retrieval 50% (down from 62.5%, explained above)

### Weeks 11–12 (Deploy + Polish)
- [ ] Frontend polish (exposure view / graph viz)
- [x] Deploy — Supabase + Render + Streamlit Community Cloud (2026-06-08)
  - OOM fixes: SKIP_PRELOAD + lazy sentence_transformers import
  - Auth: API key on /ask + APP_PASSWORD gate on frontend
  - Auto-deploy on push to main
- [ ] Scheduled ingestion running
- [x] README updated — live demo link, ablation results, deployment section (2026-06-08)
- [ ] Demo script, recorded demo

---

## Deployment (Live — 2026-06-08)

Three-service architecture. All free tiers.

| Service | Provider | URL |
|---|---|---|
| Database | Supabase (PostgreSQL + pgvector) | project `hslwxgbkbbiwhcgjgocu` |
| API | Render (Python 3, Free) | `https://financial-copilot-api-d60z.onrender.com` |
| Frontend | Streamlit Community Cloud | `https://financial-copilot.streamlit.app` |

### Environment variables per service

**Render (API):**
- `OPENAI_API_KEY` — gpt-4o-mini calls
- `DATABASE_URL` — Supabase Session Pooler URL (IPv4; direct connection is IPv6-only on free tier)
  - Format: `postgresql://postgres.hslwxgbkbbiwhcgjgocu:<pw>@aws-1-us-east-1.pooler.supabase.com:5432/postgres`
- `API_KEY` — secret key required in `X-API-Key` header for `/ask` (empty = open access)
- `SKIP_PRELOAD=1` — skips embedding model warmup at startup (prevents OOM on 512MB)
- `PYTHONUNBUFFERED=1` — log flushing

**Streamlit Community Cloud (Secrets):**
```toml
API_URL = "https://financial-copilot-api-d60z.onrender.com"
API_KEY = "<same value as Render API_KEY>"
APP_PASSWORD = "<password shown to visitors before they can use the app>"
```

### Known fixes applied (all in codebase)
1. `SKIP_PRELOAD=1` — startup event returns early before loading embedding model
2. Lazy `sentence_transformers` import in `dense.py` — PyTorch not loaded until first `retrieve_text` call; prevents OOM on Tier-1/2/3 numeric queries
3. Password gate in `frontend.py` — `APP_PASSWORD` secret required before UI is shown
4. API key auth on `/ask` — `_require_api_key` FastAPI dependency; `/health` remains public
5. Non-blocking frontend — `retrieve_text` calls run in background thread with Stop button

### Redeployment
- Push to `main` → Render and Streamlit auto-deploy (Auto-Deploy is on)
- To update DB schema: Supabase SQL editor (project dashboard → SQL Editor)
- To re-run extraction: `uv run --active python -m copilot.pipeline.extract_edges --source all` (local only)
- Free tier cold start: Render spins down after 15 min inactivity; first request ~50-60s

### Supabase migration notes
- pgvector extension must be in `public` schema: `ALTER EXTENSION vector SET SCHEMA public;`
- Session Pooler required for IPv4 access (free tier direct = IPv6 only)
- pg_restore used for initial migration from local PostgreSQL

---

## Stage 2 — Methodology Decisions (surveyed 2026-05-29)

### 1. Relation Extraction

**Problem type: Document-level RE (DocRE), not sentence-level.**
Customer concentration disclosures routinely span multiple sentences across a section
("Our largest customer is X. ... In FY2024, this customer accounted for Y%...").
Cross-sentence F1 is consistently 10–15 pts lower than intra-sentence — known hard problem.

**Methods surveyed:**

| Method | Verdict |
|---|---|
| Regex / rules | Useful as pre-filter only; brittle on phrasing variants |
| Supervised BERT-based (REBEL, ATLOP, SSAN) | ❌ Requires labeled training data — out of scope |
| Fine-tuning (AutoRE, DocRED) | ❌ No training set; violates project constraints |
| LLM single-pass schema-guided (FinReflectKG EvalBench) | ✅ Core method — highest faithfulness score in benchmark |
| LLM multi-stage / reflection (PARSE 2025) | ❌ Overkill for narrow domain |
| Constraint decoding (Instructor / Outlines) | ✅ Enforces schema compliance at decode time |
| "Relation as Prior" (2025 frontier) | Noted; not needed at this scale |

**Chosen approach: Regex pre-filter → Schema-guided LLM extraction + constraint decoding**

```
969 text_chunks
    ↓ regex: chunks containing "%" + "revenue" + company name → ~30 candidates
    ↓ LLM (gpt-4o-mini) with Instructor/structured output
    ↓ Pydantic schema validation
→ (supplier, customer, revenue_pct, fiscal_year, accn, chunk_id)
```

**Input unit: section-level chunks (Item 1 Business, Item 1A Risk Factors), not whole document.**
Controls prompt size; customer concentration disclosures are concentrated in these sections.

**Validation: hand-labeled golden set (1-2 filings) for regression testing.**
Known ground truth: QRVO→AAPL 46%, SWKS→AAPL 59%, CRUS→AAPL ~85%.
Re-run extraction regression on every prompt/model change.

**Key papers:**
- REBEL (Cabot & Navigli, EMNLP 2021) — seq2seq extraction baseline
- FinReflectKG EvalBench (Arun et al. 2025) — direct reference; single-pass chosen for highest faithfulness score in multi-dimensional benchmark
- PARSE (2025) — schema-guided reflection mechanism (noted, not adopted)
- AutoRE (Xue et al. 2024) — modular DocRE, current SOTA direction

---

### 2. Graph Storage

**Methods surveyed:**

| Method | Verdict |
|---|---|
| RDF triple store (Jena, Virtuoso) | ❌ Academic standard; industry use low; SPARQL overhead |
| Neo4j / TigerGraph (native LPG) | ❌ Overkill — new service, new query language, new ops burden |
| Apache AGE (Postgres + Cypher) | ❌ Known write-performance issues at 10K+ edges (MERGE slowdowns); extra learning curve |
| NetworkX (in-memory) | ✅ Visualization only |
| **Pure SQL edge table + recursive CTE** | ✅ **Chosen** |

**Chosen approach: PostgreSQL edge table**

```sql
CREATE TABLE supply_edges (
    id              SERIAL PRIMARY KEY,
    supplier_ticker TEXT NOT NULL,
    customer_ticker TEXT NOT NULL,
    revenue_pct     FLOAT,
    fiscal_year     INT,
    accn            TEXT,          -- SEC filing accession for citation
    chunk_id        INT,           -- FK to text_chunks for traceability
    extracted_at    TIMESTAMP DEFAULT NOW()
);
```

Multi-hop traversal via `WITH RECURSIVE` SQL — sufficient for 2-3 hops across 6 companies.

**Why not Neo4j / Apache AGE (principled rejection):**
Graph has ~tens to low hundreds of edges (6 companies × 3 years). Recursive CTE handles
2-3 hop queries trivially. Neo4j adds a new service, new query language (Cypher), and new
ops burden with zero marginal benefit at this scale. AGE has documented write-performance
degradation at 10K+ edges. Keeping everything in one Postgres instance preserves the
audit story: financial_facts + text_chunks + supply_edges are all transactionally consistent,
share one backup, and citation chains stay unified.

NetworkX used only for graph visualization in README/demo (read edges table, draw, export PNG).

**Apache AGE vs Neo4j — architectural difference:**
Apache AGE is a PostgreSQL extension that adds Cypher syntax on top of PostgreSQL's
row-based storage. It is NOT architecturally equivalent to Neo4j:
- Neo4j uses **index-free adjacency**: each node stores physical pointers to neighbors,
  multi-hop traversal is O(1) per hop regardless of graph size.
- Apache AGE translates Cypher into SQL JOINs internally. Each hop is still a table scan.
  It is a syntax-layer graph, not a storage-layer graph.
AGE's appeal is "write Cypher without changing databases." Its implementation quality
does not match this promise — MERGE performance is the visible symptom of the deeper
architectural mismatch.

**Scale thresholds for storage migration:**
| Scale | Nodes | Recommendation |
|---|---|---|
| Current | 6 | SQL edge table ✅ |
| Expanded cluster | 50–100 | SQL still sufficient |
| Industry-wide | 500+ | SQL starts to struggle for complex multi-hop |
| Full market | 10,000+ | Neo4j necessary |

**Future expansion directions (post Stage 2):**
Three dimensions for dataset growth:
1. **Horizontal** — more companies: broader Apple ecosystem (TSMC, Foxconn require 20-F
   support) or new hub (NVIDIA AI supply chain, automotive)
2. **Vertical** — multi-tier: supplier's suppliers (2-hop relationships), forming true
   multi-layer supply chain graph
3. **Temporal** — more years: extend from 3–4 years to 10 years to analyze how
   supply-chain dependencies evolve over time

---

### 3. Graph Reasoning

**Methods surveyed:**

| Method | Verdict |
|---|---|
| KG embedding (TransE, RotatE, RGCN) | ❌ Link prediction breaks honest-refusal principle |
| GNN-based reasoning (GCN, GAT) | ❌ Training required; overkill for 6 nodes |
| Neurosymbolic | ❌ Out of scope |
| Symbolic SQL query | ✅ For all numeric/traversal steps |
| **LLM agent + graph_query tool (GraphRAG)** | ✅ **Chosen — current design is already on frontier** |

**Chosen approach: Agent + `graph_query` tool (GraphRAG pattern)**

Current agent design is already the GraphRAG paradigm (Edge et al., Microsoft 2024).
Two targeted enhancements borrowed from the literature:

**Enhancement 1 — StepChain-inspired problem decomposition (StepChain GraphRAG 2025):**
Agent must decompose Tier-3 questions into explicit sub-steps before querying:
```
Q: "Which suppliers are most exposed if Apple cuts orders 20%?"
Step 1: graph_query → list all direct AAPL suppliers
Step 2: query_financials × N → each supplier's revenue
Step 3: multiply by exposure_pct from supply_edges
Step 4: compute impact per supplier
Step 5: rank and cite each edge's source filing
```
Converts single opaque graph call into auditable reasoning chain.

**Enhancement 2 — Traversal trace in agent output (for eval faithfulness):**
Every `graph_query` response includes the edges traversed and their citations.
Agent is required to surface this trace in its final answer.
Eval harness checks traversal trace the same way it checks `tool_trace` for Tier-1/2.

**Key papers:**
- GraphRAG (Edge et al., Microsoft 2024, arXiv 2404.16130) — defines this generation of methods
- StepChain GraphRAG (2025) — sub-question decomposition + BFS traversal
- Inference-Scaled GraphRAG (2025) — sequential/parallel scaling at inference time (noted)
- Survey: "LLMs Meet KGs for QA" (arXiv 2505.20099) — landscape map

---

## Stage 2 — Extraction Results (2026-06-11, research cluster)

### supply_edges table — current state (updated 2026-06-11)

**Two extraction pipelines, both write to supply_edges:**
- `--source chunks`: from text_chunks (Business/Risk Factors sections already in DB)
- `--source html`: direct HTML parsing of Item 8 Financial Notes from EDGAR
- Rerun command: `$env:PYTHONUTF8="1"; uv run --active python -m copilot.pipeline.extract_edges --source all`

**DB state after full extraction (chunks + HTML, 2026-06-11):**
~130 edges across 13 suppliers. HTML source added Item 8 EMS sub-supplier edges for CRUS (Pegatron, Foxconn/Hongfujin, Jabil Circuits, Protek) and smartphone OEM edges for QCOM (Samsung, OPPO, vivo).

**Post-extraction data quality cleanup applied (2026-06-11):**
- Deleted sub-10% edges (QRVO→Huawei FY2018 8%, QRVO→Samsung FY2021 7%) — below ASC 280 threshold
- Deleted aggregate/channel edges (AVGO "top 5 customers" / "distributors", ON/ADI "10 largest") — not single-entity disclosures
- Deleted inferred duplicates (APH "largest customer" FY2020 = named AAPL same filing)
- Deduplicated Huawei aliases (Huawei / HWT / Huawei Technologies Co., Ltd.) → kept canonical name
- Set `threshold_only=TRUE` on APH FY2022-2025 "10% or more" language
- **HTML extraction cleanup complete (2026-06-11)**:
  - AVGO pre-2018 rows deleted (entity limitation: CIK created 2018)
  - AVGO Foxconn 9% FY2018 deleted (sub-10%)
  - QRVO Huawei aliases deduplicated (HWT / Huawei Technologies Co., Ltd. → "Huawei")
  - AVGO WT Microelectronics Co., Ltd. → "WT Microelectronics" (normalized)
  - QCOM OPPO/VIVO/Foxconn customer names normalized
  - TXN geographic revenue row (China 20%) deleted — false positive, not ASC 280
  - SWKS→AAPL FY2016-2018 upgraded to exact values (40%/39%/47%) from Item 8 notes
  - **Code fixes in extract_edges.py**: `_should_skip_edge()` added — auto-rejects sub-10%, geographic revenue (China-based etc.), aggregate groups (top-N, distributors); `CUSTOMER_ALIASES` extended with Huawei/WT Micro/OPPO/VIVO/Foxconn/Arrow variants; `_SYSTEM_PROMPT` updated with explicit DO NOT EXTRACT rules
  - **Final state: 110 rows, 29 (supplier,customer) pairs**

**Named Apple edges confirmed (WRDS-validated where available):**

| Supplier | FY Range | Apple % range | Status |
|---|---|---|---|
| CRUS | FY2017–2026 | 79%–91% | ✅ WRDS exact |
| QRVO | FY2016–2026 | 30%–50% | ✅ WRDS exact (FY2024) |
| JBL | FY2020–2024 | 11%–22% | Named, not WRDS-validated |
| AVGO | FY2020–2023 | 15%–20% | ✅ WRDS exact |
| ADI | FY2016–2017 | 12%–14% | Named; disappears post-LT merger |
| APH | FY2018, FY2020 | 11%–12% | Named; intermittent |
| SWKS | FY2021–2025 | >10% threshold | ⚠️ real ~69% per WRDS |

**Other notable edges from HTML source:**
- CRUS→EMS partners (Pegatron, Hongfujin/Foxconn, Jabil Circuits, Protek) — Apple assemblers appear in CRUS concentration notes as indirect customer chain
- QCOM→AAPL: confirmed named Apple disclosure in modem/chip business (pre-dispute years)
- QCOM→Samsung/OPPO/vivo: smartphone OEM customers in QCT segment
- ON→unnamed distributor: single distributor 10–13% FY2020-2025 (likely automotive channel)
- MCHP→Arrow Electronics: named distributor 10–12% FY2024-2026
- LRCX→unnamed: 45–52% FY2014-2016 (likely Samsung memory fabs; not Apple chain)

**Tier-1 negative confirmation (no named single-customer >10% in any year):**
TXN, SANM, GLW (threshold-only unnamed), LRCX (unnamed only)

### Company tier reclassification based on extraction results

| Company | Original tier | Revised tier | Evidence |
|---|---|---|---|
| JBL | Tier-2 EMS | **Tier-1 positive** | Consistent Apple named disclosure FY2020-2024 |
| ADI | Tier-1 negative | **Hybrid** | Apple 12-14% FY2016-2017; gone post-LT merger |
| APH | Tier-2 connector | **Intermittent positive** | Named Apple FY2018, FY2020; not other years |
| QCOM | Tier-1 negative | **Confirmed negative** | No customer concentration disclosure found in any year (segment revenues, not customer %) |

### Disclosure patterns discovered

**Item 1 Business section — named, precise:**
```
"Apple Inc. ('Apple')...accounted for 46% and 37% of total revenue"  ← QRVO style, uses %
"Apple, Inc....represented approximately 87 percent of total sales"   ← CRUS style, uses "percent"
"Apple, Inc...accounted for approximately 20% of net revenue" ← AVGO style
```

**Item 1A Risk Factors — unnamed, aggregate:**
```
"our two largest customers accounted for approximately 58% of our net revenue"
```

**Item 8 Financial Notes — named, precise (CRUS):**
Same text as Business section, but this is where CRUS puts the specific Apple %.

Named disclosures always in Business or Financial Notes. Risk Factors always unnamed.
Two regex forms needed: `%` symbol AND text-form "percent" — companies are inconsistent.

### Known data gaps

- **SWKS**: Both text and HTML say "more than ten percent" — exact ~69% is available only
  in WRDS Compustat (third-party paid database). EDGAR XBRL contains no ConcentrationRisk
  tags for any company in our cluster (confirmed). Stored as 10.0% threshold disclosure.
- **GLW**: Discloses unnamed threshold-only concentration — Corning never names Apple even though it is the principal Gorilla Glass supplier. Cannot verify via EDGAR alone.
- **QCOM**: No customer concentration disclosure found in any year — QCOM discloses segment revenues (QCT/QTL) but not single-customer %. Apple dispute FY2017-2019 is documented in MD&A text, not as a concentration disclosure.

### Entity disambiguation — current approach and limitations

**Three-layer normalization:**

1. **Pydantic validator** (`resolve_ticker`) — resolves `customer_ticker` field against
   `CUSTOMER_ALIASES` dict at parse time
2. **`_normalize_customer()`** — fallback: if ticker field empty, tries `customer_name` field
3. **Post-hoc SQL cleanup** (`normalize_edges.py`) — handles aliases discovered after extraction

**`CUSTOMER_ALIASES` (hardcoded):**
```python
"apple" / "apple inc" / "apple inc." → "AAPL"
"samsung" / "samsung electronics co., ltd." → "005930.KS"
```

**Known limitation — exact string matching only:**
New aliases not in the dict pass through unresolved. Examples that would fail:
- `"Apple Computer"` → stored as `"APPLE COMPUTER"`
- `"the Cupertino company"` → unresolvable
- `"AAPL Inc."` → not in aliases

**Mitigation for current scope:** 10-K language is formulaic; the 15-company cluster uses
consistent naming. The dict covers all observed variants. If cluster expands, fix is to
constrain LLM output to a predefined ticker list in the system prompt.

**Cost note:** Full research-cluster extraction (~219 candidates, chunks source) cost ~$0.04
(171K input + 26K output tokens, gpt-4o-mini at $0.15/$0.60 per 1M). One-time batch job.

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

---

## Research Branch — SC-DisclosureQA (2026-06-09)

> **Branch: `research`** — Experimental research track. Does NOT affect `main` / deployed prototype.

### Research Question

**Core claim (one sentence):**
In structured financial QA systems, locking the numeric channel to SQL/XBRL causes hallucination to migrate toward qualitative retrieval and cross-entity relational reasoning; graph augmentation selectively suppresses the relational error layer; the three error types are architecturally separable.

**Three sub-questions:**
1. How much numeric hallucination does SQL-locking eliminate?
2. Do residual errors concentrate in retrieval and graph reasoning?
3. Does evidence trace improve verifiability of cross-entity claims?

---

### Industry & Company Cluster

**Industry: Apple semiconductor supply chain (ASC 280 customer concentration disclosures)**

| Role | Companies | Rationale |
|---|---|---|
| Hub | AAPL | Customer cited in supplier 10-Ks |
| Tier-1 positive — consistent Apple disclosure | CRUS, QRVO, SWKS, AVGO, JBL | Apple >10% revenue, named, EDGAR-verifiable across multiple years |
| Tier-1 positive — historical only | ADI (FY2016-2017), APH (FY2018/2020), QCOM (pre-2017 QCT) | Apple concentration documented but no longer active or not directly disclosed |
| Tier-1 negative (no named customer >10%) | GLW, TXN, MCHP, ON, LRCX | Tests whether system hallucinates non-existent edges |
| Tier-2 EMS / connector | APH, SANM | Structural supply chain role; Apple concentration intermittent or inferred |

**Extraction-confirmed updates vs. original design (2026-06-11):**
- JBL reclassified Tier-2 → **Tier-1 positive**: named Apple disclosure FY2020-2024 (11-22%)
- ADI reclassified Tier-1 negative → **hybrid**: had Apple 12-14% in FY2016-2017, disappeared after Linear Technology merger
- APH: intermittent Apple >10% (FY2018, FY2020 only) — stays Tier-2 but with known positive years
- QCOM: no customer concentration disclosure found in any year — QCOM reports segment revenues, not customer %; classified Tier-1 negative for graph layer purposes
- NXPI/FLEX replaced by LRCX/SANM (foreign private issuers excluded)

**Why this industry — four principled reasons (in order of importance):**

1. **ASC 280 legal mandate produces ground truth automatically.**
   US securities law requires any company to name and quantify any single customer that
   accounts for >10% of revenue in their 10-K filing. Ground truth is not hand-labeled —
   it is legally mandated. This does not hold in other jurisdictions or industries.

2. **US-listed companies file 10-K + XBRL → machine-readable, structured data.**
   The constraint is not "suppliers are American" but "EDGAR/XBRL data is available."
   Causality: we need ASC 280 disclosures → we need 10-K filers → we exclude foreign
   suppliers (TSMC, Foxconn file 20-F; Samsung files 20-F equivalent). TSMC is Apple's
   largest fab supplier but is out of scope precisely because it cannot provide the same
   structured, verifiable disclosure.

3. **Apple is too large to anonymize — disclosures are named and precise.**
   ASC 280 technically allows "a significant customer" without naming them. In practice,
   when that customer is the world's largest company by market cap, suppliers name Apple
   explicitly (investors would demand it). This guarantees the ground truth is attributed
   (supplier → AAPL, not supplier → "major customer") and numerically precise.
   QCOM is additionally valuable: its Apple relationship changed dramatically from 2017
   (dispute, revenue fell to near zero) → 2019 (settlement) → 2023+ (self-design displacement),
   providing a temporally dynamic edge for trend reasoning questions.

4. **Dependency concentration gradient, not scatter — enables dataset diversity.**
   The supply chain is NOT "distributed." It is highly concentrated around Apple.
   What creates dataset diversity is the *gradient of dependency levels*:
   CRUS (~90%) → QRVO (~46%) → AVGO (~20%) → SWKS (>10%, threshold only) → GLW (no disclosure).
   This gradient supports T1–T5 question variety and makes the negative samples
   (GLW/TXN/ADI) a principled test of whether the system hallucinates non-existent edges.

**Graph direction convention:** supplier → customer (revenue_pct = % of *supplier's* revenue from that customer)

**Supply-chain depth:**
- Primary: 1-hop (Supplier → AAPL)
- Extension: 2-hop structural inference only (Sub-supplier → Supplier → AAPL), no 2-hop text ground truth required
- Out of scope: 3-hop

---

### Dataset Design — SC-DisclosureQA Benchmark (target: 150 questions)

| Type | Count | Example | Ground truth source |
|---|---|---|---|
| **T1 SQL numeric** | 30 | "CRUS FY2023 Revenue?" | XBRL financial_facts |
| **T2 SQL + compute** | 30 | "QRVO Apple exposure × Revenue = dollar amount" | XBRL + compute |
| **T3 Qualitative retrieval** | 25 | "How does SWKS describe customer concentration risk?" | text_chunks key phrase |
| **T4 Graph relation** | 30 | "Which companies supply Apple in FY2024? At what %?" | supply_edges |
| **T5 Cross-entity reasoning** | 20 | "20% Apple order cut: CRUS vs QRVO — who loses more in dollars?" | graph + compute |
| **T6 Unanswerable / refusal** | 15 | "What % of Apple's procurement comes from QRVO?" | Customer-side = no disclosure → must refuse |

**Dataset special properties:**
- Every T4/T5 question has a `traversal_ground_truth` field (required edges that must appear in the answer's reasoning trace)
- T6 includes two sub-types: true data gaps AND direction-reversed questions (customer-side queries)
- Time range: FY2020–FY2024 (5 years), enabling trend questions

**Build process:** `python -m copilot.eval.build_sc_eval` — to be implemented

---

### System Conditions (Ablation Design)

```
Condition A — Naive RAG
  User → LLM + retrieve_text only → Answer
  (no SQL tools, no graph)

Condition B — SQL-locked RAG
  User → Agent → query_financials / compute / retrieve_text → Answer
  (numeric forced through SQL, no graph)

Condition C — SQL + Graph + Evidence Trace  [this system]
  User → Agent → query_financials / compute / retrieve_text / graph_query → Answer
  (full system; graph provides relations; evidence trace provides citation chain)

Condition D — SQL + Graph, no Evidence Trace  [optional ablation]
  Isolates the contribution of citation/grounding mechanism
```

**Verification logic for core claim:**
```
If A → B: numeric_accuracy ↑, relation_accuracy ≈ flat  → SQL isolates numeric layer
If B → C: relation_accuracy ↑, numeric_accuracy ≈ flat  → graph isolates relational layer
→ Three error types are architecturally separable (error isolation thesis)
```

---

### Evaluation Framework

Each question scored on five dimensions:

| Dimension | Measurement |
|---|---|
| Numeric accuracy | `_within_tolerance()` ±2%, XBRL-verified |
| Relation accuracy | `traversal_trace` contains required edges |
| Retrieval hit | key phrase present in `retrieve_text` output |
| Refusal accuracy | refusal keyword detection |
| Citation verifiability | accession number resolves to real SEC document |

Harness: `src/copilot/eval/harness_sc.py` — to be implemented

---

### Expected Contributions

1. **SC-DisclosureQA benchmark** — first supply-chain QA dataset grounded in ASC 280 mandatory disclosures with EDGAR-verifiable ground truth (150 questions, 6 types, 15 companies, FY2020–2024)
2. **Error migration evidence** — quantified ablation showing SQL-locking and graph augmentation selectively suppress distinct hallucination types
3. **Evidence trace design** — `graph_query` returns `traversal_trace` + `source_text` per edge; enables citation-level verification of cross-entity claims
4. **Finance-domain GraphRAG instantiation** — domain-specific application of Edge et al. (2024) GraphRAG with EDGAR ground truth (vs. synthetic data in prior work)

---

### Target Venue

| Priority | Venue | Notes |
|---|---|---|
| Primary | EMNLP / ACL 2027 Findings | NLP + finance domain, fits system paper track |
| Backup | AAAI 2027 / FinNLP workshop (KDD/IJCAI satellite) | Finance-specific audience |
| arXiv preprint | 2026 Q4 | Establish priority before submission |

---

### Execution Roadmap

```
Current (already built on main)
  ├── 6-company XBRL + text_chunks + supply_edges ✅
  ├── Agent + 5 tools (query_financials, compute, retrieve_text, graph_query, list_metrics) ✅
  ├── Eval harness (Tier 1/2/3/refusal) ✅
  └── Tier-3 ablation: graph vs no-graph, +87.5pp delta ✅

Phase 1 — Data expansion (2–4 weeks)
  ├── Expand to 15 companies: add QCOM, ADI, TXN, MCHP, ON, NXPI, APH, JBL, FLEX
  ├── XBRL ingestion: 10 years (FY2015–FY2024) per company
  ├── text_chunks: ingest + embed (~50,000 chunks total)
  ├── supply_edges: re-run extraction, manually verify golden set per company
  └── Validate: WRDS cross-check where available

Phase 2 — Benchmark construction (2–3 weeks)
  ├── Implement build_sc_eval.py (150 questions + human review)
  ├── Label traversal_ground_truth per T4/T5 question
  ├── Verify T6 unanswerable boundaries (direction-reversed vs. true gaps)
  └── Lock dataset (no changes after first eval run)

Phase 3 — Experiments (2–3 weeks)
  ├── Run Condition A (Naive RAG: disable SQL tools, retrieve_text only)
  ├── Run Condition B (SQL-locked: no graph_query)
  ├── Run Condition C (full system)
  ├── Optional: Condition D (no evidence trace)
  └── Per-type error breakdown → error migration analysis

Phase 4 — Writing (ongoing)
  ├── Core results table + error migration figure
  ├── Case studies: CRUS vs QRVO order-cut comparison
  └── arXiv draft → venue submission
```

---

### Research Branch — Pending Work (as of 2026-06-11)

**Phase 1 data expansion — status:**
- [x] XBRL ingestion: 42,244 facts, 15 companies, 20 metrics
- [x] Text ingestion: 7,444 chunks (all embedded), 148 filings, 10yr per company (AVGO 8yr, entity limit)
- [x] EDGAR pagination fix + LRCX heading regex fix → all companies re-ingested
- [x] supply_edges extraction: chunks source complete; HTML source complete
- [x] **Final cleanup pass on supply_edges after HTML extraction** — 110 rows, 29 pairs (2026-06-11)
- [x] Add `CUSTOMER_ALIASES` + `_should_skip_edge()` to extract_edges.py — future re-extractions auto-reject geographic/aggregate/sub-10% false positives

**Phase 2 benchmark construction — not started:**
- [ ] Implement `build_sc_eval.py` — 150 questions, 6 types (T1–T6)
- [ ] Label `traversal_ground_truth` per T4/T5 question (required supply_edges rows must appear in answer trace)
- [ ] Verify T6 unanswerable boundaries (direction-reversed vs. true data gaps)
- [ ] Lock dataset (freeze after first eval run)
- Suggested starting point: generate T1/T2 questions from financial_facts (automated), T4 questions from confirmed supply_edges, T6 questions from confirmed negative companies (TXN/GLW/etc.)

**Phase 3 experiments — not started:**
- Condition A: Naive RAG (disable SQL + graph tools, retrieve_text only)
- Condition B: SQL-locked (no graph_query)
- Condition C: Full system
- Harness to implement: `src/copilot/eval/harness_sc.py`

### Research Branch — Key Decisions Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-06-09 | Use Apple supply-chain cluster as primary domain | ASC 280 ground truth, hub-spoke clarity, economic relevance |
| 2026-06-09 | 2-hop = structural inference only, no text ground truth required | EDGAR sub-supplier disclosures don't reveal end-customer chains |
| 2026-06-09 | Negative samples (GLW/TXN/etc.) test hallucinated-edge detection | Most interesting failure mode for graph layer |
| 2026-06-09 | Error migration as core claim, not accuracy leaderboard | Mechanistic claim is harder to challenge than benchmark score |
| 2026-06-11 | JBL reclassified Tier-2→Tier-1 positive | Extraction confirmed Apple named 11-22% FY2020-2024 |
| 2026-06-11 | ADI classified as hybrid (Tier-1 positive FY2016-17, negative after) | Apple disappears post-LT merger — temporal edge case |
| 2026-06-11 | QCOM classified Tier-1 negative | No customer concentration disclosure found; QCT reports segment not customer % |
| 2026-06-11 | EDGAR pagination fix applied to ingest_text.py | GLW/QRVO/QCOM/ADI/MCHP/JBL all extended to 10yr |
