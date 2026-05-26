"""
Financial QA agent using OpenAI tool use (gpt-4o-mini).

The agent receives a question, calls tools to fetch numbers from SQL,
and returns a cited answer. The LLM never computes numbers itself.
"""

import json

from openai import OpenAI

from copilot.agent.tools import compute, list_metrics, query_financials, retrieve_text
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

## Multi-step questions — follow this pattern exactly

Before calling any tool, identify ALL data points needed:

| Question type         | Required calls                                              |
|-----------------------|-------------------------------------------------------------|
| Margin (gross/op/net) | query_financials ×2 (numerator + denominator) → compute     |
| YoY growth            | query_financials ×2 (year N and year N-1) → compute         |
| Cross-company compare | query_financials ×N (one per company per metric) → compute  |
| Trend (3 years)       | query_financials ×3 → present each with citation            |

Example for gross margin:
  Step 1: query_financials(AAPL, GrossProfit, 2024)
  Step 2: query_financials(AAPL, Revenue, 2024)
  Step 3: compute("gross_profit / revenue * 100", {gross_profit: <val1>, revenue: <val2>})

## Qualitative questions
Use retrieve_text for risk factors, MD&A commentary, business descriptions, competitive position.

## Output format
State the result clearly with: value, fiscal year/period, and SEC citation (accession number).
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

        # Execute each tool call and collect results
        for tc in msg.tool_calls:
            tool_name  = tc.function.name
            tool_input = json.loads(tc.function.arguments)
            steps.append({"tool": tool_name, "input": tool_input})

            result_str  = _run_tool(tool_name, tool_input)
            result_data = json.loads(result_str)

            if tool_name == "query_financials" and result_data.get("found"):
                citations.append(result_data["citation"])

            steps[-1]["output"] = result_data

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
