from copilot.storage.db import get_conn

ALIASES = {
    'apple': 'AAPL',
    'apple inc': 'AAPL',
    'apple inc.': 'AAPL',
    'samsung': '005930.KS',
    'samsung electronics': '005930.KS',
    'samsung electronics co., ltd.': '005930.KS',
    'samsung electronics co., ltd': '005930.KS',
    '005930': '005930.KS',
}

conn = get_conn()
cur = conn.cursor()

# Step 1: delete non-canonical rows where canonical already exists
for raw, canonical in ALIASES.items():
    cur.execute("""
        DELETE FROM supply_edges a
        USING supply_edges b
        WHERE LOWER(a.customer_ticker) = %s
          AND b.customer_ticker        = %s
          AND a.supplier_ticker        = b.supplier_ticker
          AND a.fiscal_year            = b.fiscal_year
          AND a.accn                   = b.accn
          AND a.id                    != b.id
    """, (raw.lower(), canonical))
    if cur.rowcount:
        print(f"  deleted {cur.rowcount} rows shadowed by canonical {canonical!r}")

conn.commit()

# Step 2: update remaining non-canonical rows
for raw, canonical in ALIASES.items():
    cur.execute(
        "UPDATE supply_edges SET customer_ticker=%s WHERE LOWER(customer_ticker)=%s",
        (canonical, raw.lower())
    )
    if cur.rowcount:
        print(f"  {raw!r} -> {canonical!r}  ({cur.rowcount} rows updated)")

conn.commit()

# Step 3: remove any leftover duplicates
cur.execute("""
    DELETE FROM supply_edges WHERE id NOT IN (
        SELECT MIN(id) FROM supply_edges
        GROUP BY supplier_ticker, customer_ticker, fiscal_year, revenue_pct, disclosure_status
    )
""")
print(f"\nDeleted {cur.rowcount} additional duplicates")
conn.commit()

# Show result
cur.execute("""
    SELECT supplier_ticker, customer_ticker, revenue_pct, fiscal_year, disclosure_status
    FROM supply_edges WHERE disclosure_status='named'
    ORDER BY supplier_ticker, fiscal_year DESC, revenue_pct DESC
""")
rows = cur.fetchall()
print(f"\nNamed edges after normalization: {len(rows)}\n")
for r in rows:
    print(f"  {r['supplier_ticker']} -> {r['customer_ticker']}   {r['revenue_pct']}%   FY{r['fiscal_year']}")

conn.close()
