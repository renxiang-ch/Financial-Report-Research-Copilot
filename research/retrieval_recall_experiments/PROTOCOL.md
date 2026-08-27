# Retrieval Recall Experiments

## Why this exists

The frozen eval set's retrieval metric ("Retrieval: 42.9%" in the README) was
shown, this session, to be dominated by a source of noise unrelated to
retrieval quality: the agent's tool-calling loop has no `temperature` pinned
(`agent.py::_ask_openai`, no `temperature=` argument → OpenAI default 1.0),
so the literal query string it hands to `retrieve_text` varies run to run for
the identical question. Traced directly from historical result files:
`ret_avgo_vmware_acquisition` was searched as `"history and VMware
acquisition"` in one run and `"Broadcom history and VMware acquisition"` in
another — same question, same code, different words, different rank, a
different hit/miss verdict.

That finding also settled the experimental design here: measuring "did a
retrieval change help" through the full agent loop confounds the change under
test with the agent's own query-phrasing noise. `eval/probe_retrieval.py`
already established the fix for that — call `bm25_retrieve`/`retrieve_dense`
directly with the eval item's fixed question text, no agent, no LLM call, no
sampling variance at all. This experiment extends that same pattern rather
than re-inventing it.

## Question this answers

With chunking held fixed (explicitly out of scope — re-chunking would
invalidate the frozen eval set's chunk-level golden citations, a cost this
experiment isn't authorized to spend), can any change to the *retrieval
layer itself* raise the rate at which a golden passage lands in the top-k a
question would actually receive?

## Constraints

1. **No re-chunking.** `text_chunks` rows, their boundaries, and their
   `embedding` column are read-only inputs here. Any idea that requires a
   different chunk boundary is out of scope for this experiment, noted and
   deferred, not attempted.
2. **No frozen-set edits.** `data/datasets/eval_set.json` is read-only.
   Findings here inform a future PR against the retrieval layer; they do not
   retroactively change what any past result file measured.
3. **Deterministic first.** Every arm is evaluated via direct calls to
   `bm25_retrieve` / `retrieve_dense` / a candidate replacement, using the
   eval item's own fixed question text as the query — zero LLM calls, zero
   run-to-run variance, $0 cost. This isolates the retrieval-layer change
   from the agent's query-generation noise this session already proved is
   real and large enough to flip individual verdicts.
4. **Small n, said plainly.** 7 non-retired retrieval items in the frozen
   set. A hit@5 count moves in steps of ~14 percentage points at this n;
   MRR is the more sensitive statistic and is reported alongside every
   hit@k table for that reason (same choice `probe_retrieval.py` already
   made, same reasoning).
5. **Agent-loop validation is a separate, later step**, not the main track:
   if a change wins deterministically, it gets validated once against a
   small number of real agent runs with `temperature` pinned in a
   *research-local* wrapper — production `agent.py` is not modified for
   this experiment. Any such validation run's estimated cost is reported
   before it executes.

## Baseline

Today's production config, measured the same deterministic way: `RRF_K=60`,
`pool=k*2`, BM25 `k1=1.5, b=0.75`, whitespace-lowercase tokenization, no
query expansion, no reranking. See `results/00_baseline.json`.

## Arms tried (log, updated as each is run)

n=7. hit@k moves in ~14pp steps at this n; MRR is the more sensitive
statistic and the one to weight more heavily. Full per-question ranks in
`results/run_20260827_104634.json` (latest run, includes arm 7).

| # | Change | Hypothesis | hit@5 | MRR | Verdict |
|---|---|---|---|---|---|
| 0 | Baseline (current production config) | — | 3/7 | 0.214 | reference |
| 1 | **Prepend company full name to query** | Session already proved query wording moves results a lot (Broadcom-name case) | **4/7** | **0.386** | **Real win — see below. Current best arm.** |
| 2 | Prepend ticker + full name (not just name) | Ticker might help BM25 term matching too | 3/7 | 0.333 | Worse than name alone — don't add the bare ticker |
| 3 | Name expansion + 4x pool | Does expansion still help with more candidates | 3/7 | 0.243 | Worse than name alone |
| 4 | 4x candidate pool alone | Golden passage might be just outside a 2x pool | 2/7 | 0.214 | **Worse than baseline** — pool depth dilutes RRF (see below) |
| 5 | RRF_K=10 (vs production's 60) | Standard IR constant might not suit a small homogeneous corpus | 3/7 | 0.214 | No effect at all — identical to baseline on every question |
| 6 | BM25 b=0.3 (vs production's 0.75) | Less length normalization might help long prose sections | 3/7 | 0.214 | No effect at all — identical to baseline on every question |
| 7 | Cross-encoder rerank over union pool (skip RRF) | Per-retriever breakdown shows RRF's sum formula, not weak retrieval, buries items strong in one retriever only | 3/7 | 0.357 | **Worse than arm 1 net** — fixes exactly the case it was designed for (1/3), doesn't touch the other 2 (different root cause) — see below |

### The win, item by item (arm 1 vs baseline)

- `ret_swks_apple_concentration_2024`: not found → **rank 2**
- `ret_swks_markets_served_2024`: rank 4 → **rank 1**
- `ret_glw_business_segments`: rank 4 → rank 5 (slight regression, still hit@5)
- `ret_aapl_applecare_description`: rank 1 → rank 1 (already perfect, unaffected)
- `ret_avgo_vmware_acquisition`, `ret_aapl_product_categories`, `ret_qrvo_customer_risk`: still not found — **unaffected**, confirms these three are a genuinely different problem, not query-wording noise (see diagnostic below)

Net: 2 items fixed, 1 item's rank moved by one position without crossing the
k=5 boundary, 3 unaffected, 1 already-perfect unaffected. Coherent with this
session's own diagnosis (Category A items were query-wording-sensitive;
Category B/C items were not) rather than an unexplained aggregate swing.

### Why deeper pool hurts (arms 3 and 4)

RRF's fused score for a given chunk depends only on that chunk's own rank
within each retriever's list, not on pool depth — so a chunk that ranks #2
at pool=10 keeps that same raw score at pool=50. What changes is *how many
other candidates now also have a nonzero score competing for the final
sorted position*. A 4x pool lets ~40 more chunks per retriever into the
fusion, some of which now outscore the golden chunk's fixed score by
sheer accumulation — pushing its final rank down even though nothing about
its own retrieval quality changed. Deeper is not free; it dilutes.

### Diagnostic: do the remaining misses exist in the corpus at all?

At depth 50 (arm 1's query expansion, pool widened only for this check):

| Item | Fused rank at depth 50 |
|---|---|
| `ret_avgo_vmware_acquisition` | 10 |
| `ret_aapl_product_categories` | 18 |
| `ret_qrvo_customer_risk` | 22 |

All three genuinely exist in the corpus — this is not a chunking gap. But
the *fused* rank was the wrong number to stop at, because RRF is a sum
across two retrievers, and a sum obscures whether both retrievers actually
agree it's deep. Broken out per-retriever, at the same depth:

| Item | Fused rank | BM25-only rank | Dense-only rank |
|---|---|---|---|
| `ret_avgo_vmware_acquisition` | 10 | 33 | **8** |
| `ret_aapl_product_categories` | 18 | >50 (not found) | **4** |
| `ret_qrvo_customer_risk` | 22 | **11** | >50 (not found) |

**None of the three is "ranked 6-10 by both retrievers."** In every case one
retriever already finds the golden passage shallow (rank 4, 8, or 11) and
the *other* retriever misses it completely (not even in the top 50). RRF's
score is `1/(k+rank_bm25) + 1/(k+rank_dense)`, so a chunk that's mediocre
but *present* in both lists can out-sum a chunk that's excellent in one list
and absent from the other — the fusion formula itself is what buries these
three, not weak retrieval. This also explains why deeper pooling (arms 3/4)
made things worse: a deeper pool adds more "mediocre-in-both" competitors,
which is exactly the kind of candidate RRF's sum rewards.

(`ret_swks_apple_concentration_2024` also showed fused rank 38 in this same
depth-50 diagnostic run — read that as the same pool-dilution effect, not
as "hard to find": at the pool depth arm 1 actually uses (10), it ranks #2.
The diagnostic's own deeper pool reproduces exactly the arm-3/4 dilution
effect on this item, which is a finding, not a contradiction.)

### Arm 7: cross-encoder rerank over a union pool

Given the per-retriever breakdown above, the natural next move is not "widen
the pool feeding RRF" (already proven counterproductive) but "stop using
RRF's sum for these candidates and score them with a model that actually
reads both texts." Implementation: union of each retriever's own top-20
(company-name-expanded query, since that query is already the best-proven
retrieval input) — not the RRF-fused list — rescored by
`cross-encoder/ms-marco-MiniLM-L-6-v2` (local, deterministic, zero LLM
calls, same cost bar as every other arm here) against the *raw* question,
top 5 by cross-encoder score kept.

| Arm | hit@5 | MRR |
|---|---|---|
| `query_expansion_company` (current best) | 4/7 | 0.386 |
| `rerank_cross_encoder` | 3/7 | 0.357 |

**Net: worse on this n=7 set than the existing best arm.** Item by item
(golden rank within the full candidate pool, not just top-5, so "how close"
is visible even for misses):

| Item | query_expansion_company | rerank_cross_encoder | Read |
|---|---|---|---|
| `ret_avgo_vmware_acquisition` | not found | **rank 2** | **Clean win** — confirms the hypothesis: dense already had it at rank 8, the cross-encoder correctly recognized it and floated it to #2 once RRF was no longer burying it |
| `ret_swks_apple_concentration_2024` | rank 2 | rank 7 in pool (just outside top-5) | Regression, but a near-miss — the cross-encoder mildly disagrees with RRF, not badly |
| `ret_glw_business_segments` | rank 5 | rank 6 in pool | Same — was already right at the boundary, tipped one position the wrong way |
| `ret_aapl_product_categories` | not found | **rank 14 in pool of 35** | Still not fixed, and the pool-depth theory doesn't explain why: dense had ranked this passage #4, but the cross-encoder itself actively disagrees, scoring it far down among 35 candidates it did see |
| `ret_qrvo_customer_risk` | not found | **rank 38 of 38 — dead last** | Same shape, more extreme: BM25 ranked this #11, the cross-encoder ranks it least relevant of every candidate in the pool |

Checked whether the query text fed to the cross-encoder was the problem —
scored the same two stubborn items with the expanded (company-name) query
instead of the raw question, matching what built the pool: `aapl_product_categories`
moved 14→13, `qrvo_customer_risk` moved 38→31. Barely moves either number,
which rules out "wrong query wording" as the explanation and points instead
to a genuine relevance-model disagreement — `ms-marco-MiniLM-L-6-v2` is
trained on general web/QA-style relevance, not finance-10-K-specific
phrasing, and for these two items it doesn't recognize what the retriever
that found them recognized.

**Conclusion on reranking**: the underlying diagnosis was right — RRF's sum
formula, not the retrievers, buries a signal that's strong in exactly one
retriever — and the fix works when that's the whole problem (`avgo`, +1
clean win). But two of the three misses have a second, independent problem
this reranker doesn't solve: the golden passage is only weakly recognized
as relevant even once it's sitting in the candidate pool being read
directly. Reranking is not a blanket fix for "item ranked outside top-5";
it fixes "item ranked outside top-5 *because of how RRF combines two
otherwise-good signals*," which was true for 1 of these 3, not all 3.
A finance-domain-tuned or larger cross-encoder is the next thing worth
trying if this direction continues, not a bigger pool or a different
generalist reranker size — but that's a new candidate to test, not
something this session has evidence for yet.

### A methodology gap in this harness, found after the fact

`hit@10` in the harness's own summary table is not a meaningful number:
every arm's fusion function truncates to `FINAL_K=5` before scoring, so no
result can ever rank 6-10 — `hit@10` is mathematically pinned to equal
`hit@5` by construction, not measuring anything past what `hit@5` already
shows. Noted here rather than silently left in the printed table; the
reranking-pass follow-up will need arms that return a wider list than they
score against for this reason.

## Agent-loop validation: does pinning `temperature` help?

Constraint 5 deferred this on purpose: every arm above bypasses the agent
entirely, calling `bm25_retrieve`/`retrieve_dense` with the eval item's own
fixed question text, so none of them can say anything about whether fixing
the *agent's* sampling (the actual source of the query-wording noise this
session traced `agent.py::_ask_openai` to — no `temperature=` argument,
OpenAI default 1.0) would help. `agent_temperature_validation.py` is that
step: a research-local monkeypatch of
`openai...Completions.create` that injects `temperature=0, seed=42` only
when the caller didn't already set them, so production `agent.py` is
untouched. 7 active retrieval items x 5 repeats x 2 arms (unpinned vs
pinned) = 70 real agent calls, $0.088 total, matching the 5-repeats
precedent already established for noisy-metric validation elsewhere in
this project (P3 multi-turn).

**"v3" cannot be literally re-run** — this teaching repo is a single-commit
snapshot, and current `agent.py` (slots, clarification, grounding
verification, routing) is far more evolved than v3's era. What runs here is
the current code, twice, with only the sampling params different — a clean
single-variable test, with v3's own number kept only as a loose reference
point, not a reproduction target.

| Arm | Mean hit rate | Range across 5 runs | Items that flipped at all | Cost |
|---|---|---|---|---|
| Unpinned (production default) | 34.3% | 28.6%–57.1% | 2/7 | $0.0442 |
| **Pinned (temp=0, seed=42)** | **28.6%** | **28.6%–28.6% (zero variance)** | **0/7** | $0.0437 |

**Pinning did exactly what it was supposed to do on the variance side —
and the opposite of what was hoped on the accuracy side.** It fully
eliminates the flip (0/7 unstable across 5 identical-config runs, vs 2/7
unpinned), confirming the noise mechanism is real and fixable. But it
converges on **the losing outcome**, not the winning one: 4 of the 5
unpinned runs also landed at 28.6% (only one lucky run hit 57.1%, from both
unstable items landing HIT together), and pinning locks in that more common
worse-case rather than the rarer better one.

Why: greedy (temperature=0) decoding picks the single most-likely
completion, not the best-scoring-at-retrieval one, and the most-likely
completion for these two questions happens to omit the company name from
the query text. Verified directly, not inferred — ran both stuck items
again under the same pinned config and read the actual `retrieve_text`
call:

```
ret_aapl_product_categories -> query: 'product and service categories revenue'   (ticker=AAPL passed separately)
ret_glw_business_segments   -> query: 'main business segments'                   (ticker=GLW passed separately)
```

Neither query string contains the company name — `ticker` is passed as a
structured filter (scopes which company's chunks are searched) but doesn't
help BM25/dense match the name as a *term*. This is exactly arm 1's
finding in reverse: the deterministic greedy completion happens to be the
query-without-company-name case that arm 1 already measured as the worse
one (MRR 0.214 vs 0.386 with the name prepended).

**Conclusion**: temperature pinning is not a fix for this on its own — it
removes randomness but has no preference for a *good* deterministic answer
over a bad one, and greedy decoding's single most-likely completion isn't
guaranteed (and here, isn't) the best-retrieving one. It strengthens rather
than replaces the case for arm 1 (forcing the company name into the query
mechanically, which doesn't depend on what the model happens to generate).
The two together — pin sampling *and* force the company name into the
query server-side rather than hoping the model includes it — would give
both zero variance and the better-recall outcome; pinning alone gives only
the first.

## Files

- `run_experiment.py` — the harness; each candidate method is a function
  registered as an "arm," scored identically via `golden_rank`/hit@k/MRR.
- `results/` — one JSON per run, never overwritten.
