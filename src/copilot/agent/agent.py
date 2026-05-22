"""
Minimal financial QA agent using Claude tool use.

The agent receives a question, calls tools to fetch numbers from SQL,
and returns a cited answer. The LLM never computes numbers itself.
"""

import json

import anthropic

from copilot.agent.tools import compute, query_financials, retrieve_text
from copilot.config import settings

# Tool schemas passed to Claude
TOOL_SCHEMAS = [
    {
        "name": "query_financials",
        "description": (
            "Query a financial metric for a company from the database. "
            "Use this for any numeric financial data — never guess numbers."
        ),
        "input_schema": {
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
    {
        "name": "retrieve_text",
        "description": (
            "Search 10-K filing text using BM25 for qualitative questions "
            "(why, how, risk factors, MD&A commentary, business description). "
            "Never use this for numeric data — use query_financials for numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":  {"type": "string", "description": "Natural language search query"},
                "ticker": {"type": "string", "description": "Optional: restrict to one company e.g. AAPL"},
                "k":      {"type": "integer", "description": "Number of passages to return (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "compute",
        "description": (
            "Evaluate an arithmetic expression using variables from query_financials results. "
            "Always use this for calculations — never compute in your head."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Python arithmetic expression e.g. 'gross_profit / revenue * 100'"},
                "variables": {"type": "object", "description": "Dict of variable name → numeric value"},
            },
            "required": ["expression", "variables"],
        },
    },
]


def _run_tool(name: str, inputs: dict) -> str:
    if name == "query_financials":
        result = query_financials(**inputs)
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
    Returns: {answer, steps, citations}
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    system = (
        "You are a financial research assistant. "
        "You answer questions about SEC filings using tools to fetch exact numbers from a database. "
        "Rules:\n"
        "1. ALWAYS use query_financials for any number — never state a number from memory.\n"
        "2. ALWAYS use compute for any calculation — never do arithmetic yourself.\n"
        "3. Cite every number with its accession number from the tool result.\n"
        "4. If the tool returns found=false, say you cannot determine the answer.\n"
        "5. Be concise. State the number, its period, and the citation."
    )

    messages = [{"role": "user", "content": question}]
    steps = []
    citations = []

    # Agentic loop — keep going until Claude stops calling tools
    while True:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            system=system,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        # Collect tool calls and text in this turn
        tool_results = []
        answer_text = ""

        for block in response.content:
            if block.type == "text":
                answer_text = block.text

            elif block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input
                steps.append({"tool": tool_name, "input": tool_input})

                result_str = _run_tool(tool_name, tool_input)
                result_data = json.loads(result_str)

                # Collect citations from query_financials results
                if tool_name == "query_financials" and result_data.get("found"):
                    citations.append(result_data["citation"])

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

        # If Claude made tool calls, feed results back and continue
        if tool_results:
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue

        # No more tool calls — we have the final answer
        break

    return {
        "answer": answer_text,
        "steps": steps,
        "citations": citations,
    }
