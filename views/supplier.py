"""Supplier Deep Dive — one supplier's dependency and revenue trend over time."""

import altair as alt
import pandas as pd
import streamlit as st

from _dash_common import DEFAULT_COLOR, SUPPLIER_COLORS, get_json, warm_badge

st.title("🔍 Supplier Deep Dive")
warm_badge()

col1, col2 = st.columns(2)
customer = col2.selectbox("Customer", ["AAPL"], index=0)

# The options come from the data, not from SUPPLIER_COLORS. That dict assigns a
# stable hue per ticker and deliberately covers more tickers than any one
# deployment serves, so using its keys as the option list offered suppliers the
# database has no disclosures for -- dead ends that render only an "in the
# database" notice. Colour assignment and entity availability are separate
# concerns; the fallback below applies only if the API itself is unreachable.
_exposure = get_json("/dashboard/exposure", {"customer": customer})
_options = [e["supplier"] for e in (_exposure or {}).get("edges", [])] or list(SUPPLIER_COLORS)
_options = sorted(dict.fromkeys(_options))
ticker = col1.selectbox("Supplier", _options,
                        index=_options.index("CRUS") if "CRUS" in _options else 0)

data = get_json(f"/dashboard/supplier/{ticker}", {"customer": customer})
if data is None:
    st.stop()

trend = data.get("trend", [])
if not trend:
    st.info(f"No named {customer}-dependency disclosures found for {ticker} in the database.")
    st.stop()

color = SUPPLIER_COLORS.get(ticker, DEFAULT_COLOR)
df = pd.DataFrame(trend)
df["fiscal_year"] = df["fiscal_year"].astype(int)

threshold_years = [r["fiscal_year"] for r in trend if r["threshold_only"]]
if threshold_years:
    st.caption(
        f"FY {', '.join(str(y) for y in threshold_years)}: 10-K disclosed only \"more than ten "
        f"percent\" — plotted at 10% as a floor, not the exact figure."
    )

st.subheader(f"{ticker} dependency on {customer}")
dep_line = (
    alt.Chart(df)
    .mark_line(size=2, point=alt.OverlayMarkDef(size=60, filled=True), color=color)
    .encode(
        x=alt.X("fiscal_year:O", title="Fiscal year"),
        y=alt.Y("revenue_pct:Q", title=f"% of {ticker} revenue from {customer}", scale=alt.Scale(domain=[0, 100])),
        tooltip=[
            alt.Tooltip("fiscal_year:O", title="Fiscal year"),
            alt.Tooltip("revenue_pct:Q", title="Dependency %"),
            alt.Tooltip("threshold_only:N", title="Threshold only"),
        ],
    )
)
st.altair_chart(dep_line, use_container_width=True)

st.subheader(f"{ticker} total revenue")
rev_df = df.dropna(subset=["revenue"])
if rev_df.empty:
    st.caption("No revenue data available for these fiscal years.")
else:
    rev_line = (
        alt.Chart(rev_df)
        .mark_line(size=2, point=alt.OverlayMarkDef(size=60, filled=True), color=color)
        .encode(
            x=alt.X("fiscal_year:O", title="Fiscal year"),
            y=alt.Y("revenue:Q", title="Total revenue (USD)", scale=alt.Scale(zero=True)),
            tooltip=[
                alt.Tooltip("fiscal_year:O", title="Fiscal year"),
                alt.Tooltip("revenue:Q", title="Revenue", format=",.0f"),
            ],
        )
    )
    st.altair_chart(rev_line, use_container_width=True)

st.markdown("#### Source disclosures")
for row in sorted(trend, key=lambda r: r["fiscal_year"], reverse=True):
    pct_str = f">{row['revenue_pct']:.0f}% (threshold only)" if row["threshold_only"] else f"{row['revenue_pct']:.0f}%"
    with st.expander(f"FY{row['fiscal_year']} · {pct_str}"):
        st.markdown(f"_{row['citation']}_")
        if row["source_text"]:
            st.caption(f'"{row["source_text"]}"')
