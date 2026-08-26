"""What-if Scenario Tool — dollar impact per supplier of a customer order cut."""

import altair as alt
import pandas as pd
import streamlit as st

from _dash_common import SUPPLIER_COLORS, get_json, warm_badge

st.title("🎯 What-if Scenario Tool")
warm_badge()
st.caption(
    "Dollar impact = supplier's total revenue × its disclosed dependency % × the order cut. "
    "Ranks suppliers by exposure, not by dependency % alone — a smaller, more-dependent supplier "
    "can lose fewer dollars than a larger, less-dependent one."
)

customer = st.selectbox("Customer", ["AAPL"], index=0)
cut_pct = st.slider("Order cut (%)", min_value=5, max_value=100, value=20, step=5)

data = get_json("/dashboard/whatif", {"customer": customer, "cut_pct": cut_pct})
if data is None:
    st.stop()

results = data.get("results", [])
if not results:
    st.info(f"No named customer-concentration disclosures found for {customer} in the database.")
    st.stop()

# Split before ranking, not after — a threshold_only supplier's true dollar loss could be
# far above what its 10%-floor estimate shows (e.g. SWKS discloses only ">10%"; WRDS puts
# its real Apple dependency at ~69%). Mixing it into the same ranked bar chart risks a
# reader concluding "low exposure" from a bar position that a corrected number could move
# to the top. Floor-estimate suppliers get their own section, never a rank position implying
# certainty relative to exact-disclosure suppliers.
exact_results = [r for r in results if not r["threshold_only"]]
floor_results = [r for r in results if r["threshold_only"]]

domain = list(SUPPLIER_COLORS.keys())
color_range = list(SUPPLIER_COLORS.values())


def _render_bar_chart(rows: list[dict], height_per_row: int = 32) -> None:
    chart_df = pd.DataFrame(rows)
    chart_df["loss_label"] = chart_df["dollar_loss"].apply(lambda v: f"${v / 1e6:,.0f}M")
    chart_df["pct_label"] = chart_df.apply(
        lambda r: f">{r['revenue_pct']:.0f}%" if r["threshold_only"] else f"{r['revenue_pct']:.0f}%",
        axis=1,
    )
    bars = (
        alt.Chart(chart_df)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("dollar_loss:Q", title=f"Estimated revenue loss from a {cut_pct}% {customer} order cut (USD)"),
            y=alt.Y("supplier:N", sort="-x", title=None),
            color=alt.Color("supplier:N", scale=alt.Scale(domain=domain, range=color_range), legend=None),
            tooltip=[
                alt.Tooltip("supplier:N", title="Supplier"),
                alt.Tooltip("loss_label:N", title="Est. loss"),
                alt.Tooltip("pct_label:N", title="Dependency %"),
                alt.Tooltip("fiscal_year:O", title="Fiscal year"),
            ],
        )
    )
    labels = bars.mark_text(align="left", dx=4).encode(text="loss_label:N", color=alt.value("#0b0b0b"))
    st.altair_chart(
        (bars + labels).properties(height=height_per_row * len(chart_df) + 20),
        use_container_width=True,
    )


st.markdown("#### Ranked by estimated exposure (exact disclosures)")
if exact_results:
    _render_bar_chart(exact_results)
else:
    st.info("No supplier in this set has an exact (non-threshold) Apple revenue percentage disclosed.")

if floor_results:
    st.markdown("#### ⚠️ Floor estimates — real exposure likely higher, not ranked against the above")
    st.caption(
        "These suppliers' 10-Ks disclose only \"more than ten percent\" — no exact figure. "
        "The bars below use 10% as a floor for the dollar-loss calculation, which likely "
        "**understates** the true number (e.g. WRDS third-party data puts SWKS's actual Apple "
        "dependency at ~69%, not 10%). Do not compare bar length against the exact-disclosure "
        "chart above — a corrected percentage could move a supplier here to the top of that ranking."
    )
    _render_bar_chart(floor_results)

st.markdown("#### Detail")
for r in results:
    pct_str = f">{r['revenue_pct']:.0f}% (threshold only — floor estimate)" if r["threshold_only"] else f"{r['revenue_pct']:.0f}%"
    with st.expander(f"{r['supplier']} · est. ${r['dollar_loss'] / 1e6:,.0f}M loss · FY{r['fiscal_year']} · {pct_str} dependency"):
        st.markdown(f"Supplier FY{r['fiscal_year']} revenue: ${r['supplier_revenue'] / 1e6:,.0f}M")
        st.markdown(f"_{r['citation']}_")
