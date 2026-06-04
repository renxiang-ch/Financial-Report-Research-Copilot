"""
Financial QA agent using OpenAI tool use (gpt-4o-mini).

The agent receives a question, calls tools to fetch numbers from SQL,
and returns a cited answer. The LLM never computes numbers itself.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from copilot.agent.tools import compute, graph_query, list_metrics, query_financials, retrieve_text
from copilot.config import settings

MODEL = "gpt-4o-mini"

# OpenAI function-calling format (parameters = same JSON Schema as Anthropic input_schema)
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_metrics",
            "description": (
                "List all available financial metrics and fiscal years for a company. "
                "Call this when you are unsure what data exists, or to confirm year coverage "
                "before making a multi-step query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker e.g. AAPL"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_financials",
            "description": (
                "Fetch one financial metric for one company for one fiscal year from the database. "
                "IMPORTANT: call this once per data point — for multi-datapoint questions "
                "(ratios, YoY comparisons, cross-company) you MUST call it multiple times. "
                "Never guess or reuse a number across questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker e.g. AAPL"},
                    "metric": {
                        "type": "string",
                        "description": "One of: Revenue, GrossProfit, NetIncome, OperatingIncome, "
                                       "EPS_Basic, EPS_Diluted, TotalAssets, LongTermDebt, R&D, COGS",
                    },
                    "fiscal_year": {"type": "integer", "description": "Fiscal year e.g. 2024. Omit for latest."},
                    "form": {"type": "string", "description": "10-K for annual (default), 10-Q for quarterly."},
                },
                "required": ["ticker", "metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute",
            "description": (
                "Evaluate an arithmetic expression with named variables. "
                "Call this AFTER all needed query_financials calls are done. "
                "Never do arithmetic in your head — always use this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": (
                            "Python arithmetic expression. Examples: "
                            "'gross_profit / revenue * 100' for margin, "
                            "'(new - old) / old * 100' for YoY growth."
                        ),
                    },
                    "variables": {
                        "type": "object",
                        "description": "Dict mapping variable names to numeric values from query_financials.",
                    },
                },
                "required": ["expression", "variables"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_text",
            "description": (
                "Search 10-K filing text for qualitative questions: risk factors, MD&A commentary, "
                "business description, competitive position, strategy. "
                "Use this for 'why', 'how', 'what does the company say about' questions. "
                "Never use for numeric data — use query_financials for numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":  {"type": "string", "description": "Natural language search query"},
                    "ticker": {"type": "string", "description": "Optional: restrict to one company e.g. AAPL"},
                    "k":      {"type": "integer", "description": "Number of passages to return (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_query",
            "description": (
                "Query the supply-chain graph built from 10-K customer concentration disclosures "
                "(ASC 280: customers ≥10% of revenue must be disclosed). "
                "customer: return edges where this ticker is the customer. "
                "supplier: return edges where this ticker is the supplier. "
                "Pass both to query a specific supplier→customer pair. "
                "fiscal_year: 'latest' = most recent year for this company, "
                "'trend' = all available years, an integer year, or 'YYYY-YYYY' range. "
                "depth: 1 = direct relationships (default), 2 = suppliers of suppliers. "
                "Each edge includes revenue_pct, threshold_only (true = text says '>10%' only, "
                "exact % not disclosed), citation (accession number), and source_text "
                "(verbatim sentence from the 10-K). Always surface traversal_trace and "
                "source_text in your final answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer":    {"type": "string", "description": "Find suppliers of this customer e.g. AAPL"},
                    "supplier":    {"type": "string", "description": "Find customers of this supplier e.g. QRVO"},
                    "fiscal_year": {"type": "string", "description": (
                        "'latest' = most recent year for this company (default for snapshot questions). "
                        "'trend' = all available years (use when asked about a relationship or evolution over time). "
                        "'2024' = specific year. '2022-2025' = year range."
                    )},
                    "depth":       {"type": "integer", "description": "Hops to traverse (1=direct, 2=suppliers of suppliers)"},
                },
            },
        },
    },
]

SYSTEM = """You are a financial research assistant that answers analyst questions over SEC 10-K filings.
You have tools to fetch exact numbers from a database and search filing text. Never state numbers from memory.

## Core rules
1. Every number must come from query_financials — never recall or guess figures.
2. Every calculation must use compute — never do arithmetic in your head.
3. Cite every number with its accession number from the tool result.
4. If query_financials returns found=false, say you cannot determine that value.
5. The ONLY available metrics are: Revenue, GrossProfit, NetIncome, OperatingIncome,
   EPS_Basic, EPS_Diluted, TotalAssets, LongTermDebt, R&D, COGS.
   If asked for any other metric (Free Cash Flow, EBITDA, Shareholders' Equity,
   geographic revenue, dividend yield, etc.) say you cannot determine it.
   NEVER compute a proxy approximation for an unavailable metric.
6. NEVER substitute a different company's data when the asked-about company has no results.
   If the user asks about company X and the tool returns no data for X, say
   "I cannot find supply-chain data for X in the database." — do not pivot to
   other companies' data as a substitute answer. Answer only what was asked.
7. When the question specifies a fiscal year (e.g. "FY2024", "fiscal year 2024"), pass that
   EXACT year to BOTH query_financials AND graph_query. Never use fiscal_year="latest" when
   a specific year is given.
8. graph_query returns supplier-perspective data only: revenue_pct = what % of the SUPPLIER's
   revenue comes from that customer. It does NOT tell you what % of the CUSTOMER's procurement
   or spending comes from that supplier. Questions like "what % of Apple's procurement comes
   from QRVO?" or "what share of Apple's spending is QRVO?" are UNANSWERABLE — say so explicitly.

## Multi-step questions — follow this pattern exactly

Before calling any tool, identify ALL data points needed:

| Question type              | Required calls                                                     |
|----------------------------|--------------------------------------------------------------------|
| Margin (gross/op/net)      | query_financials ×2 (numerator + denominator) → compute            |
| YoY growth                 | query_financials ×2 (year N and year N-1) → compute                |
| Cross-company compare      | query_financials ×N (one per company per metric) → compute         |
| Trend (3 years)            | query_financials ×3 → present each with citation                   |
| Supplier exposure (Tier-3) | graph_query → query_financials ×N → compute ×N → rank              |

Example for gross margin:
  Step 1: query_financials(AAPL, GrossProfit, 2024)
  Step 2: query_financials(AAPL, Revenue, 2024)
  Step 3: compute("gross_profit / revenue * 100", {gross_profit: <val1>, revenue: <val2>})

Example for supplier exposure / order-cut impact analysis:
  Step 1: graph_query(customer="AAPL", fiscal_year=year) → get all suppliers + revenue_pct
  Step 2: query_financials(supplier, Revenue, year) × N → each supplier's total revenue
  Step 3: compute("revenue * pct / 100 * cut", {revenue: <val>, pct: <val>, cut: 0.20}) × N
          → dollar IMPACT per supplier (e.g. 20% cut = multiply by 0.20, NOT 1.0)
          IMPORTANT: "impact of a 20% cut" = total_apple_revenue × 0.20, not total_apple_revenue
  Step 4: rank by dollar impact, cite accession number per edge

Example for specific relationship question ("relationship between AAPL and SWKS"):
  Step 1: graph_query(customer="AAPL", supplier="SWKS", fiscal_year="trend")
  Step 2: State the relationship facts per fiscal year, cite accession number per edge

Example for supplier trend question ("CRUS dependency on Apple" / "CRUS revenue from Apple over time"):
  Step 1: graph_query(supplier="CRUS", fiscal_year="trend")
  — CRUS is the supplier (files the 10-K that discloses the %). Apple is its customer.
  — "Company X's dependency on Y" means X sells to Y → X=supplier param, Y=customer param.
  — NEVER pass the filing company (the one whose revenue_pct is disclosed) as customer.

## Qualitative questions
Use retrieve_text for risk factors, MD&A commentary, business descriptions, competitive position.

## Output format
State the result clearly with: value, fiscal year/period, and SEC citation (accession number).
For graph queries:
- Keep the answer concise — state facts and cite one accession number per edge.
- Do NOT quote source_text inline. The UI displays source_text in a separate Citations panel.
- If an edge has threshold_only=true, report the percentage as ">10%" — never as "10.0%".
  The 10-K text only disclosed a threshold ("more than ten percent"), not the exact figure.
If a value cannot be determined, say so explicitly — never fabricate."""


def _run_tool(name: str, inputs: dict) -> str:
    if name == "query_financials":
        result = query_financials(**inputs)
    elif name == "list_metrics":
        result = list_metrics(**inputs)
    elif name == "compute":
        result = compute(**inputs)
    elif name == "retrieve_text":
        result = retrieve_text(**inputs)
    elif name == "graph_query":
        result = graph_query(**inputs)
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)


def ask(question: str) -> dict:
    """
    Run the agent loop for a single question.
    Returns: {answer, steps, citations, usage}
    """
    client = OpenAI(api_key=settings.openai_api_key)

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": question},
    ]
    steps = []
    citations = []
    total_input_tokens  = 0
    total_output_tokens = 0

    MAX_ROUNDS = 10

    for _ in range(MAX_ROUNDS):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )

        total_input_tokens  += response.usage.prompt_tokens
        total_output_tokens += response.usage.completion_tokens

        msg = response.choices[0].message

        # No tool calls — model is done
        if not msg.tool_calls:
            answer_text = msg.content or ""
            break

        # Append assistant turn (with tool_calls)
        messages.append(msg)

        # Execute tool calls — parallel when multiple in one round
        def _exec(tc):
            name  = tc.function.name
            inp   = json.loads(tc.function.arguments)
            out   = _run_tool(name, inp)
            return tc, name, inp, out

        if len(msg.tool_calls) == 1:
            ordered = [_exec(msg.tool_calls[0])]
        else:
            with ThreadPoolExecutor() as pool:
                futures = {pool.submit(_exec, tc): tc for tc in msg.tool_calls}
                id_order = {tc.id: i for i, tc in enumerate(msg.tool_calls)}
                ordered = [None] * len(msg.tool_calls)
                for future in as_completed(futures):
                    tc, name, inp, out = future.result()
                    ordered[id_order[tc.id]] = (tc, name, inp, out)

        for tc, tool_name, tool_input, result_str in ordered:
            result_data = json.loads(result_str)
            steps.append({"tool": tool_name, "input": tool_input, "output": result_data})

            if tool_name == "query_financials" and result_data.get("found"):
                citations.append(result_data["citation"])

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result_str,
            })

    else:
        answer_text = (
            f"[Circuit breaker] Could not produce a final answer within {MAX_ROUNDS} "
            "tool-call rounds. Partial steps are recorded."
        )

    return {
        "answer":    answer_text,
        "steps":     steps,
        "citations": citations,
        "usage": {
            "input_tokens":  total_input_tokens,
            "output_tokens": total_output_tokens,
        },
    }
