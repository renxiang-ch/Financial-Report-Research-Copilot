"""
SC-DisclosureQA benchmark builder.

Generates 150 questions across 6 types from live DB state:
  T1 (30): SQL numeric — direct XBRL lookup
  T2 (30): SQL + compute — ratios, YoY, R&D intensity
  T3 (25): Qualitative retrieval — key phrase from text_chunks
  T4 (30): Graph relation — supply_edges lookup / trend / comparison
  T5 (20): Cross-entity reasoning — graph × financials compute
  T6 (15): Unanswerable — direction-reversed, data gap, out-of-scope

All expected_values for T1/T2/T4/T5 are resolved from live DB at build time.
T3 questions include a candidate key_phrase; those not auto-verified are flagged
requires_review=true.

Usage:
    python -m copilot.eval.build_sc_eval
    python -m copilot.eval.build_sc_eval --out data/eval_set_sc.json
"""

import argparse
import json
import re
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor

from copilot.config import settings
from copilot.pipeline.companies import CLUSTER_RESEARCH

COMPANY_SHORT: dict[str, str] = {
    "AAPL": "Apple",
    "CRUS": "Cirrus Logic",
    "QRVO": "Qorvo",
    "SWKS": "Skyworks",
    "AVGO": "Broadcom",
    "QCOM": "Qualcomm",
    "GLW": "Corning",
    "ADI": "Analog Devices",
    "TXN": "Texas Instruments",
    "MCHP": "Microchip Technology",
    "ON": "onsemi",
    "LRCX": "Lam Research",
    "APH": "Amphenol",
    "JBL": "Jabil",
    "SANM": "Sanmina",
}

METRIC_LABEL: dict[str, str] = {
    "Revenue": "revenue",
    "NetIncome": "net income",
    "GrossProfit": "gross profit",
    "OperatingIncome": "operating income",
    "R&D": "R&D spending",
    "EPS_Diluted": "diluted EPS",
    "TotalAssets": "total assets",
    "OperatingCashFlow": "operating cash flow",
    "LongTermDebt": "long-term debt",
    "CapEx": "capital expenditure",
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_fact(cur, ticker: str, label: str, fiscal_year: int) -> float | None:
    # Match query_financials: annual form only, most recent period_end within that FY
    cur.execute(
        """SELECT value FROM financial_facts
           WHERE ticker=%s AND label=%s AND fiscal_year=%s AND form='10-K'
           ORDER BY period_end DESC LIMIT 1""",
        (ticker, label, fiscal_year),
    )
    row = cur.fetchone()
    return float(row["value"]) if row else None


def _get_edge(cur, supplier: str, customer: str, fiscal_year: int) -> dict | None:
    cur.execute(
        "SELECT revenue_pct, threshold_only FROM supply_edges "
        "WHERE supplier_ticker=%s AND customer_ticker=%s AND fiscal_year=%s",
        (supplier, customer, fiscal_year),
    )
    return cur.fetchone()


def _find_key_phrase(cur, ticker: str, search_terms: list[str],
                     sections: list[str], fiscal_year: int | None = None) -> str | None:
    """Return a short unique phrase from the best matching text chunk.

    Progressively relaxes from all search_terms to the first term only
    until a matching chunk is found.
    """
    section_placeholders = ",".join(["%s"] * len(sections))
    fy_clause = " AND f.fiscal_year = %s" if fiscal_year else ""

    for num_terms in range(len(search_terms), 0, -1):
        terms_subset = search_terms[:num_terms]
        terms_filter = " AND ".join(f"tc.text ILIKE %s" for _ in terms_subset)
        params: list = sections + [f"%{t}%" for t in terms_subset] + [ticker]
        if fiscal_year:
            params.append(fiscal_year)

        cur.execute(f"""
            SELECT tc.text FROM text_chunks tc
            JOIN filings f ON tc.accn = f.accn
            WHERE tc.section IN ({section_placeholders})
              AND {terms_filter}
              AND tc.ticker = %s
              {fy_clause}
            ORDER BY f.fiscal_year DESC, tc.chunk_index
            LIMIT 1
        """, params)
        row = cur.fetchone()
        if row:
            text = row["text"]
            # Prefer a sentence containing as many search terms as possible
            sentences = re.split(r'(?<=[.!?])\s+', text)
            for n in range(len(search_terms), 0, -1):
                for sent in sentences:
                    low = sent.lower()
                    if all(t.lower() in low for t in search_terms[:n]) and len(sent) > 20:
                        m = re.search(r'\w.{40,70}\w', sent)
                        return m.group(0).strip() if m else sent[:70].strip()
            # Fallback: first meaningful line of chunk
            for line in text.split("\n"):
                if len(line.strip()) > 30:
                    return line.strip()[:70]
            return text[:70].strip()

    return None


# ── T1: SQL numeric ───────────────────────────────────────────────────────────

def build_t1(cur) -> list[dict]:
    questions: list[dict] = []

    # (ticker, metric, fiscal_year) — 2 per company × 15 = 30
    specs: list[tuple[str, str, int]] = [
        # Apple
        ("AAPL", "Revenue",        2024),
        ("AAPL", "NetIncome",      2022),
        # Cirrus Logic
        ("CRUS", "Revenue",        2024),
        ("CRUS", "OperatingIncome", 2023),
        # Qorvo
        ("QRVO", "Revenue",        2023),
        ("QRVO", "GrossProfit",    2022),
        # Skyworks
        ("SWKS", "Revenue",        2024),
        ("SWKS", "NetIncome",      2023),
        # Broadcom
        ("AVGO", "Revenue",        2024),
        ("AVGO", "R&D",            2023),
        # Qualcomm
        ("QCOM", "Revenue",        2024),
        ("QCOM", "OperatingIncome", 2023),
        # Corning
        ("GLW",  "Revenue",        2023),
        ("GLW",  "NetIncome",      2022),
        # Analog Devices
        ("ADI",  "Revenue",        2024),
        ("ADI",  "GrossProfit",    2023),
        # Texas Instruments
        ("TXN",  "Revenue",        2023),
        ("TXN",  "OperatingIncome", 2022),
        # Microchip Technology
        ("MCHP", "Revenue",        2024),
        ("MCHP", "NetIncome",      2023),
        # onsemi
        ("ON",   "Revenue",        2023),
        ("ON",   "GrossProfit",    2022),
        # Lam Research
        ("LRCX", "Revenue",        2024),
        ("LRCX", "OperatingCashFlow", 2023),
        # Amphenol
        ("APH",  "Revenue",        2024),
        ("APH",  "NetIncome",      2023),
        # Jabil
        ("JBL",  "Revenue",        2024),
        ("JBL",  "GrossProfit",    2023),
        # Sanmina
        ("SANM", "Revenue",        2024),
        ("SANM", "NetIncome",      2023),
    ]

    for ticker, metric, fy in specs:
        val = _get_fact(cur, ticker, metric, fy)
        if val is None:
            print(f"  [SKIP T1] {ticker} {metric} FY{fy} — not in DB")
            continue
        name = COMPANY_SHORT[ticker]
        label = METRIC_LABEL.get(metric, metric)
        unit = "USD/share" if "EPS" in metric else "USD"
        questions.append({
            "id":             f"t1_{ticker.lower()}_{metric.lower().replace('&','').replace('/','_')}_{fy}",
            "sc_type":        "T1",
            "question":       f"What was {name}'s {label} in fiscal year {fy}?",
            "ticker":         ticker,
            "fiscal_year":    fy,
            "metric":         metric,
            "expected_value": val,
            "expected_unit":  unit,
            "tolerance_pct":  2.0,
            "answerable":     True,
        })

    return questions


# ── T2: SQL + compute ─────────────────────────────────────────────────────────

def build_t2(cur) -> list[dict]:
    questions: list[dict] = []

    # Gross margin % (10)
    gm_specs = [
        ("AAPL", 2024), ("QRVO", 2023), ("CRUS", 2024), ("AVGO", 2024),
        ("SWKS", 2023), ("JBL", 2024), ("ADI", 2023), ("TXN", 2023),
        ("ON", 2023), ("LRCX", 2024),
    ]
    for ticker, fy in gm_specs:
        rev = _get_fact(cur, ticker, "Revenue", fy)
        gp  = _get_fact(cur, ticker, "GrossProfit", fy)
        if rev and gp and rev > 0:
            val = round(gp / rev * 100, 4)
            name = COMPANY_SHORT[ticker]
            questions.append({
                "id":           f"t2_{ticker.lower()}_gross_margin_{fy}",
                "sc_type":      "T2",
                "question":     f"What was {name}'s gross margin percentage in fiscal year {fy}?",
                "ticker":       ticker,
                "fiscal_year":  fy,
                "expected_value": val,
                "expected_unit":  "%",
                "tolerance_pct":  0.5,
                "answerable":     True,
                "formula":        "GrossProfit / Revenue * 100",
                "input_values":   {"GrossProfit": gp, "Revenue": rev},
            })

    # R&D intensity % (8)
    rd_specs = [
        ("QRVO", 2024), ("CRUS", 2024), ("SWKS", 2023), ("AVGO", 2023),
        ("TXN", 2024), ("QCOM", 2023), ("ADI", 2024), ("MCHP", 2023),
    ]
    for ticker, fy in rd_specs:
        rev = _get_fact(cur, ticker, "Revenue", fy)
        rd  = _get_fact(cur, ticker, "R&D", fy)
        if rev and rd and rev > 0:
            val = round(rd / rev * 100, 4)
            name = COMPANY_SHORT[ticker]
            questions.append({
                "id":           f"t2_{ticker.lower()}_rd_intensity_{fy}",
                "sc_type":      "T2",
                "question":     f"What was {name}'s R&D spending as a percentage of revenue in fiscal year {fy}?",
                "ticker":       ticker,
                "fiscal_year":  fy,
                "expected_value": val,
                "expected_unit":  "%",
                "tolerance_pct":  0.5,
                "answerable":     True,
                "formula":        "R&D / Revenue * 100",
                "input_values":   {"R&D": rd, "Revenue": rev},
            })

    # YoY Revenue growth % (7)
    yoy_specs = [
        ("QRVO", 2022, 2023), ("CRUS", 2022, 2023), ("JBL",  2022, 2023),
        ("AAPL", 2023, 2024), ("SWKS", 2022, 2023), ("ON",   2021, 2022),
        ("LRCX", 2023, 2024),
    ]
    for ticker, fy_prev, fy_curr in yoy_specs:
        r1 = _get_fact(cur, ticker, "Revenue", fy_prev)
        r2 = _get_fact(cur, ticker, "Revenue", fy_curr)
        if r1 and r2 and r1 > 0:
            val = round((r2 - r1) / r1 * 100, 4)
            name = COMPANY_SHORT[ticker]
            questions.append({
                "id":           f"t2_{ticker.lower()}_revenue_yoy_{fy_prev}_{fy_curr}",
                "sc_type":      "T2",
                "question":     f"By what percentage did {name}'s revenue change from fiscal year {fy_prev} to {fy_curr}?",
                "ticker":       ticker,
                "fiscal_year":  fy_curr,
                "expected_value": val,
                "expected_unit":  "%",
                "tolerance_pct":  0.5,
                "answerable":     True,
                "formula":        f"(Revenue_{fy_curr} - Revenue_{fy_prev}) / Revenue_{fy_prev} * 100",
                "input_values":   {f"Revenue_{fy_curr}": r2, f"Revenue_{fy_prev}": r1},
            })

    # Operating margin % (5)
    om_specs = [
        ("AAPL", 2023), ("QCOM", 2022), ("GLW", 2023), ("TXN", 2022), ("SANM", 2024),
    ]
    for ticker, fy in om_specs:
        rev = _get_fact(cur, ticker, "Revenue", fy)
        oi  = _get_fact(cur, ticker, "OperatingIncome", fy)
        if rev and oi and rev > 0:
            val = round(oi / rev * 100, 4)
            name = COMPANY_SHORT[ticker]
            questions.append({
                "id":           f"t2_{ticker.lower()}_operating_margin_{fy}",
                "sc_type":      "T2",
                "question":     f"What was {name}'s operating margin percentage in fiscal year {fy}?",
                "ticker":       ticker,
                "fiscal_year":  fy,
                "expected_value": val,
                "expected_unit":  "%",
                "tolerance_pct":  0.5,
                "answerable":     True,
                "formula":        "OperatingIncome / Revenue * 100",
                "input_values":   {"OperatingIncome": oi, "Revenue": rev},
            })

    return questions


# ── T3: Qualitative retrieval ─────────────────────────────────────────────────

def build_t3(cur) -> list[dict]:
    questions: list[dict] = []

    # (ticker, fiscal_year, question_text, search_terms, sections, hint)
    # sections chosen based on actual chunk counts in DB:
    #   Companies with NO Business section: JBL, TXN, SANM → use MD&A or Risk Factors
    #   Companies with only 1 Business chunk: CRUS, ADI → use Risk Factors
    #   AVGO Risk Factors only 1-2 chunks → use Business (66-69 chunks)
    specs = [
        # Apple concentration — named disclosure
        ("CRUS", 2024, "How does Cirrus Logic describe its Apple customer concentration in its FY2024 10-K?",
         ["apple", "customer", "concentration"], ["Risk Factors"], "Apple customer concentration risk"),
        ("QRVO", 2024, "How does Qorvo describe its revenue concentration from Apple in its FY2024 10-K?",
         ["apple", "revenue", "aggregate"], ["Business"], "Apple % via contract manufacturers"),
        ("JBL", 2024, "How does Jabil describe its largest customer relationship in its FY2024 10-K?",
         ["apple", "customer", "revenue"], ["Risk Factors"], "Apple net revenue percentage"),
        ("AVGO", 2022, "How does Broadcom describe its Apple revenue concentration in its FY2022 10-K?",
         ["apple", "revenue", "channels"], ["Business"], "Apple aggregate sales % through channels"),
        ("SWKS", 2024, "How does Skyworks describe its Apple customer dependency in its FY2024 10-K?",
         ["apple", "ten percent", "net revenue"], ["Business"], "Apple > ten percent threshold"),

        # Customer concentration risk in Risk Factors
        ("QRVO", 2023, "What risk does Qorvo disclose about customer concentration in its FY2023 Risk Factors?",
         ["customer", "revenue", "significant"], ["Risk Factors"], "customer concentration risk warning"),
        ("CRUS", 2023, "What does Cirrus Logic's FY2023 10-K say about the risk of losing Apple as a customer?",
         ["apple", "revenue", "loss"], ["Risk Factors"], "loss of Apple concentration risk"),
        ("SWKS", 2023, "How does Skyworks describe customer concentration risk in its FY2023 Risk Factors?",
         ["customer", "revenue"], ["Risk Factors"], "customer concentration risk"),
        ("JBL", 2022, "What customer concentration risk does Jabil disclose in its FY2022 Risk Factors?",
         ["customer", "revenue", "significant"], ["Risk Factors"], "customer concentration warning"),
        ("AVGO", 2023, "How does Broadcom describe its customer concentration risk in its FY2023 10-K?",
         ["customer", "revenue"], ["Business"], "customer concentration risk"),

        # Business description / strategy
        ("QCOM", 2023, "How does Qualcomm describe its QCT semiconductor segment in its FY2023 10-K?",
         ["QCT", "revenue", "segment"], ["Business"], "QCT segment description"),
        ("TXN", 2023, "How does Texas Instruments describe risks to its business in its FY2023 Risk Factors?",
         ["customer", "revenue"], ["Risk Factors"], "TI customer/revenue risk"),
        ("GLW", 2023, "How does Corning describe its Display Technologies segment in its FY2023 10-K?",
         ["display", "segment", "revenue"], ["Business"], "Display Technologies segment"),
        ("MCHP", 2024, "How does Microchip Technology describe its product portfolio in its FY2024 10-K?",
         ["product", "revenue", "customer"], ["MD&A"], "MCU/FPGA product description"),
        ("ON", 2023, "How does onsemi describe its intelligent power products in its FY2023 10-K?",
         ["power", "automotive", "revenue"], ["Business"], "power semiconductor portfolio"),

        # Competitive position
        ("LRCX", 2024, "How does Lam Research describe its competitive environment in its FY2024 10-K?",
         ["customer", "revenue"], ["Risk Factors"], "competitive environment / customer concentration"),
        ("ADI", 2024, "How does Analog Devices describe export control or China-related risks in its FY2024 10-K?",
         ["china", "revenue"], ["Risk Factors"], "China/export control risk disclosure"),
        ("SANM", 2024, "How does Sanmina describe its customer concentration risk in its FY2024 10-K?",
         ["customer", "revenue"], ["Risk Factors"], "EMS customer concentration risk"),

        # Geographic/geopolitical risk
        ("QRVO", 2023, "What geopolitical or geographic risk does Qorvo disclose regarding China in its FY2023 Risk Factors?",
         ["china", "revenue", "risk"], ["Risk Factors"], "China revenue/export risk"),
        ("SWKS", 2024, "What does Skyworks say about customer concentration risks in its FY2024 Risk Factors?",
         ["customer", "revenue"], ["Risk Factors"], "customer concentration / China risk"),
        ("AVGO", 2023, "How does Broadcom describe risks related to China in its FY2023 10-K?",
         ["china", "revenue"], ["Business"], "China regulatory risk"),

        # Manufacturing / supply chain operations
        ("JBL", 2023, "How does Jabil describe its manufacturing operations in its FY2023 10-K?",
         ["manufacturing", "revenue"], ["MD&A"], "global manufacturing footprint"),
        ("APH", 2023, "How does Amphenol describe its manufacturing strategy in its FY2023 10-K?",
         ["product", "revenue"], ["Business"], "manufacturing/acquisition strategy"),
        ("SANM", 2023, "How does Sanmina describe its integrated manufacturing services in its FY2023 10-K?",
         ["manufacturing", "revenue"], ["Risk Factors"], "IMS service description"),

        # M&A / history
        ("ADI", 2022, "What acquisition-related risks does Analog Devices disclose in its FY2022 Risk Factors?",
         ["acquisition", "revenue"], ["Risk Factors"], "acquisition integration risk"),
    ]

    for spec in specs:
        ticker, fy, question, search_terms, sections, hint = spec
        key_phrase = _find_key_phrase(cur, ticker, search_terms, sections, fy)
        verified = key_phrase is not None
        name = COMPANY_SHORT[ticker]
        q: dict = {
            "id":            f"t3_{ticker.lower()}_{search_terms[0].replace(' ','_')}_{fy}",
            "sc_type":       "T3",
            "question":      question,
            "ticker":        ticker,
            "fiscal_year":   fy,
            "answerable":    True,
            "expected_value": None,
            "expected_unit":  None,
            "tolerance_pct":  None,
            "golden_citations": [{"ticker": ticker, "section": sections[0], "key_phrase": key_phrase or ""}],
            "answer_hint":   hint,
        }
        if not verified:
            q["requires_review"] = True
            print(f"  [T3 REVIEW] {ticker} FY{fy} — no chunk found for {search_terms}")
        questions.append(q)

    return questions


# ── T4: Graph relation ────────────────────────────────────────────────────────

def build_t4(cur) -> list[dict]:
    questions: list[dict] = []

    def edge_truth(supplier, customer, fy):
        row = _get_edge(cur, supplier, customer, fy)
        if not row:
            return None
        return {"supplier": supplier, "customer": customer,
                "fiscal_year": fy, "revenue_pct": row["revenue_pct"],
                "threshold_only": bool(row["threshold_only"])}

    # --- graph_fact: exact pct lookup (12 questions) ---
    fact_specs = [
        ("CRUS", "AAPL", 2024,
         "What percentage of Cirrus Logic's revenue came from Apple in fiscal year 2024?"),
        ("QRVO", "AAPL", 2024,
         "What percentage of Qorvo's revenue came from Apple in fiscal year 2024?"),
        ("JBL",  "AAPL", 2023,
         "What percentage of Jabil's revenue came from Apple in fiscal year 2023?"),
        ("AVGO", "AAPL", 2022,
         "What percentage of Broadcom's revenue came from Apple in fiscal year 2022?"),
        ("CRUS", "AAPL", 2022,
         "What percentage of Cirrus Logic's revenue came from Apple in fiscal year 2022?"),
        ("QRVO", "AAPL", 2022,
         "What percentage of Qorvo's revenue came from Apple in fiscal year 2022?"),
        ("JBL",  "AAPL", 2021,
         "What percentage of Jabil's revenue came from Apple in fiscal year 2021?"),
        ("AVGO", "AAPL", 2021,
         "What percentage of Broadcom's revenue came from Apple in fiscal year 2021?"),
        ("ADI",  "AAPL", 2017,
         "What percentage of Analog Devices' revenue came from Apple in fiscal year 2017?"),
        ("QRVO", "AAPL", 2020,
         "What percentage of Qorvo's revenue came from Apple in fiscal year 2020?"),
        ("QRVO", "Huawei", 2019,
         "What percentage of Qorvo's revenue came from Huawei in fiscal year 2019?"),
        ("AVGO", "WT Microelectronics", 2022,
         "What percentage of Broadcom's revenue came from WT Microelectronics in fiscal year 2022?"),
        # Additional Samsung edges (exact %, non-threshold)
        ("QRVO", "005930.KS", 2022,
         "What percentage of Qorvo's revenue came from Samsung in fiscal year 2022?"),
        ("QRVO", "005930.KS", 2023,
         "What percentage of Qorvo's revenue came from Samsung in fiscal year 2023?"),
        ("QRVO", "005930.KS", 2024,
         "What percentage of Qorvo's revenue came from Samsung in fiscal year 2024?"),
        ("AVGO", "WT Microelectronics", 2021,
         "What percentage of Broadcom's revenue came from WT Microelectronics in fiscal year 2021?"),
    ]
    for supplier, customer, fy, question in fact_specs:
        truth = edge_truth(supplier, customer, fy)
        if not truth:
            print(f"  [SKIP T4 fact] {supplier}->{customer} FY{fy}")
            continue
        pct = truth["revenue_pct"]
        threshold = truth["threshold_only"]
        questions.append({
            "id":          f"t4_fact_{supplier.lower()}_{customer.lower().replace(' ','_')}_{fy}",
            "sc_type":     "T4",
            "sc_subtype":  "graph_fact",
            "question":    question,
            "supplier":    supplier,
            "customer":    customer,
            "fiscal_year": fy,
            "expected_value": pct,
            "threshold_only": threshold,
            "expected_unit": "%",
            "tolerance_pct": 2.0,
            "answerable":  True,
            "traversal_ground_truth": [truth],
        })

    # --- graph_lookup: which companies supply Apple (4 questions) ---
    for fy in [2021, 2022, 2023, 2024]:
        cur.execute("""
            SELECT supplier_ticker, revenue_pct, threshold_only
            FROM supply_edges
            WHERE customer_ticker='AAPL' AND fiscal_year=%s
            ORDER BY CASE WHEN threshold_only THEN 0 ELSE revenue_pct END DESC
        """, (fy,))
        rows = cur.fetchall()
        if not rows:
            continue
        suppliers = [r["supplier_ticker"] for r in rows]
        traversal = [edge_truth(r["supplier_ticker"], "AAPL", fy) for r in rows if edge_truth(r["supplier_ticker"], "AAPL", fy)]
        questions.append({
            "id":          f"t4_lookup_apple_suppliers_{fy}",
            "sc_type":     "T4",
            "sc_subtype":  "graph_lookup",
            "question":    f"Which companies in the dataset disclosed Apple as a significant (>10%) revenue customer in fiscal year {fy}?",
            "customer":    "AAPL",
            "fiscal_year": fy,
            "expected_value": suppliers,
            "expected_unit": "ticker_list",
            "tolerance_pct": None,
            "answerable":  True,
            "traversal_ground_truth": traversal,
            "scoring":     "set_match",
        })

    # --- graph_trend: FY2020-2024 for CRUS and QRVO (2 questions) ---
    for supplier in ["CRUS", "QRVO"]:
        cur.execute("""
            SELECT fiscal_year, revenue_pct, threshold_only
            FROM supply_edges
            WHERE supplier_ticker=%s AND customer_ticker='AAPL'
              AND fiscal_year BETWEEN 2020 AND 2024
            ORDER BY fiscal_year
        """, (supplier,))
        rows = cur.fetchall()
        if len(rows) < 3:
            continue
        trend = {r["fiscal_year"]: r["revenue_pct"] for r in rows if not r["threshold_only"]}
        traversal = [edge_truth(supplier, "AAPL", r["fiscal_year"]) for r in rows]
        traversal = [t for t in traversal if t]
        name = COMPANY_SHORT[supplier]
        questions.append({
            "id":          f"t4_trend_{supplier.lower()}_apple_2020_2024",
            "sc_type":     "T4",
            "sc_subtype":  "graph_trend",
            "question":    f"How has {name}'s revenue concentration from Apple changed from fiscal year 2020 to 2024? Provide the percentage for each year.",
            "supplier":    supplier,
            "customer":    "AAPL",
            "fiscal_year": "2020-2024",
            "expected_value": trend,
            "expected_unit": "%_by_year",
            "tolerance_pct": 2.0,
            "answerable":  True,
            "traversal_ground_truth": traversal,
            "scoring":     "trend",
        })

    # --- graph_comparison: highest Apple concentration FY2022, FY2024 (2 questions) ---
    for fy in [2022, 2024]:
        cur.execute("""
            SELECT supplier_ticker, revenue_pct
            FROM supply_edges
            WHERE customer_ticker='AAPL' AND fiscal_year=%s AND threshold_only=FALSE
            ORDER BY revenue_pct DESC LIMIT 1
        """, (fy,))
        row = cur.fetchone()
        if not row:
            continue
        winner = row["supplier_ticker"]
        traversal = [t for t in [edge_truth(supplier, "AAPL", fy)
                     for supplier in ["CRUS","QRVO","JBL","AVGO","SWKS"]] if t]
        questions.append({
            "id":          f"t4_compare_highest_apple_{fy}",
            "sc_type":     "T4",
            "sc_subtype":  "graph_comparison",
            "question":    f"Which company had the highest Apple revenue concentration in fiscal year {fy}: Cirrus Logic, Qorvo, Jabil, or Broadcom?",
            "customer":    "AAPL",
            "fiscal_year": fy,
            "expected_value": winner,
            "expected_unit": "ticker",
            "tolerance_pct": None,
            "answerable":  True,
            "traversal_ground_truth": traversal,
            "scoring":     "llm_judge",
        })

    # --- graph_negative: companies that do NOT name a >10% customer (6 questions) ---
    negative_specs = [
        ("TXN",  2023, "Did Texas Instruments disclose any single customer accounting for more than 10% of revenue in fiscal year 2023?"),
        ("GLW",  2022, "Did Corning name any specific customer accounting for more than 10% of revenue in fiscal year 2022?"),
        ("LRCX", 2023, "Did Lam Research disclose a named customer accounting for more than 10% of revenue in fiscal year 2023?"),
        ("SANM", 2023, "Did Sanmina disclose any single named customer accounting for more than 10% of revenue in fiscal year 2023?"),
        ("ADI",  2022, "Did Analog Devices disclose Apple as a customer accounting for more than 10% of revenue in fiscal year 2022?"),
        ("QCOM", 2022, "Did Qualcomm disclose Apple as a direct customer accounting for more than 10% of consolidated revenues in fiscal year 2022?"),
    ]
    for ticker, fy, question in negative_specs:
        questions.append({
            "id":          f"t4_negative_{ticker.lower()}_{fy}",
            "sc_type":     "T4",
            "sc_subtype":  "graph_negative",
            "question":    question,
            "supplier":    ticker,
            "customer":    "AAPL",
            "fiscal_year": fy,
            "expected_value": False,
            "expected_unit": "boolean",
            "tolerance_pct": None,
            "answerable":  True,
            "traversal_ground_truth": [],
            "scoring":     "keyword",
        })

    return questions


# ── T5: Cross-entity reasoning ────────────────────────────────────────────────

def build_t5(cur) -> list[dict]:
    questions: list[dict] = []

    # --- Dollar exposure: Revenue × apple_pct (8 questions) ---
    exposure_specs = [
        ("QRVO", 2024, "How much revenue (in dollars) did Qorvo derive from Apple channels in fiscal year 2024, based on its disclosed concentration percentage?"),
        ("CRUS", 2024, "How much revenue (in dollars) did Cirrus Logic derive from Apple in fiscal year 2024, based on its disclosed concentration percentage?"),
        ("JBL",  2023, "How much revenue (in dollars) did Jabil derive from Apple in fiscal year 2023, based on its disclosed concentration percentage?"),
        ("AVGO", 2022, "How much revenue (in dollars) did Broadcom derive from Apple in fiscal year 2022, based on its disclosed concentration percentage?"),
        ("QRVO", 2022, "How much revenue (in dollars) did Qorvo derive from Apple channels in fiscal year 2022?"),
        ("CRUS", 2022, "How much revenue (in dollars) did Cirrus Logic derive from Apple in fiscal year 2022?"),
        ("JBL",  2021, "How much revenue (in dollars) did Jabil derive from Apple in fiscal year 2021?"),
        ("CRUS", 2023, "How much revenue (in dollars) did Cirrus Logic derive from Apple in fiscal year 2023?"),
        ("AVGO", 2020, "How much revenue (in dollars) did Broadcom derive from Apple in fiscal year 2020, based on its disclosed concentration percentage?"),
    ]
    for supplier, fy, question in exposure_specs:
        rev = _get_fact(cur, supplier, "Revenue", fy)
        edge = _get_edge(cur, supplier, "AAPL", fy)
        if not rev or not edge or edge["threshold_only"]:
            print(f"  [SKIP T5 exposure] {supplier} FY{fy} — missing data or threshold-only")
            continue
        pct = edge["revenue_pct"]
        apple_rev = round(rev * pct / 100)
        truth = edge_truth_from_edge(supplier, "AAPL", fy, pct, False)
        questions.append({
            "id":          f"t5_exposure_{supplier.lower()}_apple_{fy}",
            "sc_type":     "T5",
            "sc_subtype":  "graph_compute",
            "question":    question,
            "supplier":    supplier,
            "customer":    "AAPL",
            "fiscal_year": fy,
            "expected_value": apple_rev,
            "expected_unit": "USD",
            "tolerance_pct": 2.0,
            "answerable":  True,
            "formula":     "Revenue * apple_pct / 100",
            "input_values": {"Revenue": rev, "apple_pct": pct},
            "traversal_ground_truth": [truth],
            "scoring":     "numeric",
        })

    # --- Order cut impact (5 questions) ---
    cut_specs = [
        ("QRVO", 2024, 20, "If Apple reduced its orders by 20%, how much revenue would Qorvo lose in fiscal year 2024, based on its disclosed Apple concentration?"),
        ("CRUS", 2024, 20, "If Apple reduced its orders by 20%, how much revenue would Cirrus Logic lose in fiscal year 2024?"),
        ("JBL",  2022, 10, "If Apple reduced its orders by 10%, how much revenue would Jabil lose based on its fiscal year 2022 data?"),
        ("AVGO", 2022, 15, "If Apple reduced its orders by 15%, how much revenue would Broadcom lose based on its fiscal year 2022 Apple concentration?"),
        ("QRVO", 2022, 20, "If Apple reduced its orders by 20%, how much revenue would Qorvo lose in fiscal year 2022?"),
    ]
    for supplier, fy, cut_pct, question in cut_specs:
        rev = _get_fact(cur, supplier, "Revenue", fy)
        edge = _get_edge(cur, supplier, "AAPL", fy)
        if not rev or not edge or edge["threshold_only"]:
            continue
        pct = edge["revenue_pct"]
        impact = round(rev * pct / 100 * cut_pct / 100)
        truth = edge_truth_from_edge(supplier, "AAPL", fy, pct, False)
        questions.append({
            "id":          f"t5_cut_{supplier.lower()}_apple_{cut_pct}pct_{fy}",
            "sc_type":     "T5",
            "sc_subtype":  "graph_compute",
            "question":    question,
            "supplier":    supplier,
            "customer":    "AAPL",
            "fiscal_year": fy,
            "expected_value": impact,
            "expected_unit": "USD",
            "tolerance_pct": 2.0,
            "answerable":  True,
            "formula":     f"Revenue * apple_pct / 100 * {cut_pct} / 100",
            "input_values": {"Revenue": rev, "apple_pct": pct, "cut_pct": cut_pct},
            "traversal_ground_truth": [truth],
            "scoring":     "numeric",
        })

    # --- Comparison: who loses more (3 questions) ---
    compare_specs = [
        (("CRUS", "QRVO"), 2023, "llm_judge",
         "Which company stands to lose more revenue from a 20% Apple order cut in fiscal year 2023: Cirrus Logic or Qorvo? Provide the dollar impact for each."),
        (("CRUS", "QRVO", "JBL"), 2022, "llm_judge",
         "Rank Cirrus Logic, Qorvo, and Jabil by their dollar exposure to Apple in fiscal year 2022, from highest to lowest."),
        (("QRVO", "CRUS"), 2024, "llm_judge",
         "Compare Cirrus Logic and Qorvo's Apple revenue exposure in fiscal year 2024. Which is more concentrated and which has more absolute dollar exposure?"),
    ]
    for suppliers, fy, scoring, question in compare_specs:
        # Build expected values dict
        ev: dict[str, float] = {}
        traversal = []
        for sup in suppliers:
            rev = _get_fact(cur, sup, "Revenue", fy)
            edge = _get_edge(cur, sup, "AAPL", fy)
            if rev and edge and not edge["threshold_only"]:
                ev[sup] = round(rev * edge["revenue_pct"] / 100)
                traversal.append(edge_truth_from_edge(sup, "AAPL", fy, edge["revenue_pct"], False))
        if len(ev) < 2:
            continue
        label = "_".join(s.lower() for s in suppliers)
        questions.append({
            "id":          f"t5_compare_{label}_{fy}",
            "sc_type":     "T5",
            "sc_subtype":  "graph_comparison",
            "question":    question,
            "suppliers":   list(suppliers),
            "customer":    "AAPL",
            "fiscal_year": fy,
            "expected_value": ev,
            "expected_unit": "USD_by_supplier",
            "tolerance_pct": 2.0,
            "answerable":  True,
            "traversal_ground_truth": traversal,
            "scoring":     scoring,
        })

    # --- Trend: QRVO dollar exposure FY2020-2024 (1 question) ---
    cur.execute("""
        SELECT se.fiscal_year, se.revenue_pct, ff.value as rev
        FROM supply_edges se
        JOIN financial_facts ff ON ff.ticker=se.supplier_ticker AND ff.label='Revenue' AND ff.fiscal_year=se.fiscal_year
        WHERE se.supplier_ticker='QRVO' AND se.customer_ticker='AAPL'
          AND se.threshold_only=FALSE AND se.fiscal_year BETWEEN 2020 AND 2024
        ORDER BY se.fiscal_year
    """)
    trend_rows = cur.fetchall()
    if trend_rows:
        trend_ev = {r["fiscal_year"]: round(float(r["rev"]) * r["revenue_pct"] / 100) for r in trend_rows}
        traversal = [edge_truth_from_edge("QRVO", "AAPL", r["fiscal_year"], r["revenue_pct"], False) for r in trend_rows]
        questions.append({
            "id":          "t5_trend_qrvo_apple_exposure_2020_2024",
            "sc_type":     "T5",
            "sc_subtype":  "graph_trend",
            "question":    "How has Qorvo's absolute dollar exposure to Apple changed from fiscal year 2020 to 2024? Provide the revenue derived from Apple for each year.",
            "supplier":    "QRVO",
            "customer":    "AAPL",
            "fiscal_year": "2020-2024",
            "expected_value": trend_ev,
            "expected_unit": "USD_by_year",
            "tolerance_pct": 2.0,
            "answerable":  True,
            "traversal_ground_truth": traversal,
            "scoring":     "trend",
        })

    # --- Distributor exposure (1 question) ---
    rev_avgo = _get_fact(cur, "AVGO", "Revenue", 2022)
    edge_wt = _get_edge(cur, "AVGO", "WT Microelectronics", 2022)
    if rev_avgo and edge_wt:
        wt_pct = edge_wt["revenue_pct"]
        wt_rev = round(rev_avgo * wt_pct / 100)
        questions.append({
            "id":          "t5_exposure_avgo_wt_micro_2022",
            "sc_type":     "T5",
            "sc_subtype":  "graph_compute",
            "question":    "How much revenue did Broadcom derive from WT Microelectronics in fiscal year 2022, based on its disclosed concentration percentage?",
            "supplier":    "AVGO",
            "customer":    "WT Microelectronics",
            "fiscal_year": 2022,
            "expected_value": wt_rev,
            "expected_unit": "USD",
            "tolerance_pct": 2.0,
            "answerable":  True,
            "formula":     "Revenue * wt_pct / 100",
            "input_values": {"Revenue": rev_avgo, "wt_pct": wt_pct},
            "traversal_ground_truth": [edge_truth_from_edge("AVGO", "WT Microelectronics", 2022, wt_pct, False)],
            "scoring":     "numeric",
        })

    # --- JBL multi-customer FY2020 (1 question) ---
    rev_jbl = _get_fact(cur, "JBL", "Revenue", 2020)
    edge_aapl = _get_edge(cur, "JBL", "AAPL", 2020)
    edge_amzn = _get_edge(cur, "JBL", "AMZN", 2020)
    if rev_jbl and edge_aapl and edge_amzn:
        a_pct = edge_aapl["revenue_pct"]
        az_pct = edge_amzn["revenue_pct"]
        questions.append({
            "id":          "t5_compare_jbl_apple_amazon_2020",
            "sc_type":     "T5",
            "sc_subtype":  "graph_comparison",
            "question":    "In fiscal year 2020, how did Jabil's revenue from Apple compare to its revenue from Amazon in absolute dollar terms? Which was larger?",
            "supplier":    "JBL",
            "fiscal_year": 2020,
            "expected_value": {
                "AAPL": round(rev_jbl * a_pct / 100),
                "AMZN": round(rev_jbl * az_pct / 100),
            },
            "expected_unit": "USD_by_customer",
            "tolerance_pct": 2.0,
            "answerable":  True,
            "traversal_ground_truth": [
                edge_truth_from_edge("JBL", "AAPL", 2020, a_pct, False),
                edge_truth_from_edge("JBL", "AMZN", 2020, az_pct, False),
            ],
            "scoring":     "llm_judge",
        })

    return questions


def edge_truth_from_edge(supplier, customer, fy, pct, threshold):
    return {"supplier": supplier, "customer": customer,
            "fiscal_year": fy, "revenue_pct": pct, "threshold_only": threshold}


# ── T6: Unanswerable ──────────────────────────────────────────────────────────

def build_t6() -> list[dict]:
    return [
        # Direction-reversed: customer-side queries (no Apple procurement data in EDGAR)
        {
            "id": "t6_reversed_apple_procurement_qrvo",
            "sc_type": "T6", "sc_subtype": "direction_reversed",
            "question": "What percentage of Apple's total procurement budget is spent on Qorvo components?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Apple's 10-K does not disclose supplier-level procurement percentages; data only exists from the supplier side.",
        },
        {
            "id": "t6_reversed_apple_spend_crus",
            "sc_type": "T6", "sc_subtype": "direction_reversed",
            "question": "How much does Apple pay Cirrus Logic annually for audio chips?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Apple does not disclose supplier-level spend. Only CRUS's perspective (% of CRUS revenue from Apple) is available.",
        },
        {
            "id": "t6_reversed_apple_depend_swks",
            "sc_type": "T6", "sc_subtype": "direction_reversed",
            "question": "What share of Apple's RF component purchases come from Skyworks?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Customer-side procurement data is not disclosed in EDGAR filings.",
        },
        {
            "id": "t6_reversed_apple_importance_jbl",
            "sc_type": "T6", "sc_subtype": "direction_reversed",
            "question": "How critical is Jabil to Apple's supply chain — what % of Apple's manufacturing is handled by Jabil?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Apple does not disclose its reliance on specific suppliers in its 10-K.",
        },
        {
            "id": "t6_reversed_apple_bom_avgo",
            "sc_type": "T6", "sc_subtype": "direction_reversed",
            "question": "What percentage of Apple's iPhone bill of materials cost comes from Broadcom?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "BOM-level data is proprietary and not disclosed in any EDGAR filing.",
        },

        # Data gaps: companies that never name their >10% customer
        {
            "id": "t6_gap_glw_named_customer_2023",
            "sc_type": "T6", "sc_subtype": "data_gap",
            "question": "Which specific company is Corning's largest customer that accounts for more than 10% of its revenue in fiscal year 2023?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Corning's 10-K discloses the threshold (>10%) but does not name the customer. The customer identity cannot be verified from EDGAR.",
        },
        {
            "id": "t6_gap_txn_customer_2023",
            "sc_type": "T6", "sc_subtype": "data_gap",
            "question": "Who is Texas Instruments' largest single customer and what percentage of revenue do they represent in FY2023?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Texas Instruments does not disclose any single customer exceeding 10% of revenue in its 10-K.",
        },
        {
            "id": "t6_gap_swks_exact_pct_2024",
            "sc_type": "T6", "sc_subtype": "data_gap",
            "question": "What is the exact percentage of Skyworks Solutions' revenue attributable to Apple in fiscal year 2024?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Skyworks' 10-K states only that Apple 'constituted more than ten percent' — no exact figure is disclosed. The exact percentage is not available from EDGAR.",
        },
        {
            "id": "t6_gap_swks_exact_pct_2022",
            "sc_type": "T6", "sc_subtype": "data_gap",
            "question": "What exact percentage of Skyworks' FY2022 revenue came from Apple?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Skyworks only discloses a threshold (>10%) for Apple in its 10-K filings from FY2019 onwards.",
        },

        # Out-of-scope: entities not in our dataset
        {
            "id": "t6_oos_tsmc_apple_2024",
            "sc_type": "T6", "sc_subtype": "out_of_scope",
            "question": "What percentage of TSMC's revenue comes from Apple in fiscal year 2024?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "TSMC files a 20-F (foreign private issuer) with the SEC, not a 10-K. It is not in the dataset.",
        },
        {
            "id": "t6_oos_foxconn_apple_2023",
            "sc_type": "T6", "sc_subtype": "out_of_scope",
            "question": "How dependent is Foxconn on Apple as a customer in fiscal year 2023?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Foxconn (Hon Hai) is a foreign private issuer and does not file 10-K EDGAR disclosures. Not in dataset.",
        },
        {
            "id": "t6_oos_mediatek_customer_2023",
            "sc_type": "T6", "sc_subtype": "out_of_scope",
            "question": "Does MediaTek have Apple as a major customer in fiscal year 2023?",
            "answerable": False,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "MediaTek is a Taiwanese company that does not file with the SEC. Not in dataset.",
        },

        # Metric unavailable
        {
            "id": "t6_metric_qrvo_fcf_2024",
            "sc_type": "T6", "sc_subtype": "metric_unavailable",
            "question": "What was Qorvo's free cash flow in fiscal year 2024?",
            "answerable": False,
            "ticker": "QRVO", "fiscal_year": 2024,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Free cash flow is not a reported XBRL metric and is not stored in the database.",
        },
        {
            "id": "t6_metric_crus_div_payout_2024",
            "sc_type": "T6", "sc_subtype": "metric_unavailable",
            "question": "What is Cirrus Logic's dividend payout ratio for fiscal year 2024?",
            "answerable": False,
            "ticker": "CRUS", "fiscal_year": 2024,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Cirrus Logic does not pay dividends. Payout ratio is undefined.",
        },
        {
            "id": "t6_metric_aapl_china_rev_2024",
            "sc_type": "T6", "sc_subtype": "metric_unavailable",
            "question": "What percentage of Apple's revenue came from China in fiscal year 2024?",
            "answerable": False,
            "ticker": "AAPL", "fiscal_year": 2024,
            "expected_value": None, "expected_unit": None, "tolerance_pct": None,
            "refusal_reason": "Geographic revenue splits are not stored as XBRL metrics in this database.",
        },
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/eval_set_sc.json")
    args = parser.parse_args()

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    print("Building T1 …")
    t1 = build_t1(cur)
    print(f"  {len(t1)} questions")

    print("Building T2 …")
    t2 = build_t2(cur)
    print(f"  {len(t2)} questions")

    # T3 (qualitative retrieval) excluded — not central to the error-migration thesis.
    # Kept in build_t3() for future use; re-add here when needed.
    t3: list[dict] = []

    print("Building T4 …")
    t4 = build_t4(cur)
    print(f"  {len(t4)} questions")

    print("Building T5 …")
    t5 = build_t5(cur)
    print(f"  {len(t5)} questions")

    print("Building T6 …")
    t6 = build_t6()
    print(f"  {len(t6)} questions")

    cur.close()
    conn.close()

    all_questions = t1 + t2 + t3 + t4 + t5 + t6
    total = len(all_questions)

    # Check for duplicate IDs
    ids = [q["id"] for q in all_questions]
    dupes = [i for i in ids if ids.count(i) > 1]
    if dupes:
        print(f"  WARNING: duplicate IDs: {set(dupes)}")

    dataset = {
        "version": "1.0",
        "built_at": datetime.utcnow().isoformat() + "Z",
        "total": total,
        "counts": {
            "T1": len(t1), "T2": len(t2),
            "T4": len(t4), "T5": len(t5), "T6": len(t6),
        },
        "items": all_questions,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    print(f"\nDataset written to {args.out}")
    print(f"Total: {total} questions  (T1={len(t1)} T2={len(t2)} T4={len(t4)} T5={len(t5)} T6={len(t6)})")


if __name__ == "__main__":
    main()
