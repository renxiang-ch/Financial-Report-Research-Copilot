"""
Ingest XBRL financial facts from EDGAR into Postgres.

Usage:
    python -m copilot.pipeline.ingest_xbrl --ticker AAPL
    python -m copilot.pipeline.ingest_xbrl                    # v1 cluster (6 companies)
    python -m copilot.pipeline.ingest_xbrl --cluster research # 15-company research cluster
"""

import argparse
import time

import httpx

from copilot.pipeline.companies import CIK_OVERRIDES, CLUSTER_RESEARCH, CLUSTER_V1
from copilot.storage.db import get_conn
from copilot.storage.schema import create_tables

EDGAR_HEADERS = {"User-Agent": "financial-copilot research renxiangchao2678@gmail.com"}

# XBRL tag → human label mapping
# Multiple tags can map to the same label; first match wins at query time.
TAG_LABELS: dict[str, str] = {
    # Income statement
    "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
    "Revenues":                                            "Revenue",
    "SalesRevenueNet":                                     "Revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "Revenue",
    "GrossProfit":                                         "GrossProfit",
    "CostOfGoodsAndServicesSold":                          "COGS",
    "CostOfRevenue":                                       "COGS",
    "CostOfGoodsSold":                                     "COGS",
    "OperatingIncomeLoss":                                 "OperatingIncome",
    "NetIncomeLoss":                                       "NetIncome",
    "EarningsPerShareBasic":                               "EPS_Basic",
    "EarningsPerShareDiluted":                             "EPS_Diluted",
    "ResearchAndDevelopmentExpense":                       "R&D",
    "IncomeTaxExpenseBenefit":                             "IncomeTaxExpense",
    "InterestExpense":                                     "InterestExpense",
    "InterestExpenseDebt":                                 "InterestExpense",
    "DepreciationDepletionAndAmortization":                "D&A",
    "DepreciationAndAmortization":                         "D&A",
    # Balance sheet
    "Assets":                                              "TotalAssets",
    "AssetsCurrent":                                       "CurrentAssets",
    "LiabilitiesCurrent":                                  "CurrentLiabilities",
    "LongTermDebt":                                        "LongTermDebt",
    "LongTermDebtNoncurrent":                              "LongTermDebt",
    "StockholdersEquity":                                  "TotalEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "TotalEquity",
    "PropertyPlantAndEquipmentNet":                        "PP&E",
    "InventoryNet":                                        "Inventory",
    # Cash flow
    "NetCashProvidedByUsedInOperatingActivities":          "OperatingCashFlow",
    "PaymentsToAcquirePropertyPlantAndEquipment":          "CapEx",
    "PaymentsForCapitalImprovements":                      "CapEx",
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
                    "ticker": ticker,
                    "tag": tag,
                    "label": label,
                    "value": f["val"],
                    "unit": unit_key,
                    "period_end": f["end"],
                    "fiscal_year": f.get("fy"),
                    "fiscal_period": f.get("fp"),
                    "form": f.get("form"),
                    "accn": f["accn"],
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
    parser.add_argument("--ticker", default=None)
    parser.add_argument(
        "--cluster",
        default="v1",
        choices=["v1", "research"],
        help="v1=original 6 companies, research=15-company SC-DisclosureQA cluster",
    )
    args = parser.parse_args()

    cluster = CLUSTER_RESEARCH if args.cluster == "research" else CLUSTER_V1
    tickers = {args.ticker.upper(): cluster.get(args.ticker.upper(), args.ticker.upper())} \
        if args.ticker else cluster

    conn = get_conn()
    create_tables(conn)
    print(f"Tables ready. Ingesting {len(tickers)} companies (cluster={args.cluster}).")

    for i, (ticker, name) in enumerate(tickers.items()):
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
