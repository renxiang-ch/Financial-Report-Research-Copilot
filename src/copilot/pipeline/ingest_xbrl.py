"""
Ingest XBRL financial facts from EDGAR into Postgres.

Usage:
    python -m copilot.pipeline.ingest_xbrl --ticker AAPL
    python -m copilot.pipeline.ingest_xbrl                   # v1 cluster only
    python -m copilot.pipeline.ingest_xbrl --cluster all     # all 37 companies
    python -m copilot.pipeline.ingest_xbrl --cluster fb      # FinanceBench companies only
"""

import argparse
import time

import httpx

from copilot.pipeline.companies import CIK_OVERRIDES, CLUSTER_ALL, CLUSTER_FB, CLUSTER_V1
from copilot.storage.db import get_conn
from copilot.storage.schema import create_tables

EDGAR_HEADERS = {"User-Agent": "financial-copilot research renxiangchao2678@gmail.com"}

# XBRL tag → human label mapping
# v1: original 10 metrics
# scale-up: added CapEx, OCF, PP&E, CurrentAssets, CurrentLiabilities, Equity, Inventory, Tax, D&A
TAG_LABELS: dict[str, str] = {
    # ── Income Statement ──────────────────────────────────────────────────────
    "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
    "Revenues":                                            "Revenue",
    "GrossProfit":                                         "GrossProfit",
    "CostOfGoodsAndServicesSold":                         "COGS",
    "CostOfRevenue":                                       "COGS",
    "OperatingIncomeLoss":                                 "OperatingIncome",
    "NetIncomeLoss":                                       "NetIncome",
    "EarningsPerShareBasic":                               "EPS_Basic",
    "EarningsPerShareDiluted":                             "EPS_Diluted",
    "ResearchAndDevelopmentExpense":                       "R&D",
    "IncomeTaxExpenseBenefit":                             "IncomeTaxExpense",
    "InterestExpense":                                     "InterestExpense",
    "DepreciationDepletionAndAmortization":                "D&A",

    # ── Balance Sheet ─────────────────────────────────────────────────────────
    "Assets":                                              "TotalAssets",
    "AssetsCurrent":                                       "CurrentAssets",
    "LiabilitiesCurrent":                                  "CurrentLiabilities",
    "LongTermDebt":                                        "LongTermDebt",
    "PropertyPlantAndEquipmentNet":                        "PP&E",
    "StockholdersEquity":                                  "TotalEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "TotalEquity",
    "InventoryNet":                                        "Inventory",

    # ── Cash Flow Statement ───────────────────────────────────────────────────
    "NetCashProvidedByUsedInOperatingActivities":          "OperatingCashFlow",
    "PaymentsToAcquirePropertyPlantAndEquipment":          "CapEx",
}


def get_cik(ticker: str) -> str:
    if ticker.upper() in CIK_OVERRIDES:
        return CIK_OVERRIDES[ticker.upper()]
    resp = httpx.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=EDGAR_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    for entry in resp.json().values():
        if entry["ticker"].upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    raise ValueError(f"Ticker {ticker} not found")


def get_company_facts(cik: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    resp = httpx.get(url, headers=EDGAR_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extract_facts(ticker: str, facts: dict) -> list[dict]:
    rows = []
    gaap = facts.get("facts", {}).get("us-gaap", {})

    for tag, label in TAG_LABELS.items():
        if tag not in gaap:
            continue
        for unit_key, filings in gaap[tag].get("units", {}).items():
            for f in filings:
                if f.get("form") not in ("10-K", "10-Q"):
                    continue
                rows.append({
                    "ticker":         ticker,
                    "tag":            tag,
                    "label":          label,
                    "value":          f["val"],
                    "unit":           unit_key,
                    "period_end":     f["end"],
                    "fiscal_year":    f.get("fy"),
                    "fiscal_period":  f.get("fp"),
                    "form":           f.get("form"),
                    "accn":           f["accn"],
                })
    return rows


def upsert_company(conn, ticker: str, name: str, cik: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO companies (ticker, name, cik)
            VALUES (%s, %s, %s)
            ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name, cik = EXCLUDED.cik
            """,
            (ticker, name, cik),
        )
    conn.commit()


def upsert_facts(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """
                INSERT INTO financial_facts
                    (ticker, tag, label, value, unit, period_end, fiscal_year, fiscal_period, form, accn)
                VALUES
                    (%(ticker)s, %(tag)s, %(label)s, %(value)s, %(unit)s, %(period_end)s,
                     %(fiscal_year)s, %(fiscal_period)s, %(form)s, %(accn)s)
                ON CONFLICT (ticker, tag, period_end, accn) DO NOTHING
                """,
                r,
            )
    conn.commit()
    return len(rows)


def ingest_ticker(ticker: str, name: str, conn) -> None:
    print(f"[{ticker}] fetching CIK...")
    cik = get_cik(ticker)
    print(f"[{ticker}] CIK={cik}, fetching facts...")
    facts = get_company_facts(cik)
    upsert_company(conn, ticker, name, cik)
    rows = extract_facts(ticker, facts)
    inserted = upsert_facts(conn, rows)
    print(f"[{ticker}] upserted {inserted} fact rows")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default=None, help="Single ticker to ingest")
    parser.add_argument(
        "--cluster",
        default="v1",
        choices=["v1", "fb", "all"],
        help="v1=original 6, fb=FinanceBench 31, all=37 companies",
    )
    args = parser.parse_args()

    if args.ticker:
        cluster = CLUSTER_ALL
        ticker = args.ticker.upper()
        if ticker not in cluster:
            raise ValueError(f"{ticker} not in company registry — add it to companies.py")
        targets = {ticker: cluster[ticker]}
    else:
        targets = {"v1": CLUSTER_V1, "fb": CLUSTER_FB, "all": CLUSTER_ALL}[args.cluster]

    conn = get_conn()
    create_tables(conn)
    print(f"Tables ready. Ingesting {len(targets)} companies...")

    for i, (ticker, name) in enumerate(targets.items()):
        if i > 0:
            time.sleep(0.3)
        try:
            ingest_ticker(ticker, name, conn)
        except Exception as e:
            print(f"[{ticker}] ERROR: {e}")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
