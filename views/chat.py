"""Chat — a multi-turn conversation with the agent (query_financials / compute /
retrieve_text / graph_query). The dashboard pages are faster and free for the
questions they cover.

The thread lives in this client, not on the server: each request carries the
prior turns and gets the updated thread back. That keeps the API stateless, which
matters on a free tier whose instance spins down after 15 idle minutes -- a
server-side session store would lose conversations on every cold start.
"""

import queue as _queue
import threading
import time

import httpx
import streamlit as st

from _dash_common import API_KEY, API_URL, _cfg, _server_warm_state


# ── Background workers ────────────────────────────────────────────────────────

def _warm_api(q: _queue.Queue, state: dict) -> None:
    """Ping /health to wake the Render instance; result goes into queue + shared state."""
    try:
        r = httpx.get(f"{API_URL}/health", timeout=90)
        ok = r.status_code == 200
        if ok:
            state["warm"] = True
        q.put(ok)
    except Exception:
        q.put(False)


def _fetch(q: _queue.Queue, qtext: str, model: str, history: list) -> None:
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        resp = httpx.post(
            f"{API_URL}/ask",
            json={"question": qtext, "model": model, "history": history},
            headers=headers, timeout=180,
        )
        resp.raise_for_status()
        q.put(("ok", resp.json()))
    except Exception as e:
        q.put(("err", str(e)))


# ── Session state defaults ────────────────────────────────────────────────────

_DEFAULTS = {
    "_warm_started":  False,
    "_warm_q":        None,
    "_api_warm":      False,   # True = confirmed healthy
    "_warm_failed":   False,   # True = health check returned an error
    "_warm_at":       None,
    "_running":       False,
    "_ask_q":         None,
    "_ask_at":        None,
    "_pending_q":     None,    # question awaiting an answer, shown while it runs
    "_used_model":    "gpt-4o-mini",
    "_turns":         [],      # [{"question", "data"}] for rendering
    "_history":       [],      # [{"question", "answer"}] sent to the API
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Warm-up ───────────────────────────────────────────────────────────────────

_svr = _server_warm_state()
if _svr["warm"] and not st.session_state._api_warm:
    st.session_state._api_warm = True

if not st.session_state._api_warm and not st.session_state._warm_started:
    q: _queue.Queue = _queue.Queue()
    st.session_state._warm_q       = q
    st.session_state._warm_started = True
    st.session_state._warm_at      = time.time()
    threading.Thread(target=_warm_api, args=(q, _svr), daemon=True).start()

if st.session_state._warm_started and not st.session_state._api_warm and not st.session_state._warm_failed:
    wq = st.session_state._warm_q
    if wq is not None and not wq.empty():
        if wq.get_nowait():
            st.session_state._api_warm = True
        else:
            st.session_state._warm_failed = True


# ── Header ────────────────────────────────────────────────────────────────────

st.title("💬 Chat")
st.caption(
    "Numbers verified from XBRL/SEC filings. Supply-chain edges from 10-K disclosures. "
    "Follow-up questions can refer back to earlier turns."
)

if st.session_state._api_warm:
    st.success("API ready", icon="🟢")
elif st.session_state._warm_failed:
    st.error("API unreachable — check that the backend is running on port 8000", icon="🔴")
    if st.button("Retry connection"):
        for k in ("_warm_started", "_warm_q", "_api_warm", "_warm_failed", "_warm_at"):
            st.session_state[k] = _DEFAULTS[k]
        st.rerun()
else:
    elapsed_warm = int(time.time() - (st.session_state._warm_at or time.time()))
    st.warning(f"API warming up… {elapsed_warm}s elapsed (usually ~60s on first visit)", icon="🟡")


# ── Controls ──────────────────────────────────────────────────────────────────

# Whatever the configured OpenAI-compatible endpoint serves. Override with
# AVAILABLE_MODELS (comma-separated) rather than editing this file -- pointing the
# backend at an aggregator changes which models exist, and that is deployment
# configuration, not code.
_MODELS = {m: m for m in
           (x.strip() for x in _cfg("AVAILABLE_MODELS", "gpt-4o-mini,gpt-4o").split(","))
           if m}
cc1, cc2 = st.columns([3, 1])
_model_label = cc1.selectbox("Model", list(_MODELS.keys()), index=0,
                             disabled=st.session_state._running)
_selected_model = _MODELS[_model_label]
if cc2.button("New conversation", use_container_width=True,
              disabled=st.session_state._running or not st.session_state._turns):
    st.session_state._turns = []
    st.session_state._history = []
    st.rerun()


def _submit(text: str) -> None:
    """Start a request in a background thread so Streamlit keeps rendering."""
    q2: _queue.Queue = _queue.Queue()
    st.session_state._ask_q      = q2
    st.session_state._running    = True
    st.session_state._ask_at     = time.time()
    st.session_state._pending_q  = text
    st.session_state._used_model = _selected_model
    threading.Thread(
        target=_fetch,
        args=(q2, text, _selected_model, list(st.session_state._history)),
        daemon=True,
    ).start()
    st.rerun()


# Examples only make sense as an opener; once a thread exists they would derail it.
if not st.session_state._turns and not st.session_state._running:
    st.markdown("**Try one of these, then ask a follow-up:**")
    examples = [
        "Which companies supply Apple in FY2024, and at what revenue concentration?",
        "If Apple cuts orders by 20%, which supplier loses the most in dollars?",
        "Show CRUS dependency on Apple across all available years.",
        "What was Qorvo's gross margin in FY2024?",
    ]
    ec1, ec2 = st.columns(2)
    for i, ex in enumerate(examples):
        if (ec1 if i % 2 == 0 else ec2).button(ex, use_container_width=True):
            _submit(ex)


# ── Rendering one answer ──────────────────────────────────────────────────────

_INHERITED_LABELS = {"fiscal_year": "fiscal year", "metric": "metric",
                     "companies": "company"}


def _render_clarification(data: dict, key: int, live: bool) -> bool:
    """The question put back to the reader. True if that is all there is to show.

    Buttons only on the LAST turn. An earlier clarification has already been
    answered, and leaving it clickable invites re-answering a question the thread
    has moved past -- which would silently branch the conversation.
    """
    clar = data.get("clarification") or {}
    if not (data.get("needs_clarification") and clar):
        return False

    st.warning(clar["reason"], icon="❓")
    st.markdown(f"**{clar['question']}**")

    if not live:
        for opt in clar["options"]:
            st.markdown(f"- {opt['label']}")
        return True

    cols = st.columns(len(clar["options"]))
    for j, opt in enumerate(clar["options"]):
        # The label is the short form; the rewrite is the sentence that will be
        # asked, shown on hover so choosing is not a guess about what happens.
        if cols[j].button(opt["label"], key=f"clar{key}_{j}",
                          help=opt["rewrite"], use_container_width=True):
            _submit(opt["rewrite"])

    if clar.get("allow_other"):
        # The escape hatch, and the reason a menu is safe to offer at all: two
        # readings the system thought of do not exhaust what the reader meant,
        # and without this the menu would be a smaller cage than the question.
        with st.form(key=f"clarother{key}", clear_on_submit=True):
            typed = st.text_input("None of these — put it in your own words",
                                  placeholder="e.g. Apple's operating margin in FY2024")
            if st.form_submit_button("Ask this instead") and typed.strip():
                _submit(typed.strip())
    return True


def _render_answer(data: dict, key: int = 0, live: bool = False) -> None:
    # What the follow-up did not say and this answer assumed anyway. Above the
    # answer, not in the footer with the token counts: it is a constraint the
    # system chose on the reader's behalf, and a reader who disagrees needs to
    # see it before reading the number, not after.
    # Nothing below applies to a handed-back question: no answer, no figures, no
    # provenance. Rendering "0 figures traced to a tool result" under it would be
    # true and useless.
    if _render_clarification(data, key, live):
        return

    ctx0 = data.get("context") or {}
    inherited = ctx0.get("inherited") or []
    resolved = ctx0.get("resolved_question")
    if inherited or resolved:
        bits = []
        if resolved:
            # The strongest of the three: a pronoun was replaced with a company
            # before anything was decided. Quote the sentence that was actually
            # answered rather than naming the field.
            bits.append(f"Read as: *{resolved}*")
        if inherited:
            named = ", ".join(_INHERITED_LABELS.get(f, f) for f in inherited
                              if f != "companies" or not resolved)
            if named:
                bits.append(f"Carried over from earlier in this thread: **{named}**")
        st.info(" · ".join(bits) + ". Say the year or company outright to change it.",
                icon="↩️")
    st.markdown(data.get("answer", ""))

    prov = data.get("provenance") or {}
    if prov:
        with st.container(border=True):
            st.markdown("**How this answer is grounded**")
            pc1, pc2 = st.columns(2)
            with pc1:
                st.caption("Numbers from")
                st.markdown(prov.get("numeric_source", "—"))
                st.caption("Computation")
                st.markdown(prov.get("computation", "—"))
            with pc2:
                st.caption("Relationships from")
                st.markdown(prov.get("relationship_source") or "— (no supply-chain edge used)")
                st.caption("Corroboration")
                st.markdown(prov.get("corroboration", "—"))
            for lim in prov.get("limitations", []):
                st.warning(lim, icon="⚠️")

    # Two different kinds of statement, so two different colours. Amber above is
    # "this data source has a known limitation" -- true of the whole category,
    # written in advance. Red below is "this specific figure was checked against
    # the tool trace just now, and it is not there."
    ver = data.get("verification") or {}
    if ver.get("unverified_numbers"):
        st.error(
            "Unverified figure(s): "
            + ", ".join(f"{n:,.4g}" for n in ver["unverified_numbers"])
            + " — stated in the answer but not returned by any tool. "
              "Treat as unsourced.",
            icon="🚩",
        )
    if ver.get("unsourced_inputs"):
        st.error(
            "Computation ran on unsourced input(s): "
            + ", ".join(ver["unsourced_inputs"])
            + " — these values did not come from a data tool, so the result "
              "inherits their uncertainty.",
            icon="🚩",
        )
    if ver.get("unverified_citations"):
        st.error(
            "Citation(s) not returned by any tool: "
            + ", ".join(ver["unverified_citations"]),
            icon="🚩",
        )
    if ver.get("misbound_inputs"):
        # The most dangerous shape this system produces: every operand is real,
        # the arithmetic is right, and the result is about the wrong company or
        # the wrong year. Nothing else on this page would have caught it, which
        # is exactly why it gets its own red flag rather than a footnote.
        for problem in ver["misbound_inputs"]:
            st.error(
                "Figures bound to the wrong subject — " + problem
                + " The arithmetic is correct; the inputs are not the ones the "
                  "question is about.",
                icon="🚩",
            )
    if ver.get("unsourced_formulas"):
        # Amber, not red. Nothing here is known to be wrong -- the arithmetic is
        # fine and every input was fetched. What is missing is a source for the
        # FORMULA, which is a judgement this system cannot make and the reader
        # can: seeing `(current assets - inventory) / revenue * 365` beside
        # "days sales outstanding" settles it in a glance.
        st.warning(
            "Derived with a formula this system has no source for -- check it "
            "before relying on the figure:\n\n"
            + "\n\n".join(f"- {f}" for f in ver["unsourced_formulas"]),
            icon="🧮",
        )
    # `verified` already accounts for every failure above, so this stands alone
    # rather than chaining off the last check -- a green tick that depended on
    # which branch ran last would be a lie waiting to happen.
    if ver.get("verified") and ver.get("numbers_checked"):
        note = f"✅ {ver['numbers_checked']} figure(s) traced back to a tool result."
        if ver.get("passage_values"):
            # Say which regime this pass was earned under. Matching against
            # hundreds of numbers scraped out of retrieved prose is a much weaker
            # claim than matching against a handful of SQL lookups, and a tick
            # that hid the difference would be the wrong kind of reassuring.
            note += (f" {ver['passage_values']} candidate value(s) came from retrieved "
                     f"passages, so this is a permissive check.")
        st.caption(note)

    cites = [c for c in data.get("citations", []) if "mock mode" not in c]
    if cites:
        with st.expander(f"Sources ({len(cites)})"):
            for cite in cites:
                st.markdown(f"- {cite}")

    graph_edges = [
        edge
        for step in data.get("steps", [])
        if step.get("tool") == "graph_query"
        for edge in (step.get("output") or {}).get("edges", [])
    ]
    if graph_edges:
        with st.expander(f"Graph citations ({len(graph_edges)} edges from 10-K disclosures)"):
            for edge in graph_edges:
                pct_str = edge.get("pct_display") or (
                    ">10% (exact % not disclosed in 10-K)" if edge.get("threshold_only")
                    else f"{edge.get('revenue_pct')}%"
                )
                st.markdown(
                    f"**{edge.get('supplier', '')} → {edge.get('customer', '')}** · "
                    f"FY{edge.get('fiscal_year', '')} · {pct_str}"
                )
                st.markdown(f"_{edge.get('citation', '')}_")
                if edge.get("source_text"):
                    st.caption("“" + edge["source_text"] + "”")
                st.divider()

    if data.get("steps"):
        with st.expander("Reasoning steps"):
            for step in data["steps"]:
                tool = step.get("tool")
                inp  = step.get("input") or {}
                out  = step.get("output") or {}
                if tool == "query_financials":
                    st.markdown(
                        f"🔍 **query_financials** — `{inp.get('ticker')}` · "
                        f"`{inp.get('metric')}` · FY{inp.get('fiscal_year', 'latest')}"
                    )
                elif tool == "graph_query":
                    st.markdown(
                        f"🕸️ **graph_query** — supplier=`{inp.get('supplier') or '*'}` "
                        f"customer=`{inp.get('customer') or '*'}` fy=`{inp.get('fiscal_year', 'latest')}`"
                    )
                elif tool == "compute":
                    st.markdown(f"🧮 **compute** — `{inp.get('expression')}`")
                elif tool == "retrieve_text":
                    st.markdown(
                        f"📄 **retrieve_text** — `{inp.get('query')}` "
                        f"(ticker: `{inp.get('ticker', 'all')}`)"
                    )
                    for i, r in enumerate(out.get("results", []), 1):
                        st.markdown(
                            f"**Chunk {i}** · `{r.get('ticker')}` · {r.get('section')} · "
                            f"score: `{r.get('score', 0):.3f}`"
                        )
                        st.text((r.get("text") or "")[:500])
                        st.divider()
                else:
                    st.markdown(f"⚙️ **{tool}** — `{inp}`")

    usage = data.get("usage") or {}
    ctx   = data.get("context") or {}
    if usage:
        bits = [f"in {usage.get('input_tokens', 0):,} / out {usage.get('output_tokens', 0):,} tokens"]
        if usage.get("cached_input_tokens"):
            bits.append(f"{usage['cache_hit_rate']:.0%} of input served from cache")
        if ctx.get("turns_kept"):
            bits.append(f"{ctx['turns_kept']} prior turn(s) in context")
        if ctx.get("turns_evicted"):
            bits.append(f"{ctx['turns_evicted']} evicted — cache prefix reset")
        st.caption(" · ".join(bits))


# ── Thread ────────────────────────────────────────────────────────────────────

for _i, turn in enumerate(st.session_state._turns):
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        _render_answer(turn["data"], key=_i,
                       live=(_i == len(st.session_state._turns) - 1
                             and not st.session_state._running))

if st.session_state._running:
    with st.chat_message("user"):
        st.markdown(st.session_state._pending_q or "")
    with st.chat_message("assistant"):
        q2 = st.session_state._ask_q
        if q2 is not None and not q2.empty():
            status, payload = q2.get_nowait()
            st.session_state._running  = False
            st.session_state._ask_q    = None
            st.session_state._api_warm = True   # a response means the server is warm
            if status == "err":
                st.error(f"Could not reach API: {payload}")
                st.session_state._pending_q = None
            else:
                st.session_state._turns.append({
                    "question": st.session_state._pending_q,
                    "data": payload,
                })
                # The server returns the thread with this turn already appended,
                # so the client never has to reconstruct it.
                st.session_state._history = payload.get("history") or st.session_state._history
                st.session_state._pending_q = None
            st.rerun()
        else:
            elapsed = int(time.time() - (st.session_state._ask_at or time.time()))
            if elapsed < 5:
                msg = f"Sending request… ({elapsed}s)"
            elif not st.session_state._api_warm and elapsed < 70:
                msg = f"Server is waking up, then processing… ({elapsed}s, usually ~60s)"
            else:
                msg = f"Processing… ({elapsed}s)"
            st.info(msg)
            if st.button("Stop", use_container_width=True):
                st.session_state._running   = False
                st.session_state._ask_q     = None
                st.session_state._pending_q = None
                st.rerun()

prompt = st.chat_input(
    "Ask a question, or a follow-up such as “and Broadcom?”",
    disabled=st.session_state._running,
)
if prompt:
    _submit(prompt)


# ── Keep the page ticking while something is pending ─────────────────────────

if st.session_state._running or (
    st.session_state._warm_started
    and not st.session_state._api_warm
    and not st.session_state._warm_failed
):
    time.sleep(1)
    st.rerun()
