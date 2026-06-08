"""Minimal Streamlit frontend for the Financial Report Copilot."""

import os

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", st.secrets.get("API_URL", "http://localhost:8000"))
API_KEY = os.environ.get("API_KEY", st.secrets.get("API_KEY", ""))

st.set_page_config(page_title="Financial Report Copilot", page_icon="📊", layout="centered")

st.title("📊 Financial Report Copilot")
st.caption("Ask questions about SEC filings. Numbers are verified from XBRL data — never fabricated.")

# Example questions
st.markdown("**Example questions:**")
examples = [
    "What was Apple's revenue in FY2024?",
    "What is Skyworks' latest gross profit?",
    "What was Cirrus Logic's net income in FY2023?",
]
cols = st.columns(3)
for i, ex in enumerate(examples):
    if cols[i].button(ex, use_container_width=True):
        st.session_state["question"] = ex

# Input
question = st.text_input(
    "Your question",
    value=st.session_state.get("question", ""),
    placeholder="e.g. What was Apple's revenue in FY2024?",
)

if st.button("Ask", type="primary", use_container_width=True) and question:
    with st.spinner("Waking up server (first request may take ~60s)..."):
        try:
            httpx.get(f"{API_URL}/health", timeout=60)
        except Exception:
            pass  # best-effort warm-up
    with st.spinner("Thinking..."):
        try:
            headers = {"X-API-Key": API_KEY} if API_KEY else {}
            resp = httpx.post(f"{API_URL}/ask", json={"question": question}, headers=headers, timeout=180)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            st.error(f"Could not reach API: {e}")
            st.stop()

    # Answer
    st.markdown("### Answer")
    st.markdown(data["answer"])

    # Numeric citations (query_financials)
    if data["citations"] and data["citations"][0] != "No citations — running in mock mode":
        st.markdown("### Sources")
        for cite in data["citations"]:
            st.markdown(f"- {cite}")

    # Graph citations — collapsible, with source_text per edge
    graph_edges = []
    for step in data.get("steps", []):
        if step["tool"] == "graph_query":
            for edge in step.get("output", {}).get("edges", []):
                graph_edges.append(edge)

    if graph_edges:
        with st.expander(f"Graph citations ({len(graph_edges)} edges)"):
            for edge in graph_edges:
                supplier = edge.get("supplier", "")
                customer = edge.get("customer", "")
                fy       = edge.get("fiscal_year", "")
                pct      = edge.get("revenue_pct")
                threshold = edge.get("threshold_only", False)
                accn     = edge.get("citation", "")
                src      = edge.get("source_text", "")

                pct_str = ">10% (threshold)" if threshold else (f"{pct}%" if pct else "n/a")
                st.markdown(f"**{supplier} → {customer}** · FY{fy} · {pct_str}")
                st.markdown(f"_{accn}_")
                if src:
                    st.caption(f'"{src}"')
                st.divider()

    # Reasoning steps
    if data["steps"]:
        with st.expander("Reasoning steps"):
            for step in data["steps"]:
                tool = step["tool"]
                inp = step["input"]
                out = step.get("output", {})

                if tool == "query_financials":
                    st.markdown(
                        f"🔍 **query_financials** — "
                        f"`{inp.get('ticker')}` · `{inp.get('metric')}` · FY{inp.get('fiscal_year', 'latest')}"
                    )
                elif tool == "compute":
                    st.markdown(f"🧮 **compute** — `{inp.get('expression')}`")
                elif tool == "retrieve_text":
                    st.markdown(f"📄 **retrieve_text** — `{inp.get('query')}` (ticker: `{inp.get('ticker', 'all')}`)")
                    results = out.get("results", [])
                    if results:
                        for i, r in enumerate(results, 1):
                            st.markdown(f"**Chunk {i}** · `{r.get('ticker')}` · {r.get('section')} · score: `{r.get('score', 0):.3f}`")
                            st.text(r.get("text", "")[:500])
                            st.divider()
                else:
                    st.markdown(f"⚙️ **{tool}** — `{inp}`")

    # Mock mode warning
    if "mock mode" in data["answer"].lower():
        st.warning("Running in mock mode — add ANTHROPIC_API_KEY to .env to enable the agent.")
