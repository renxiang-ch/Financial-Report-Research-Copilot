# Financial-Report Research Copilot

An end-to-end, evaluation-driven agentic system that answers analyst-style questions over SEC 10-K filings with **verifiable numbers and citations**, extended with **supply-chain dependency reasoning** as the vertical differentiator.

**Live demo:** [financial-copilot.streamlit.app](https://financial-copilot.streamlit.app) *(password-protected; contact for access)*

---

## What It Does

- Answers Tier 1–3 financial questions over 6 Apple-cluster companies (AAPL, SWKS, QRVO, CRUS, GLW, AVGO)
- Numbers are fetched from XBRL structured data and verified against SEC filings — **never computed by the LLM**
- Supply-chain dependency reasoning: "Which suppliers are most exposed if Apple cuts orders 20%?"
- Correctly refuses unanswerable questions ("cannot determine from available data")

---

## Evaluation Results

### Tier 1–2 Benchmark (33 questions)

| Metric | Score |
|---|---|
| Tier-1 numeric (direct XBRL lookup) | **100%** (10/10) |
| Tier-2 numeric (ratios & YoY) | **100%** (10/10) |
| Refusal (unanswerable questions) | **100%** (5/5) |
| Retrieval qualitative | 62.5% (5/8) |
| **Overall** | **90.9%** |
| Cost per run | $0.018 / 33 questions |
| Avg latency | 3.26s / question |

### Tier-3 Supply-Chain Ablation (8 questions)

| | Graph-augmented | Baseline (no graph) | Delta |
|---|---|---|---|
| **Overall accuracy** | **100%** (8/8) | 12.5% (1/8) | **+87.5pp** |
| Avg latency | 5.45s | 10.33s | faster |
| Cost / run | $0.008 | $0.024 | cheaper |

The +87.5pp gap is the graph layer's contribution. Baseline naive-RAG cannot answer supply-chain exposure questions — it fails by reversing Apple's revenue or refusing outright.

---

## Architecture

```
User → Streamlit → FastAPI → Agent (gpt-4o-mini)
                                   ↓           ↓            ↓
                            PostgreSQL      pgvector     supply_edges
                            (XBRL facts)  (embeddings)  (Stage 2 graph)
```

**Deployed:** Supabase (DB) + Render (API) + Streamlit Community Cloud (frontend)

### Agent Tools

| Tool | Purpose |
|---|---|
| `query_financials(ticker, metric, year)` | Fetch XBRL number from PostgreSQL |
| `list_metrics(ticker)` | Discover available metrics and year ranges |
| `compute(expression, variables)` | Sandboxed arithmetic — LLM never computes |
| `retrieve_text(query, ticker)` | Hybrid BM25 + dense retrieval (RRF) over 10-K text |
| `graph_query(...)` | Supply-chain graph traversal via recursive CTE |

**Cardinal rule:** Numbers come from SQL and `compute`, never from LLM memory or inference.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Ingestion | EDGAR REST API + XBRL companyfacts/frames API |
| Storage | PostgreSQL + pgvector 0.8.2 |
| Embeddings | `BAAI/bge-small-en-v1.5` via sentence-transformers (local, 384 dims) |
| Retrieval | Hybrid BM25 + pgvector dense, fused with RRF (k=60) |
| LLM | gpt-4o-mini via OpenAI API |
| Agent | Hand-written ReAct loop, 5 tools, multi-step system prompt |
| Eval | Custom harness (numeric + citation + LLM-judge) |
| Graph | PostgreSQL edge table + recursive CTE (no Neo4j) |
| API | FastAPI |
| Frontend | Streamlit |
| Deploy | Render (API) + Streamlit Community Cloud (frontend) + Supabase (DB) |

---

## Local Setup

```powershell
# Clone and install
git clone https://github.com/renxiang-ch/Financial-Report-Research-Copilot
cd Financial-Report-Research-Copilot
uv sync

# Configure
cp .env.example .env  # fill in OPENAI_API_KEY and DATABASE_URL

# Start (Windows)
.\start.ps1          # FastAPI on :8000, Streamlit on :8501

# Run eval
$env:PYTHONUTF8="1"; uv run --active python -m copilot.eval.harness --out data/eval_results.json
```

---

## Stage 2 — Supply-Chain Graph

### Why This Architecture

**Storage:** Pure SQL edge table in PostgreSQL. Multi-hop traversal via `WITH RECURSIVE`. No Neo4j or Apache AGE — the graph has ~40 edges across 6 companies; recursive CTE handles 2–3 hop queries trivially. All tables (financial_facts, text_chunks, supply_edges) share one Postgres instance, preserving transactional consistency and a unified audit trail.

**Extraction:** Regex pre-filter → schema-guided LLM extraction (gpt-4o-mini + Instructor) → Pydantic validation. Disclosure patterns found:

```
Item 1 Business:   "Apple Inc. accounted for 46% of total revenue"   ← named, exact
Item 1A Risk:      "our two largest customers accounted for 58%"     ← unnamed, aggregate
Item 8 Notes:      "Apple Inc. represented approximately 87 percent" ← named, text-form %
```

**Validation against WRDS Compustat:** QRVO→AAPL 46% FY2024 ✓, CRUS→AAPL 87% FY2024 ✓, AVGO→AAPL 20% ✓

### Foundational References

| Reference | Role |
|---|---|
| ASC 280-10-50-42 | Defines the 10% disclosure obligation — sets the scope of extractable edges |
| WRDS Supply Chain (Compustat Segment) | External validation ground truth for extracted edges |
| FinReflectKG EvalBench (Arun et al., ACM ICAIF 2025) | Schema-guided single-pass extraction method |
| Cohen & Frazzini (2008), *J. Finance* | Establishes that supply-chain disclosures contain pricing-relevant information |
| GraphRAG (Edge et al., Microsoft 2024) | Paradigm for the agent + graph_query tool design |

---

## Project Constraints

- No LLM fine-tuning or training
- No PDF table extraction (XBRL bypasses it entirely)
- No investment advice framing
- Honest refusal over fabrication — "cannot determine" when evidence is absent
- Numbers from SQL + `compute` only — LLM never performs arithmetic
