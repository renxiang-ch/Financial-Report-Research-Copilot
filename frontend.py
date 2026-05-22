"""Minimal Streamlit frontend for the Financial Report Copilot."""

import httpx
import streamlit as st

API_URL = "http://localhost:8000"

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
    with st.spinner("Thinking..."):
        try:
            resp = httpx.post(f"{API_URL}/ask", json={"question": question}, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            st.error(f"Could not reach API: {e}")
            st.stop()

    # Answer
    st.markdown("### Answer")
    st.markdown(data["answer"])

    # Citations
    if data["citations"] and data["citations"][0] != "No citations — running in mock mode":
        st.markdown("### Sources")
        for cite in data["citations"]:
            st.markdown(f"- {cite}")

    # Reasoning steps
    if data["steps"]:
        with st.expander("Reasoning steps"):
            for step in data["steps"]:
                tool = step["tool"]
                inp = step["input"]
                if tool == "query_financials":
                    st.markdown(
                        f"🔍 **query_financials** — "
                        f"`{inp.get('ticker')}` · `{inp.get('metric')}` · FY{inp.get('fiscal_year', 'latest')}"
                    )
                elif tool == "compute":
                    st.markdown(f"🧮 **compute** — `{inp.get('expression')}`")
                else:
                    st.markdown(f"⚙️ **{tool}** — `{inp}`")

    # Mock mode warning
    if "mock mode" in data["answer"].lower():
        st.warning("Running in mock mode — add ANTHROPIC_API_KEY to .env to enable the agent.")
