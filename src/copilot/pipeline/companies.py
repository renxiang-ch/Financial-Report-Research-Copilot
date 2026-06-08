"""
Central company registry for ingestion pipelines.

CLUSTER_V1  — original 6-company Apple-supplier prototype
CLUSTER_FB  — 31 additional companies from FinanceBench open-source dataset
CLUSTER_ALL — full combined list
"""

# Original Apple-supplier cluster (Stage 1 / prototype)
CLUSTER_V1: dict[str, str] = {
    "AAPL": "Apple Inc.",
    "SWKS": "Skyworks Solutions Inc.",
    "QRVO": "Qorvo Inc.",
    "CRUS": "Cirrus Logic Inc.",
    "GLW":  "Corning Inc.",
    "AVGO": "Broadcom Inc.",
}

# FinanceBench open-source companies (31 new; GLW already in V1)
CLUSTER_FB: dict[str, str] = {
    "MMM":   "3M Company",
    "ATVI":  "Activision Blizzard Inc.",   # CIK override in CIK_OVERRIDES
    "ADBE":  "Adobe Inc.",
    "AES":   "AES Corporation",
    "AMZN":  "Amazon.com Inc.",
    "AMCR":  "Amcor plc",
    "AXP":   "American Express Company",
    "AWK":   "American Water Works Company Inc.",
    "BBY":   "Best Buy Co. Inc.",
    "XYZ":   "Block Inc.",
    "BA":    "Boeing Company",
    "KO":    "Coca-Cola Company",
    "COST":  "Costco Wholesale Corporation",
    "CVS":   "CVS Health Corporation",
    "FL":    "Foot Locker Inc.",            # CIK override in CIK_OVERRIDES
    "GIS":   "General Mills Inc.",
    "JNJ":   "Johnson & Johnson",
    "JPM":   "JPMorgan Chase & Co.",
    "KHC":   "Kraft Heinz Company",
    "LMT":   "Lockheed Martin Corporation",
    "MGM":   "MGM Resorts International",
    "MSFT":  "Microsoft Corporation",
    "NFLX":  "Netflix Inc.",
    "NKE":   "Nike Inc.",
    "PYPL":  "PayPal Holdings Inc.",
    "PEP":   "PepsiCo Inc.",
    "PFE":   "Pfizer Inc.",
    "ULTA":  "Ulta Beauty Inc.",
    "VZ":    "Verizon Communications Inc.",
    "WMT":   "Walmart Inc.",
    "AMD":   "Advanced Micro Devices Inc.",
}

CLUSTER_ALL: dict[str, str] = {**CLUSTER_V1, **CLUSTER_FB}

# CIK overrides for companies whose ticker lookup fails in EDGAR
# (acquired, renamed, or delisted companies)
CIK_OVERRIDES: dict[str, str] = {
    "ATVI": "0000718877",  # Activision Blizzard — acquired by MSFT Oct 2023, no longer in active ticker list
    "FL":   "0000850209",  # Foot Locker — ticker not found via company_tickers.json
}
