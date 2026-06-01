# Financial-Report Research Copilot

An end-to-end, evaluation-driven agentic system that answers analyst-style questions over SEC 10-K filings with **verifiable numbers and citations**, extended with **supply-chain dependency reasoning** as the vertical differentiator.

---

## Stage 1 — RAG Financial QA (Complete)

Agentic copilot that answers Tier 1–2 financial questions over 6 Apple-cluster companies (AAPL, SWKS, QRVO, CRUS, GLW, AVGO). Numbers come from XBRL structured data, never from LLM computation.

**Stack:** PostgreSQL + pgvector · BAAI/bge-small-en-v1.5 embeddings · Hybrid BM25 + dense retrieval (RRF) · gpt-4o-mini agent · FastAPI · Streamlit

**Eval results (33-question benchmark):**

| Metric | Score |
|---|---|
| Tier-1 numeric (direct XBRL lookup) | 100% |
| Tier-2 numeric (ratios & YoY) | 100% |
| Refusal (unanswerable questions) | 100% |
| Retrieval qualitative | 62.5% |
| **Overall** | **90.9%** |

**Run locally (Windows):**
```powershell
.\start.ps1          # starts FastAPI (8000) + Streamlit (8501)
```

**Run eval:**
```powershell
$env:PYTHONUTF8="1"; uv run --active python -m copilot.eval.harness --out data/eval_results.json
```

---

## Stage 2 — Supply-Chain Knowledge Graph

Builds a verifiable supply-chain dependency graph from SEC disclosures, enabling Tier-3 cross-company exposure reasoning ("Which suppliers are most exposed if Apple cuts orders 20%?").

### Foundational References

Stage 2 rests on four references, each answering a distinct design question.

---

#### 1. ASC 280-10-50-42 — *What we extract*

FASB Accounting Standards Codification, Topic 280 (Segment Reporting), §50-42. US public companies must disclose any single external customer whose revenue equals or exceeds **10% of total revenue**, including the amount and reporting segment.

Key implications for this project:

- **Naming is optional under ASC 280** — Regulation S-K Item 101(c) creates a separate naming obligation in the Business section. This is why our edge schema carries a `disclosure_status` field: `named | inferred | unnamed`.
- **Only supplier→customer direction is extractable.** Hubs like Apple have no symmetric disclosure obligation — they are not required to name their suppliers. The graph is therefore directed and asymmetric by regulatory design.
- Subsidiaries under common control count as one customer; each level of government counts as one customer.

> Deloitte DART roadmap — [ASC 280-10-50-42](https://dart.deloitte.com/USDART/home/codification/presentation/asc280-10/roadmap-segment-reporting/chapter-5-entity-wide-disclosures/5-7-information-about-major-customers)

---

#### 2. WRDS Supply Chain — *How we validate extraction*

The Wharton Research Data Services supply chain linking dataset, derived from Compustat Segment data on ASC 280 disclosures. Entity disambiguation (GVKEY/PERMNO matching) is pre-resolved.

Used as **external validation ground truth**: every edge we extract from 10-K text should appear in WRDS Supply Chain for the same period. Gaps in either direction require explanation.

Known ground truth for our cluster: QRVO→AAPL 46% (FY2024), SWKS→AAPL 59% (FY2024), CRUS→AAPL ~85% (FY2024).

> [WRDS Linking Suite — Supply Chain with IDs](https://wrds-www.wharton.upenn.edu/pages/grid-items/linking-suite-wrds/) (institutional subscription)

---

#### 3. FinReflectKG — EvalBench (Arun et al., ACM ICAIF 2025) — *How we extract*

"FinReflectKG -- EvalBench: Benchmarking Financial KG with Multi-Dimensional Evaluation." Schema-guided knowledge graph construction from S&P 100 10-K filings using LLMs. Benchmarks three extraction modes — single-pass, multi-pass, and reflection-agent — across faithfulness, precision, relevance, and comprehensiveness.

Our extraction pipeline adopts **schema-guided single-pass**, narrowed to customer-concentration relationships:

```
969 text_chunks
    ↓ regex pre-filter: "%" + "revenue" + company name → ~30 candidate chunks
    ↓ Section-level input (Item 1 Business, Item 1A Risk Factors only)
    ↓ gpt-4o-mini with Instructor structured output + Pydantic schema validation
→ (supplier_ticker, customer_ticker, revenue_pct, fiscal_year, accn, chunk_id)
```

Reflection mode's overhead is not justified given the narrow domain and formulaic disclosure language. A hand-labeled golden set (1–2 filings) is used for regression testing on every prompt change.

> [FinReflectKG — Google Scholar](https://scholar.google.com/scholar?q=FinReflectKG+Agentic+Construction+Evaluation+Financial+Knowledge+Graphs)

---

#### 4. Cohen & Frazzini (2008) — *Why supply-chain analysis is worth doing*

"Economic Links and Predictable Returns," *Journal of Finance* 63(4), 1977–2011.

Establishes empirically that **customer firms' stock price movements predict supplier firms' returns**, attributed to investor inattention to supply-chain relationships. This is the foundational evidence that ASC 280 disclosures contain pricing-relevant information — the underlying reason structuring this data adds value beyond financial statement retrieval.

> [SSRN abstract](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2758776)

---

#### 5. Why the Apple supplier cluster

The ASC 280 10% threshold triggers more readily when the supplier is small (small denominator) and the customer is large. As a structural consequence, Compustat Segment data — the basis of WRDS Supply Chain — is biased toward **small-supplier → large-customer** edges.

Our cluster (Apple as hub; Skyworks, Qorvo, Cirrus Logic, Corning, Broadcom as suppliers) falls squarely in this high-coverage region of the underlying disclosure data. Each supplier discloses Apple as a major customer in their 10-K, making the relationships extractable, verifiable, and cross-referenceable. This is the **data-availability rationale** for the cluster choice, independent of Apple's brand visibility.

---

### Graph Design

**Storage:** Pure SQL edge table in PostgreSQL. Multi-hop traversal via `WITH RECURSIVE`.

```sql
CREATE TABLE supply_edges (
    id              SERIAL PRIMARY KEY,
    supplier_ticker TEXT NOT NULL,
    customer_ticker TEXT NOT NULL,
    revenue_pct     FLOAT,
    fiscal_year     INT,
    disclosure_status TEXT,   -- 'named' | 'inferred' | 'unnamed'
    accn            TEXT,     -- SEC filing accession number (citation)
    chunk_id        INT       -- FK to text_chunks (traceability)
);
```

**Why not Neo4j or Apache AGE:** Graph has ~tens to low hundreds of edges across 6 companies. Recursive CTE handles 2–3 hop queries trivially. Keeping everything in one Postgres instance preserves transactional consistency across financial_facts, text_chunks, and supply_edges — the audit story stays unified.

**Reasoning:** Agent + `graph_query` tool following the GraphRAG paradigm (Edge et al., Microsoft 2024). Tier-3 questions are decomposed into explicit sub-steps (StepChain GraphRAG 2025), and every `graph_query` response surfaces the traversal trace with per-edge citations for eval faithfulness scoring.

---

## Architecture

```
User → Streamlit (8501) → FastAPI (8000) → Agent (gpt-4o-mini)
                                                  ↓         ↓          ↓
                                           PostgreSQL    pgvector   supply_edges
                                           (XBRL facts) (embeddings) (Stage 2)
```

### Agent Tools

| Tool | Purpose |
|---|---|
| `query_financials(ticker, metric, year)` | Fetch XBRL number from PostgreSQL |
| `list_metrics(ticker)` | Discover available metrics and year ranges |
| `compute(expression, variables)` | Sandboxed arithmetic — LLM never computes |
| `retrieve_text(query, ticker)` | Hybrid BM25 + dense retrieval over 10-K text |
| `graph_query(...)` | Supply-chain graph traversal (Stage 2) |

**Cardinal rule:** Numbers come from SQL and `compute`, never from LLM memory or inference.

---

## Project Constraints

- No LLM fine-tuning or training
- No PDF table extraction (XBRL bypasses it entirely)
- No investment advice framing
- Honest refusal over fabrication — "cannot determine" when evidence is absent
