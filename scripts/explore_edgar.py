"""
Week 0 exploration: verify EDGAR XBRL API works and understand data shape.

Usage:
    python scripts/explore_edgar.py
    python scripts/explore_edgar.py --ticker AAPL
    python scripts/explore_edgar.py --ticker SWKS --metric Revenues
"""

import argparse
import json
import time

import httpx

EDGAR_HEADERS = {"User-Agent": "financial-copilot research renxiangchao2678@gmail.com"}

# Apple supplier cluster
CLUSTER = {
    "AAPL": "Apple Inc.",
    "SWKS": "Skyworks Solutions",
    "QRVO": "Qorvo Inc.",
    "CRUS": "Cirrus Logic Inc.",
    "GLW": "Corning Inc.",
    "AVGO": "Broadcom Inc.",
}

# Common XBRL tags for key metrics (US-GAAP)
KEY_METRICS = {
    "Revenues": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "GrossProfit": ["GrossProfit"],
    "NetIncome": ["NetIncomeLoss"],
    "OperatingIncome": ["OperatingIncomeLoss"],
    "EPS": ["EarningsPerShareBasic", "EarningsPerShareDiluted"],
    "TotalAssets": ["Assets"],
    "TotalDebt": ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
}


def get_cik(ticker: str) -> str:
    """Resolve ticker to zero-padded 10-digit CIK."""
    url = "https://efts.sec.gov/LATEST/search-index?q=%22{}%22&dateRange=custom&startdt=2020-01-01&enddt=2025-01-01&forms=10-K".format(ticker)
    # Use the company tickers JSON endpoint instead
    resp = httpx.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=EDGAR_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    for entry in data.values():
        if entry["ticker"].upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    raise ValueError(f"Ticker {ticker} not found in EDGAR company list")


def get_company_facts(cik: str) -> dict:
    """Fetch full XBRL companyfacts for a CIK."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    resp = httpx.get(url, headers=EDGAR_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extract_annual_metric(facts: dict, tag: str) -> list[dict]:
    """Extract annual (10-K) values for a single XBRL tag."""
    try:
        units = facts["facts"]["us-gaap"][tag]["units"]
    except KeyError:
        return []

    rows = []
    for unit_key, filings in units.items():
        for f in filings:
            if f.get("form") == "10-K" and f.get("fp") == "FY":
                rows.append({
                    "tag": tag,
                    "unit": unit_key,
                    "value": f["val"],
                    "fy": f.get("fy"),
                    "end": f.get("end"),
                    "accn": f.get("accn"),
                })
    # deduplicate by (fy, end) keeping latest accn
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (r["fy"], r["end"])
        if key not in seen or r["accn"] > seen[key]["accn"]:
            seen[key] = r
    return sorted(seen.values(), key=lambda x: x["end"] or "")


def print_metric_table(ticker: str, facts: dict, metric_label: str, tags: list[str]) -> None:
    rows = []
    for tag in tags:
        rows.extend(extract_annual_metric(facts, tag))
    if not rows:
        print(f"  [{metric_label}] No data found for tags: {tags}")
        return
    # keep last 4 FY
    rows = rows[-4:]
    print(f"\n  {metric_label}:")
    for r in rows:
        val_fmt = f"{r['value']:>20,.0f}" if isinstance(r['value'], (int, float)) else str(r['value'])
        print(f"    FY{r['fy']}  end={r['end']}  {val_fmt}  ({r['unit']})  [{r['tag']}]")


def explore_company(ticker: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {ticker} — {CLUSTER.get(ticker, 'unknown')}")
    print(f"{'='*60}")

    cik = get_cik(ticker)
    print(f"  CIK: {cik}")

    facts = get_company_facts(cik)
    gaap_tags = list(facts.get("facts", {}).get("us-gaap", {}).keys())
    print(f"  Total US-GAAP tags available: {len(gaap_tags)}")

    for label, tags in KEY_METRICS.items():
        print_metric_table(ticker, facts, label, tags)


def main() -> None:
    parser = argparse.ArgumentParser(description="Explore EDGAR XBRL data")
    parser.add_argument("--ticker", default=None, help="Single ticker (default: all cluster)")
    parser.add_argument("--metric", default=None, help="Single metric label to show")
    parser.add_argument("--dump-tags", action="store_true", help="Dump all available US-GAAP tags")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else list(CLUSTER.keys())

    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(0.3)  # be polite to EDGAR
        try:
            explore_company(ticker)
        except Exception as e:
            print(f"  ERROR for {ticker}: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
