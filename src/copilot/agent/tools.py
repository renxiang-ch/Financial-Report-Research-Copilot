"""Agent tools — query_financials, list_metrics, compute, retrieve_text, graph_query."""

from copilot.retrieval.hybrid import retrieve_hybrid as _retrieve
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


def list_metrics(ticker: str) -> dict:
    """
    Return all available metrics and fiscal years for a company.
    Call this first when unsure what data exists, or before a multi-step question
    to confirm which years are available.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT label, form, MIN(fiscal_year) as min_fy, MAX(fiscal_year) as max_fy,
                       COUNT(DISTINCT fiscal_year) as n_years
                FROM financial_facts
                WHERE ticker = %s AND fiscal_year IS NOT NULL
                GROUP BY label, form
                ORDER BY form, label
                """,
                (ticker.upper(),),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return {"found": False, "ticker": ticker}

    return {
        "found": True,
        "ticker": ticker.upper(),
        "metrics": [
            {
                "metric": row["label"],
                "form": row["form"],
                "years_available": f"{row['min_fy']}–{row['max_fy']}",
                "n_years": row["n_years"],
            }
            for row in rows
        ],
    }


def retrieve_text(query: str, ticker: str | None = None, k: int = 5) -> dict:
    """
    Search 10-K text chunks using hybrid BM25 + dense retrieval (RRF fusion).
    Use for qualitative questions (why, how, risk factors, MD&A commentary).
    Never use for numeric data — use query_financials instead.
    """
    results = _retrieve(query, ticker=ticker, k=k)
    if not results:
        return {"found": False, "query": query, "ticker": ticker}
    return {
        "found": True,
        "query": query,
        "ticker": ticker,
        "results": results,
    }


def graph_query(
    customer: str | None = None,
    supplier: str | None = None,
    fiscal_year: int | str | None = "latest",
    depth: int = 1,
) -> dict:
    """
    Query the supply-chain graph stored in supply_edges.

    Use this for Tier-3 questions about supply-chain dependencies:
    - "Who are Apple's direct suppliers?" → graph_query(customer="AAPL")
    - "Who does QRVO sell to?"           → graph_query(supplier="QRVO")
    - Multi-hop (depth=2)                → returns suppliers-of-suppliers

    Always include traversal_trace in your final answer for citation traceability.
    """
    import re as _re

    conn = get_conn()
    try:
        with conn.cursor() as cur:

            # ── Resolve fiscal_year into a SQL WHERE fragment + params ──────
            fy_clause = ""   # extra WHERE condition for fiscal year
            fy_params: list = []
            fy_label  = str(fiscal_year)

            if fiscal_year == "latest" or fiscal_year is None:
                cur.execute(
                    "SELECT MAX(fiscal_year) FROM supply_edges WHERE disclosure_status='named'"
                )
                fy_single = cur.fetchone()["max"]
                fy_clause = "AND e.fiscal_year = %s"
                fy_params = [fy_single]
                fy_label  = str(fy_single)

            elif fiscal_year == "trend":
                # No year filter — return all years
                fy_clause = ""
                fy_params = []
                fy_label  = "all years"

            elif isinstance(fiscal_year, str) and _re.match(r"\d{4}-\d{4}", fiscal_year):
                # Range like "2022-2025"
                y_start, y_end = fiscal_year.split("-")
                fy_clause = "AND e.fiscal_year BETWEEN %s AND %s"
                fy_params = [int(y_start), int(y_end)]
                fy_label  = fiscal_year

            else:
                fy_single = int(fiscal_year)
                fy_clause = "AND e.fiscal_year = %s"
                fy_params = [fy_single]
                fy_label  = str(fy_single)

            # ── Build single-hop query (depth=1) ────────────────────────────
            if depth == 1:
                if customer:
                    ticker_clause = "e.customer_ticker = %s"
                    ticker_param  = [customer.upper()]
                elif supplier:
                    ticker_clause = "e.supplier_ticker = %s"
                    ticker_param  = [supplier.upper()]
                else:
                    return {"found": False, "error": "Provide customer or supplier ticker"}

                cur.execute(
                    f"""
                    SELECT e.supplier_ticker, e.customer_ticker,
                           e.revenue_pct, e.fiscal_year,
                           e.disclosure_status, e.accn,
                           f.doc_url
                    FROM supply_edges e
                    LEFT JOIN filings f ON f.accn = e.accn
                    WHERE {ticker_clause}
                      AND e.disclosure_status = 'named'
                      {fy_clause}
                    ORDER BY e.fiscal_year DESC, e.revenue_pct DESC NULLS LAST
                    """,
                    ticker_param + fy_params,
                )
                rows = cur.fetchall()

            else:
                # ── Multi-hop: recursive CTE (single year only) ─────────────
                if not fy_params:
                    # trend/range not supported for multi-hop; fall back to latest
                    cur.execute(
                        "SELECT MAX(fiscal_year) FROM supply_edges WHERE disclosure_status='named'"
                    )
                    fy_params = [cur.fetchone()["max"]]

                anchor    = customer or supplier
                direction = "customer_ticker" if customer else "supplier_ticker"
                follow    = "supplier_ticker" if customer else "customer_ticker"
                cur.execute(
                    f"""
                    WITH RECURSIVE chain AS (
                        SELECT supplier_ticker, customer_ticker,
                               revenue_pct, fiscal_year, disclosure_status, accn, 1 AS depth
                        FROM supply_edges
                        WHERE {direction} = %s
                          AND fiscal_year = %s
                          AND disclosure_status = 'named'
                        UNION ALL
                        SELECT e.supplier_ticker, e.customer_ticker,
                               e.revenue_pct, e.fiscal_year, e.disclosure_status, e.accn,
                               c.depth + 1
                        FROM supply_edges e
                        JOIN chain c ON e.{direction} = c.{follow}
                        WHERE c.depth < %s AND e.disclosure_status = 'named'
                    )
                    SELECT DISTINCT supplier_ticker, customer_ticker,
                                    revenue_pct, fiscal_year, disclosure_status, accn, depth
                    FROM chain
                    ORDER BY depth, fiscal_year DESC, revenue_pct DESC NULLS LAST
                    """,
                    (anchor.upper(), fy_params[0], depth),
                )
                rows = cur.fetchall()

    finally:
        conn.close()

    if not rows:
        hub = customer or supplier
        return {"found": False, "hub": hub, "fiscal_year": fy_label, "edges": []}

    edges = []
    trace = []
    for row in rows:
        doc_url = row.get("doc_url", "")
        accn    = row["accn"] or ""
        citation = f"SEC 10-K accession {accn} ({doc_url})" if accn else "accession not available"
        edge = {
            "supplier":          row["supplier_ticker"],
            "customer":          row["customer_ticker"],
            "revenue_pct":       row["revenue_pct"],
            "fiscal_year":       row["fiscal_year"],
            "disclosure_status": row["disclosure_status"],
            "citation":          citation,
        }
        edges.append(edge)
        pct_str = f"{row['revenue_pct']}%" if row["revenue_pct"] else ">=10% (threshold)"
        trace.append(
            f"{row['supplier_ticker']}->{row['customer_ticker']} "
            f"{pct_str} FY{row['fiscal_year']} [{accn}]"
        )

    return {
        "found":           True,
        "hub":             customer or supplier,
        "fiscal_year":     fy_label,
        "edge_count":      len(edges),
        "edges":           edges,
        "traversal_trace": trace,
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
