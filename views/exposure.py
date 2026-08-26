"""Company Exposure View — which suppliers are most dependent on a given
customer (default: Apple), as a supply-chain cluster diagram for one fiscal year.
"""

import math

import altair as alt
import pandas as pd
import streamlit as st

from _dash_common import SUPPLIER_COLORS, get_json, warm_badge

st.title("📊 Company Exposure View")
warm_badge()

customer = st.selectbox("Customer", ["AAPL"], index=0)

years_data = get_json("/dashboard/years", {"customer": customer})
if years_data is None:
    st.stop()
years = years_data.get("years", [])
if not years:
    st.info(f"No named customer-concentration disclosures found for {customer} in the database.")
    st.stop()

fiscal_year = st.selectbox("Fiscal year", years, index=0)  # years is newest-first — index 0 = latest

data = get_json("/dashboard/exposure", {"customer": customer, "fiscal_year": fiscal_year})
if data is None:
    st.stop()

edges = data.get("edges", [])
if not edges:
    st.info(f"No supplier disclosed {customer} concentration specifically for FY{fiscal_year}.")
    st.stop()

if len(edges) <= 3 and len(years) > 1:
    st.caption(
        f"Only {len(edges)} supplier(s) filed a named {customer} disclosure for FY{fiscal_year} — "
        f"suppliers report on different fiscal calendars, so the most recent year is sparser. "
        f"Try an earlier year (e.g. FY{years[-1]}) for a fuller cluster."
    )

with st.expander(f"📍 Supply-chain cluster — {customer} FY{fiscal_year} ({len(edges)} suppliers)", expanded=False):
    df = pd.DataFrame(sorted(edges, key=lambda e: e["supplier"]))  # fixed alpha order — layout is stable across years
    n = len(df)
    df["angle"] = [i / n * 2 * math.pi for i in range(n)]
    df["x"] = df["angle"].apply(math.cos)
    df["y"] = df["angle"].apply(math.sin)
    df["label"] = df.apply(
        lambda r: f"{r['supplier']}  >{r['revenue_pct']:.0f}%" if r["threshold_only"]
        else f"{r['supplier']}  {r['revenue_pct']:.0f}%",
        axis=1,
    )

    hub = pd.DataFrame([{"label": customer, "x": 0.0, "y": 0.0}])
    domain = list(SUPPLIER_COLORS.keys())
    color_range = list(SUPPLIER_COLORS.values())
    axis = alt.Axis(grid=False, ticks=False, labels=False, domain=False, title=None)
    scale = alt.Scale(domain=[-1.6, 1.6])

    edge_lines = (
        alt.Chart(pd.concat([
            df.assign(x0=0.0, y0=0.0)[["supplier", "x0", "y0", "revenue_pct", "threshold_only", "fiscal_year"]]
              .rename(columns={"x0": "x", "y0": "y"}).assign(order=0),
            df[["supplier", "x", "y", "revenue_pct", "threshold_only", "fiscal_year"]].assign(order=1),
        ]))
        .mark_line()
        .encode(
            x=alt.X("x:Q", axis=axis, scale=scale),
            y=alt.Y("y:Q", axis=axis, scale=scale),
            detail="supplier:N",
            order="order:Q",
            color=alt.Color("supplier:N", scale=alt.Scale(domain=domain, range=color_range), legend=None),
            strokeWidth=alt.StrokeWidth("revenue_pct:Q", scale=alt.Scale(range=[1, 8]), legend=None),
            tooltip=[
                alt.Tooltip("supplier:N", title="Supplier"),
                alt.Tooltip("revenue_pct:Q", title="Dependency %"),
                alt.Tooltip("fiscal_year:O", title="Fiscal year"),
            ],
        )
    )
    nodes = (
        alt.Chart(df)
        .mark_circle(opacity=0.9)
        .encode(
            x=alt.X("x:Q", axis=axis, scale=scale),
            y=alt.Y("y:Q", axis=axis, scale=scale),
            size=alt.Size("revenue_pct:Q", scale=alt.Scale(type="sqrt", range=[300, 4000]), legend=None),
            color=alt.Color("supplier:N", scale=alt.Scale(domain=domain, range=color_range), legend=None),
            tooltip=[
                alt.Tooltip("supplier:N", title="Supplier"),
                alt.Tooltip("revenue_pct:Q", title="Dependency %"),
                alt.Tooltip("threshold_only:N", title="Threshold only"),
                alt.Tooltip("fiscal_year:O", title="Fiscal year"),
            ],
        )
    )
    # Built from a fresh Chart(df), NOT nodes.mark_text(...) — reusing `nodes` would drag its
    # "size" encoding (a 300–4000 range meant for circle area) onto the text mark's font size,
    # rendering label glyphs hundreds of px tall and blanking out the whole chart.
    node_labels = (
        alt.Chart(df)
        .mark_text(dy=-22, fontWeight="bold")
        .encode(
            x=alt.X("x:Q", axis=axis, scale=scale),
            y=alt.Y("y:Q", axis=axis, scale=scale),
            text="label:N",
            color=alt.value("#0b0b0b"),
        )
    )
    hub_node = (
        alt.Chart(hub)
        .mark_circle(size=2600, color="#0b0b0b", opacity=0.9)
        .encode(x=alt.X("x:Q", axis=axis, scale=scale), y=alt.Y("y:Q", axis=axis, scale=scale))
    )
    hub_label = hub_node.mark_text(color="white", fontWeight="bold").encode(text="label:N")

    chart = (edge_lines + hub_node + hub_label + nodes + node_labels).properties(
        width=480, height=480,
    ).configure_view(strokeWidth=0)
    st.altair_chart(chart, use_container_width=True)
    st.caption('Node size and line thickness scale with dependency %. ">X%" = 10-K disclosed only a threshold.')

st.markdown("#### Source disclosures")
for edge in sorted(edges, key=lambda e: e["revenue_pct"] or 0, reverse=True):
    pct_str = f">{edge['revenue_pct']:.0f}% (threshold only)" if edge["threshold_only"] else f"{edge['revenue_pct']:.0f}%"
    with st.expander(f"{edge['supplier']} → {customer} · FY{edge['fiscal_year']} · {pct_str}"):
        st.markdown(f"_{edge['citation']}_")
        if edge["source_text"]:
            st.caption(f'"{edge["source_text"]}"')
