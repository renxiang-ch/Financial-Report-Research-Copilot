"""Agent tools — query_financials, compute, retrieve_text."""

from copilot.retrieval.bm25 import retrieve_text as _bm25_retrieve
from copilot.storage.db import get_conn


def query_financials(ticker: str, metric: str, fiscal_year: int | None = None, form: str = "10-K") -> dict:
    """
    Query financial facts from the database.

    Returns the most recent matching row, or all rows for the fiscal_year if specified.
    Always reads from SQL — never from LLM.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if fiscal_year:
                cur.execute(
                    """
                    SELECT f.ticker, f.label, f.value, f.unit, f.period_end,
                           f.fiscal_year, f.form, f.accn,
                           fi.doc_url
                    FROM financial_facts f
                    LEFT JOIN filings fi ON fi.accn = f.accn
                    WHERE f.ticker = %s
                      AND f.label  = %s
                      AND f.fiscal_year = %s
                      AND f.form = %s
                    ORDER BY f.period_end DESC
                    LIMIT 1
                    """,
                    (ticker.upper(), metric, fiscal_year, form),
                )
            else:
                cur.execute(
                    """
                    SELECT f.ticker, f.label, f.value, f.unit, f.period_end,
                           f.fiscal_year, f.form, f.accn,
                           fi.doc_url
                    FROM financial_facts f
                    LEFT JOIN filings fi ON fi.accn = f.accn
                    WHERE f.ticker = %s
                      AND f.label  = %s
                      AND f.form = %s
                    ORDER BY f.period_end DESC
                    LIMIT 1
                    """,
                    (ticker.upper(), metric, form),
                )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return {"found": False, "ticker": ticker, "metric": metric, "fiscal_year": fiscal_year}

    return {
        "found": True,
        "ticker": row["ticker"],
        "metric": row["label"],
        "value": float(row["value"]),
        "unit": row["unit"],
        "period_end": str(row["period_end"]),
        "fiscal_year": row["fiscal_year"],
        "form": row["form"],
        "citation": f"SEC 10-K filing accession {row['accn']} "
                    f"({row['doc_url'] or 'https://www.sec.gov/Archives/edgar/data/' + row['accn'].split('-')[0].lstrip('0') + '/' + row['accn'].replace('-','') + '/'})",
    }


def retrieve_text(query: str, ticker: str | None = None, k: int = 5) -> dict:
    """
    Search 10-K text chunks using BM25.
    Use for qualitative questions (why, how, risk factors, MD&A commentary).
    Never use for numeric data — use query_financials instead.
    """
    results = _bm25_retrieve(query, ticker=ticker, k=k)
    if not results:
        return {"found": False, "query": query, "ticker": ticker}
    return {
        "found": True,
        "query": query,
        "ticker": ticker,
        "results": results,
    }


def compute(expression: str, variables: dict) -> dict:
    """
    Evaluate a simple arithmetic expression with named variables.
    Numbers come from query_financials — never computed by the LLM.

    Example:
        compute("gross_profit / revenue * 100", {"gross_profit": 180683e9, "revenue": 391035e9})
    """
    allowed = {k: v for k, v in variables.items() if isinstance(v, (int, float))}
    try:
        result = eval(expression, {"__builtins__": {}}, allowed)  # noqa: S307
        return {"ok": True, "result": float(result), "expression": expression, "variables": variables}
    except Exception as e:
        return {"ok": False, "error": str(e), "expression": expression}
