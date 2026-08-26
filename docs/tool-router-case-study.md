# Case Study: From "Hope the LLM Picks the Right Tool" to a Deterministic Router

**TL;DR** — I found a real tool-selection failure in production eval logs, built a regex-based router that forces the correct tool for two narrow, high-confidence question categories, and re-measured. The honest result: on a fresh 12-question probe set, the router did **not** raise tool-selection accuracy (both router-on and router-off scored 100%) — the LLM already picked correctly on this particular sample. What the router *does* deliver, measured: a **15.3% token-cost reduction** on that same set (100% of it from one category short-circuiting to zero LLM calls), a documented fix for a real failure mode seen in the frozen 33-question benchmark, and — the actual point — a deterministic guarantee that doesn't erode if the underlying model changes. Zero regression on Tier-1/2/3 numeric and refusal accuracy across 41 questions.

---

## 1. The failure mode

The agent has two tools that can both, in principle, answer a "how dependent is supplier X on customer Y" question:

- `graph_query` — reads the `supply_edges` table (structured, per-edge SEC citation, exact percentage when disclosed)
- `retrieve_text` — hybrid BM25 + dense retrieval over unstructured 10-K text (approximate, no guaranteed numeric grounding)

For dependency/concentration questions, `graph_query` is strictly better: it returns a sourced number instead of a paraphrase. The agent's tool choice for these questions is left to the LLM's judgment on each call — there's no code path that *guarantees* it picks structurally.

This showed up as a real, observed failure in the frozen 33-question eval set. `ret_swks_apple_concentration_2024` is scored by the harness as a `retrieve_text` passage-hit check. Across v2/v3 eval runs, the agent's tool choice for this question fluctuated between the two tools call-to-call — a genuine tool-selection non-determinism, not just retrieval noise. When it picked `graph_query` (arguably the *better* answer — a sourced number beats a paraphrase), the harness scored it as a miss anyway, because the harness only knows how to credit `retrieve_text` calls. That's a second-order symptom of the same root problem: without an explicit routing contract, neither the agent's behavior nor the eval's scoring logic has a stable notion of "which tool is this question's tool."

A second, cleaner failure mode: questions asking for a customer's procurement share from a named supplier (*"What percentage of Apple's total procurement budget comes from Qorvo?"*) are **unanswerable** — 10-K concentration disclosures are supplier-reported (what % of *their* revenue came from a customer), never customer-reported (what % of the *customer's* spend went to a supplier). The agent has no tool that can answer this, and previously had to reason its way to a refusal on every call — burning a full LLM round-trip, tool schemas included, just to arrive at "I can't answer this."

## 2. The fix

`route_question()` in [agent.py](../src/copilot/agent/agent.py) — a pure, dependency-free classifier that runs before any LLM call:

```python
def route_question(question: str) -> dict:
    if not ROUTER_ENABLED:
        return {"category": "default", "action": "auto"}
    if any(p.search(question) for p in _PROCUREMENT_SHARE_PATTERNS):
        return {"category": "procurement_share", "action": "refuse"}
    if any(p.search(question) for p in _DEPENDENCY_PATTERNS):
        return {"category": "dependency", "action": "force_tool", "tool": "graph_query"}
    return {"category": "default", "action": "auto"}
```

Three outcomes:
- **`dependency`** — matches "how dependent/reliant is X on Y", "revenue concentration with Y", "% of X's revenue comes from Y" against a known-company name list. Forces `tool_choice` to `graph_query` on the first round (OpenAI `{"type": "function", "function": {"name": "graph_query"}}`, Anthropic `{"type": "tool", "name": "graph_query"}`).
- **`procurement_share`** — matches customer-side spend/procurement/sourcing phrasing. Short-circuits to a canned refusal **before any API call is made** — zero tokens, zero latency, zero cost.
- **everything else** — unchanged `"auto"` behavior; the router only intervenes where the classification confidence is high enough to hardcode.

Deliberately narrow: two regex-matched categories, not a general-purpose classifier. A negative control was built into the eval set specifically to catch over-matching — `rt_qual_*` questions that contain the word "concentration" but ask for *qualitative* risk-factor language, not a dependency percentage, to confirm the dependency pattern doesn't fire on them.

## 3. Quantifying it

Built `data/eval_set_router.json` (12 questions, 4 per category, covering QRVO/CRUS/SWKS/AVGO) and `src/copilot/eval/harness_router.py` — a **deterministic** scorer (checks actual tool names in the agent's `steps` trace + keyword refusal detection), not an LLM judge. This sidesteps the self-evaluation bias that affects `harness.py`'s retrieval scoring (gpt-4o-mini judging gpt-4o-mini's own output, [documented as an unfixed limitation](../README.md#known-limitations)) — a tool-selection check doesn't need a judge model at all.

Ran both arms via the harness's `ROUTER_ENABLED` ablation flag:

| | Router OFF (`--no-router`) | Router ON | Delta |
|---|---|---|---|
| Tool-selection accuracy (12 q) | **100.0%** | **100.0%** | 0pp |
| `dependency` category | 100.0% | 100.0% | 0pp |
| `qualitative` category | 100.0% | 100.0% | 0pp |
| `procurement_share` category | 100.0% | 100.0% | 0pp |
| Input tokens | 53,247 | 43,786 | **−17.8%** |
| Estimated cost | $0.00946 | $0.00801 | **−15.3%** |
| Procurement-question latency (each) | 0.83–1.45s | **0.00s** | −100% |
| Procurement-question tool calls | 0 (but still 1 LLM round-trip) | 0 (no LLM call at all) | — |

**The honest headline: tool-selection accuracy did not move.** On this 12-question probe, gpt-4o-mini already picked the right tool 100% of the time in unconstrained `"auto"` mode — the router didn't get to "catch" a failure here, because there wasn't one in this sample. The historically-observed displacement (`ret_swks_apple_concentration_2024`) lives in the larger, frozen 33-question set and is a call-to-call non-determinism, not something a single-run 12-question probe would reliably reproduce either direction.

What *did* move, and is real: **cost**. Every `procurement_share` question dropped from a full LLM round-trip (system prompt + 5 tool schemas + question, ~1s, non-zero tokens) to a zero-cost, zero-latency regex match. That's 4/12 questions in this set going from "cheap but non-zero" to "literally free" — a 15.3% blended cost reduction driven entirely by one category, with zero accuracy cost since those 4 questions were unanswerable either way.

*(One latency figure to discount: the router-on run's average latency reads higher (7.52s vs 4.89s) than router-off — this is an artifact of a one-time embedding-model cold-load ("Loading weights") landing on question 5 in one run (55.7s) vs question 5 in the other run already having a warm model (13.0s), not a router effect. Per-question latency outside that single outlier and the now-zero procurement questions is unchanged between arms.)*

## 4. What the router is actually buying, if not accuracy-on-this-sample

A stochastic "auto" tool choice that happens to be correct today is not the same guarantee as a routing rule that is correct by construction. Three things a regex-forced route gives you that a lucky LLM call doesn't:

1. **It doesn't erode under model swap.** `ROUTER_ENABLED` and the routing table are model-agnostic — the same guarantee holds whether the underlying model is gpt-4o-mini, gpt-4o, or a Claude model behind `_ask_anthropic`. An "auto" behavior that happens to be reliable for one model has no such portability.
2. **It removes a documented harness/behavior mismatch.** The `ret_swks_apple_concentration_2024` scoring gap (agent correctly reaches for `graph_query`, harness only credits `retrieve_text`) is a known, filed limitation ([README](../README.md#known-limitations)). Forcing the route makes the agent's behavior for that question class predictable, which is a prerequisite for eventually fixing the harness's scoring logic to credit the right tool — you can't score a moving target.
3. **It converts a probabilistic refusal into a free one.** The `procurement_share` short-circuit isn't a marginal optimization — it's a category of question that is *provably* unanswerable from any tool in the system, discovered by looking at what data actually exists in `financial_facts`/`supply_edges` rather than asking the LLM to reason it out fresh every time. Zero tokens spent reasoning your way to a conclusion that was true before the question was asked.

## 5. Regression verification

Full regression, router enabled, run after this session's other changes (extraction gate hardening, six historical data fixes, an `is_primary`-protection fix, and a Huawei entity-alias consolidation — all in the same session, documented in `CLAUDE.md`):

| Metric | Result | vs. pre-router baseline |
|---|---|---|
| Tier-1 accuracy (10 q) | 100.0% | 0pp |
| Tier-2 accuracy (10 q) | 100.0% | 0pp |
| Tier-2 input fetch | 100.0% | 0pp |
| Refusal accuracy (5 q) | 100.0% | 0pp |
| Tier-3 overall (8 q, all 5 graph sub-types) | 100.0% | 0pp |
| Retrieval passage hit (8 q) | 25.0% (2/8) | −12.5pp, within documented run-to-run noise band (25–62.5% observed historically) |

Numeric and refusal accuracy — the metrics that actually matter for the cardinal "numbers come from SQL, never the LLM" rule — show **zero regression** across all 41 frozen-set questions. The retrieval dip is a single question (`ret_glw_business_segments`) flipping from hit to miss between runs, the same stochastic-chunk-ranking behavior already documented across the v2→v3 transition in `CLAUDE.md` — not attributable to anything changed in this session.

## 6. What I'd do differently

The 12-question router eval set was built to validate classification *precision* (does the regex fire on the right questions, and — critically — not on the negative-control qualitative questions that share vocabulary with the dependency category). It was not built to reproduce the specific stochastic failure that motivated the router in the first place, because that failure is a property of *repeated calls to the same question*, not a property capturable in a single-pass 12-question run. A more honest before/after design would run the same question N times per arm and measure the *variance* in tool choice, not just a single-shot accuracy number — that's the actual metric a deterministic router should be judged against. Flagged as a gap in this case study rather than glossed over.
