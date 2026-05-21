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
User → Frontend (Next.js) → FastAPI → Agent Orchestration Layer
                                            ↓           ↓           ↓
                                    Postgres (XBRL)  pgvector   Edge table (Stage 2)
                                            ↑
                                    Data Pipeline (EDGAR ETL)
```

### Agent Tools
- `query_financials(company, metric, period)` → Postgres XBRL numbers
- `compute(expr)` → code execution, never model-computed
- `retrieve_text(query, company)` → hybrid retrieval + rerank
- `graph_query(...)` → graph traversal (Stage 2 only)

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python |
| Ingestion | EDGAR REST API + XBRL companyfacts/frames API |
| Storage | PostgreSQL + pgvector |
| Embeddings | text-embedding-3 or bge/e5 |
| Retrieval | Hybrid BM25 + dense + reranker (good enough, not deep) |
| LLM | Claude or GPT via API |
| Agent | Hand-written loop (preferred) or LangGraph |
| Eval / Observability | Custom harness + Langfuse |
| Graph (Stage 2) | Postgres edge table + SQL (no Neo4j unless learning graph DBs) |
| API | FastAPI |
| Frontend | Next.js / React (fallback: Streamlit) |
| Deploy | Docker + Render/Railway/Fly |

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
- [ ] Repo, Python env, CI skeleton
- [ ] EDGAR API + XBRL companyfacts API working
- [ ] Lock company cluster

### Weeks 1–2 (Walking Skeleton)
- [ ] Ingest one company, one 10-K (body + XBRL)
- [ ] Postgres + pgvector set up
- [ ] Naive single-doc RAG answers one Tier-1 question
- [ ] Minimal FastAPI + minimal frontend (answer + one citation)

### Weeks 3–4 (Data Pipeline)
- [ ] Batch-ingest full cluster; incremental updates, dedup, idempotency
- [ ] Normalize XBRL financials into SQL
- [ ] Hybrid retrieval (BM25 + dense) + rerank
- [ ] Tier-1 eval runs automatically

### Weeks 5–7 (Agent + Tools — Signature 1)
- [ ] Query decomposition / planning loop
- [ ] Tools: `query_financials` / `compute` / `retrieve_text`
- [ ] Cross-doc synthesis + citation tracking
- [ ] Tier-2 working; "numbers from data only" enforced

### Weeks 6–8 (Eval & Observability — Signature 2)
- [ ] Eval harness + Tier 1–2 set built
- [ ] Auto-scoring + traces + cost/latency
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
