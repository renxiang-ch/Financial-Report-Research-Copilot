"""
Generate the frozen eval dataset from the database.

Pulls ground-truth values directly from financial_facts (no LLM involved),
computes Tier-2 answers with Python arithmetic, and writes data/eval_set.json.

Re-run whenever the company cluster or ingested years change.
The output JSON is checked in and treated as frozen for reproducibility.

Usage:
    python -m copilot.eval.generate_dataset
    python -m copilot.eval.generate_dataset --out data/eval_set.json
"""

import argparse
import json
import math
from pathlib import Path

from copilot.storage.db import get_conn

# ── helpers ──────────────────────────────────────────────────────────────────

def fetch_value(cur, ticker: str, label: str, fiscal_year: int) -> float | None:
    cur.execute(
        """
        SELECT value FROM financial_facts
        WHERE ticker = %s AND label = %s AND fiscal_year = %s AND form = '10-K'
        ORDER BY period_end DESC LIMIT 1
        """,
        (ticker, label, fiscal_year),
    )
    row = cur.fetchone()
    return float(row["value"]) if row else None


def pct(numerator: float, denominator: float) -> float:
    return round(numerator / denominator * 100, 4)


def yoy_growth(new: float, old: float) -> float:
    return round((new - old) / old * 100, 4)


# ── question builders ─────────────────────────────────────────────────────────

def tier1(id_: str, question: str, ticker: str, fiscal_year: int,
          value: float, unit: str = "USD", tol: float = 0.5) -> dict:
    return {
        "id": id_,
        "tier": 1,
        "question": question,
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "expected_value": value,
        "expected_unit": unit,
        "tolerance_pct": tol,
        "answerable": True,
    }


def tier2(id_: str, question: str, ticker: str, fiscal_year: int,
          value: float, unit: str = "%", formula: str = "",
          tol: float = 0.5) -> dict:
    return {
        "id": id_,
        "tier": 2,
        "question": question,
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "expected_value": value,
        "expected_unit": unit,
        "tolerance_pct": tol,
        "answerable": True,
        "formula": formula,
    }


def unanswerable(id_: str, question: str, ticker: str | None = None) -> dict:
    return {
        "id": id_,
        "tier": 1,
        "question": question,
        "ticker": ticker,
        "fiscal_year": None,
        "expected_value": None,
        "expected_unit": None,
        "tolerance_pct": None,
        "answerable": False,
    }


# ── main generation ───────────────────────────────────────────────────────────

def generate(out_path: Path) -> list[dict]:
    conn = get_conn()
    items: list[dict] = []

    with conn.cursor() as cur:

        # ── Tier 1: direct single-metric lookups ──────────────────────────────

        tier1_specs = [
            # (ticker, label, fiscal_year, question_template, unit)
            ("AAPL", "Revenue",         2024, "What was Apple's total revenue in fiscal year 2024?",                       "USD"),
            ("AAPL", "Revenue",         2023, "What was Apple's total revenue in fiscal year 2023?",                       "USD"),
            ("AAPL", "Revenue",         2022, "What was Apple's total revenue in fiscal year 2022?",                       "USD"),
            ("AAPL", "GrossProfit",     2024, "What was Apple's gross profit in fiscal year 2024?",                        "USD"),
            ("AAPL", "NetIncome",       2024, "What was Apple's net income in fiscal year 2024?",                          "USD"),
            ("AAPL", "NetIncome",       2023, "What was Apple's net income in fiscal year 2023?",                          "USD"),
            ("AAPL", "OperatingIncome", 2024, "What was Apple's operating income in fiscal year 2024?",                    "USD"),
            ("AAPL", "OperatingIncome", 2023, "What was Apple's operating income in fiscal year 2023?",                    "USD"),
            ("AAPL", "R&D",             2024, "How much did Apple spend on research and development in fiscal year 2024?", "USD"),
            ("AAPL", "EPS_Diluted",     2024, "What was Apple's diluted EPS in fiscal year 2024?",                        "USD"),
            ("AAPL", "EPS_Diluted",     2023, "What was Apple's diluted EPS in fiscal year 2023?",                        "USD"),

            ("SWKS", "Revenue",         2024, "What was Skyworks Solutions' revenue in fiscal year 2024?",                 "USD"),
            ("SWKS", "Revenue",         2023, "What was Skyworks Solutions' revenue in fiscal year 2023?",                 "USD"),
            ("SWKS", "GrossProfit",     2024, "What was Skyworks Solutions' gross profit in fiscal year 2024?",            "USD"),
            ("SWKS", "NetIncome",       2024, "What was Skyworks Solutions' net income in fiscal year 2024?",              "USD"),
            ("SWKS", "R&D",             2024, "How much did Skyworks spend on R&D in fiscal year 2024?",                  "USD"),

            ("QRVO", "Revenue",         2024, "What was Qorvo's total revenue in fiscal year 2024?",                      "USD"),
            ("QRVO", "Revenue",         2023, "What was Qorvo's total revenue in fiscal year 2023?",                      "USD"),
            ("QRVO", "NetIncome",       2024, "What was Qorvo's net income in fiscal year 2024?",                         "USD"),
            ("QRVO", "EPS_Diluted",     2024, "What was Qorvo's diluted EPS in fiscal year 2024?",                        "USD"),

            ("CRUS", "Revenue",         2024, "What was Cirrus Logic's revenue in fiscal year 2024?",                     "USD"),
            ("CRUS", "GrossProfit",     2023, "What was Cirrus Logic's gross profit in fiscal year 2023?",                 "USD"),
            ("CRUS", "NetIncome",       2024, "What was Cirrus Logic's net income in fiscal year 2024?",                   "USD"),

            ("GLW",  "Revenue",         2024, "What was Corning's total revenue in fiscal year 2024?",                    "USD"),
            ("GLW",  "NetIncome",       2022, "What was Corning's net income in fiscal year 2022?",                       "USD"),
            ("GLW",  "R&D",             2024, "How much did Corning spend on R&D in fiscal year 2024?",                   "USD"),

            ("AVGO", "Revenue",         2024, "What was Broadcom's total revenue in fiscal year 2024?",                   "USD"),
            ("AVGO", "GrossProfit",     2024, "What was Broadcom's gross profit in fiscal year 2024?",                    "USD"),
            ("AVGO", "R&D",             2024, "How much did Broadcom spend on R&D in fiscal year 2024?",                  "USD"),
        ]

        for ticker, label, fy, question, unit in tier1_specs:
            val = fetch_value(cur, ticker, label, fy)
            if val is None:
                print(f"  SKIP (no data): {ticker} {label} {fy}")
                continue
            id_ = f"t1_{ticker.lower()}_{label.lower().replace('&','and')}_{fy}"
            items.append(tier1(id_, question, ticker, fy, val, unit))

        # ── Tier 2: multi-step calculations ───────────────────────────────────

        tier2_specs = [
            # (ticker, fy, metric_a, metric_b, formula_str, question, unit)
            ("AAPL", 2024, "GrossProfit",     "Revenue",
             "GrossProfit / Revenue * 100",
             "What was Apple's gross margin percentage in fiscal year 2024?"),
            ("AAPL", 2023, "GrossProfit",     "Revenue",
             "GrossProfit / Revenue * 100",
             "What was Apple's gross margin percentage in fiscal year 2023?"),
            ("AAPL", 2022, "GrossProfit",     "Revenue",
             "GrossProfit / Revenue * 100",
             "What was Apple's gross margin percentage in fiscal year 2022?"),
            ("AAPL", 2024, "OperatingIncome", "Revenue",
             "OperatingIncome / Revenue * 100",
             "What was Apple's operating margin percentage in fiscal year 2024?"),
            ("AAPL", 2024, "NetIncome",       "Revenue",
             "NetIncome / Revenue * 100",
             "What was Apple's net profit margin in fiscal year 2024?"),
            ("AAPL", 2024, "R&D",             "Revenue",
             "R&D / Revenue * 100",
             "What percentage of Apple's revenue was spent on R&D in fiscal year 2024?"),

            ("SWKS", 2024, "GrossProfit",     "Revenue",
             "GrossProfit / Revenue * 100",
             "What was Skyworks Solutions' gross margin percentage in fiscal year 2024?"),
            ("SWKS", 2024, "OperatingIncome", "Revenue",
             "OperatingIncome / Revenue * 100",
             "What was Skyworks Solutions' operating margin in fiscal year 2024?"),

            ("QRVO", 2024, "GrossProfit",     "Revenue",
             "GrossProfit / Revenue * 100",
             "What was Qorvo's gross margin percentage in fiscal year 2024?"),

            ("AVGO", 2024, "GrossProfit",     "Revenue",
             "GrossProfit / Revenue * 100",
             "What was Broadcom's gross margin percentage in fiscal year 2024?"),
            ("AVGO", 2024, "OperatingIncome", "Revenue",
             "OperatingIncome / Revenue * 100",
             "What was Broadcom's operating margin in fiscal year 2024?"),

            ("CRUS", 2024, "OperatingIncome", "Revenue",
             "OperatingIncome / Revenue * 100",
             "What was Cirrus Logic's operating margin percentage in fiscal year 2024?"),

            ("GLW",  2024, "GrossProfit",     "Revenue",
             "GrossProfit / Revenue * 100",
             "What was Corning's gross margin percentage in fiscal year 2024?"),
        ]

        for ticker, fy, label_a, label_b, formula, question in tier2_specs:
            a = fetch_value(cur, ticker, label_a, fy)
            b = fetch_value(cur, ticker, label_b, fy)
            if a is None or b is None or b == 0:
                print(f"  SKIP (missing data): {ticker} {label_a}/{label_b} {fy}")
                continue
            val = pct(a, b)
            id_ = f"t2_{ticker.lower()}_{label_a.lower().replace('&','and')}_pct_{fy}"
            items.append(tier2(id_, question, ticker, fy, val, "%", formula))

        # ── Tier 2: YoY revenue growth ────────────────────────────────────────

        yoy_specs = [
            ("AAPL", 2023, 2024,
             "By what percentage did Apple's revenue grow from fiscal year 2023 to 2024?"),
            ("AAPL", 2022, 2023,
             "By what percentage did Apple's revenue change from fiscal year 2022 to 2023?"),
            ("SWKS", 2023, 2024,
             "By what percentage did Skyworks' revenue change from fiscal year 2023 to 2024?"),
            ("AVGO", 2023, 2024,
             "By what percentage did Broadcom's revenue grow from fiscal year 2023 to 2024?"),
        ]

        for ticker, fy_old, fy_new, question in yoy_specs:
            old = fetch_value(cur, ticker, "Revenue", fy_old)
            new = fetch_value(cur, ticker, "Revenue", fy_new)
            if old is None or new is None or old == 0:
                continue
            val = yoy_growth(new, old)
            id_ = f"t2_{ticker.lower()}_revenue_yoy_{fy_old}_{fy_new}"
            formula = f"(Revenue_{fy_new} - Revenue_{fy_old}) / Revenue_{fy_old} * 100"
            items.append(tier2(id_, question, ticker, fy_new, val, "%", formula))

    conn.close()

    # ── Unanswerable questions ────────────────────────────────────────────────
    # These test that the agent refuses rather than fabricates.

    unanswerable_specs = [
        ("unans_aapl_fcf_2024",
         "What was Apple's free cash flow in fiscal year 2024?",
         "AAPL"),
        ("unans_aapl_china_rev_2023",
         "What percentage of Apple's revenue came from China in fiscal year 2023?",
         "AAPL"),
        ("unans_swks_dte_2024",
         "What was Skyworks Solutions' debt-to-equity ratio in fiscal year 2024?",
         "SWKS"),
        ("unans_tsmc_rev_2024",
         "What was TSMC's revenue in fiscal year 2024?",
         None),
        ("unans_aapl_div_yield_2024",
         "What was Apple's dividend yield in fiscal year 2024?",
         "AAPL"),
    ]

    for id_, question, ticker in unanswerable_specs:
        items.append(unanswerable(id_, question, ticker))

    # ── Retrieval questions ───────────────────────────────────────────────────
    # golden_citations: list of {ticker, section} that should appear in agent citations.
    # golden_answer: reference answer written from actual 10-K text (for LLM-judge).

    retrieval_items = [
        {
            "id": "ret_swks_apple_concentration_2024",
            "tier": 1,
            "type": "retrieval",
            "question": "How dependent is Skyworks Solutions on Apple as a customer, according to its fiscal year 2024 10-K?",
            "ticker": "SWKS",
            "fiscal_year": 2024,
            "answerable": True,
            "expected_value": None,
            "expected_unit": None,
            "tolerance_pct": None,
            "golden_citations": [{"ticker": "SWKS", "section": "Business"}],
            "golden_answer": (
                "In each of fiscal years 2024, 2023, and 2022, Apple — through sales to multiple "
                "distributors and contract manufacturers for smartphones, tablets, computers, watches, "
                "and other devices — constituted more than ten percent of Skyworks' net revenue. "
                "Additionally, Skyworks' three largest accounts receivable balances comprised 80% and "
                "83% of aggregate gross accounts receivable as of the end of fiscal 2024 and 2023, respectively."
            ),
            "keywords": ["Apple", "ten percent", "net revenue", "customer"],
        },
        {
            "id": "ret_swks_markets_served_2024",
            "tier": 1,
            "type": "retrieval",
            "question": "What end markets and applications does Skyworks Solutions serve?",
            "ticker": "SWKS",
            "fiscal_year": 2024,
            "answerable": True,
            "expected_value": None,
            "expected_unit": None,
            "tolerance_pct": None,
            "golden_citations": [{"ticker": "SWKS", "section": "Business"}],
            "golden_answer": (
                "Skyworks is a developer, manufacturer and provider of analog and mixed-signal semiconductor "
                "products for numerous applications including aerospace, automotive, broadband, cellular "
                "infrastructure, connected home, defense, entertainment and gaming, industrial, medical, "
                "smartphone, tablet, and wearables."
            ),
            "keywords": ["aerospace", "automotive", "smartphone", "semiconductor"],
        },
        {
            "id": "ret_avgo_vmware_acquisition",
            "tier": 1,
            "type": "retrieval",
            "question": "How does Broadcom describe its history and the role of the VMware acquisition?",
            "ticker": "AVGO",
            "fiscal_year": 2024,
            "answerable": True,
            "expected_value": None,
            "expected_unit": None,
            "tolerance_pct": None,
            "golden_citations": [{"ticker": "AVGO", "section": "Business"}],
            "golden_answer": (
                "Broadcom is a global technology leader designing semiconductor and infrastructure software "
                "solutions. Its history spans over 60 years originating from AT&T/Bell Labs, Lucent, and "
                "Hewlett-Packard, evolving through acquisitions including LSI Corporation, Broadcom Corporation, "
                "Brocade, CA Inc., Symantec Enterprise Security, and VMware, Inc. VMware is listed as a key "
                "part of Broadcom's expanding infrastructure software portfolio."
            ),
            "keywords": ["VMware", "acquisition", "infrastructure software", "semiconductor"],
        },
        {
            "id": "ret_aapl_product_categories",
            "tier": 1,
            "type": "retrieval",
            "question": "What product and service categories does Apple report revenue from in its 10-K?",
            "ticker": "AAPL",
            "fiscal_year": 2024,
            "answerable": True,
            "expected_value": None,
            "expected_unit": None,
            "tolerance_pct": None,
            "golden_citations": [{"ticker": "AAPL", "section": "Business"}],
            "golden_answer": (
                "Apple reports revenue across hardware products including iPhone, Mac, iPad, and Wearables, "
                "Home and Accessories (which includes Apple TV, HomePod, AirPods, Apple Watch). The Services "
                "segment includes Advertising (third-party licensing and advertising platforms), AppleCare "
                "(fee-based service and support), and other digital services."
            ),
            "keywords": ["iPhone", "Mac", "iPad", "Services", "AppleCare"],
        },
        {
            "id": "ret_aapl_applecare_description",
            "tier": 1,
            "type": "retrieval",
            "question": "How does Apple describe its AppleCare service offerings in its 10-K?",
            "ticker": "AAPL",
            "fiscal_year": 2024,
            "answerable": True,
            "expected_value": None,
            "expected_unit": None,
            "tolerance_pct": None,
            "golden_citations": [{"ticker": "AAPL", "section": "Business"}],
            "golden_answer": (
                "Apple offers a portfolio of fee-based service and support products under the AppleCare brand. "
                "The offerings provide priority access to Apple technical support and hardware repair services."
            ),
            "keywords": ["AppleCare", "fee-based", "support", "service"],
        },
        {
            "id": "ret_qrvo_customer_risk",
            "tier": 1,
            "type": "retrieval",
            "question": "What does Qorvo's 10-K say about customer concentration risk?",
            "ticker": "QRVO",
            "fiscal_year": 2024,
            "answerable": True,
            "expected_value": None,
            "expected_unit": None,
            "tolerance_pct": None,
            "golden_citations": [{"ticker": "QRVO", "section": "Risk Factors"}],
            "golden_answer": (
                "Qorvo discloses that a small number of customers account for a substantial portion of its "
                "revenue, and the loss of any such customer or a significant reduction in orders would "
                "materially harm its business. Apple is among the customers representing more than ten "
                "percent of net revenue."
            ),
            "keywords": ["customer", "concentration", "revenue", "Apple"],
        },
        {
            "id": "ret_glw_business_segments",
            "tier": 1,
            "type": "retrieval",
            "question": "What are Corning's main business segments described in its 10-K?",
            "ticker": "GLW",
            "fiscal_year": 2024,
            "answerable": True,
            "expected_value": None,
            "expected_unit": None,
            "tolerance_pct": None,
            "golden_citations": [{"ticker": "GLW", "section": "Business"}],
            "golden_answer": (
                "Corning operates through several business segments specializing in specialty glass and "
                "ceramics. Key segments include Optical Communications, Display Technologies, Specialty "
                "Materials, Environmental Technologies, and Life Sciences."
            ),
            "keywords": ["Optical", "Display", "specialty glass", "segment"],
        },
        {
            "id": "ret_swks_rf_customer_concentration_risk",
            "tier": 1,
            "type": "retrieval",
            "question": "What risk factors does Skyworks identify related to reliance on a small number of customers?",
            "ticker": "SWKS",
            "fiscal_year": 2024,
            "answerable": True,
            "expected_value": None,
            "expected_unit": None,
            "tolerance_pct": None,
            "golden_citations": [{"ticker": "SWKS", "section": "Risk Factors"}],
            "golden_answer": (
                "Skyworks identifies that its business, results of operations, and financial condition could "
                "be materially and adversely impacted by risks stemming from reliance on a concentrated "
                "customer base. A significant reduction in orders from a major customer like Apple could "
                "severely harm the company's revenue and profitability."
            ),
            "keywords": ["risk", "customer", "materially", "adversely"],
        },
    ]

    items.extend(retrieval_items)

    # ── write output ──────────────────────────────────────────────────────────

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"version": "1.0", "items": items}, f, indent=2)

    tier_counts = {}
    for item in items:
        k = f"tier{item['tier']}_{'answerable' if item['answerable'] else 'unanswerable'}"
        tier_counts[k] = tier_counts.get(k, 0) + 1

    print(f"Wrote {len(items)} questions to {out_path}")
    for k, v in sorted(tier_counts.items()):
        print(f"  {k}: {v}")

    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/eval_set.json")
    args = parser.parse_args()
    generate(Path(args.out))


if __name__ == "__main__":
    main()
