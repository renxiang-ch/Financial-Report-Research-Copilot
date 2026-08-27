"""
Ceiling check: bypass retrieval entirely. Concatenate every text_chunk for
the item's own target (ticker, fiscal_year) filing into one blob, hand the
whole thing to the model with the question, no tools, no ranking, no top-k
cutoff -- see if the bottleneck really is retrieval ranking (as diagnosed
across this session's other experiments) or whether the model still gets
it wrong even with the entire relevant document in front of it.

This is not a deployable fix (production can't stuff a 30-100k token
document into every question's context), it's a diagnostic: if accuracy
jumps to near-100% here, that confirms retrieval ranking is the whole
story. If it doesn't, there's a second, comprehension-level problem this
session hasn't found yet.

Usage:
    uv run --active python research/retrieval_recall_experiments/full_document_ceiling.py
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def load_items() -> list[dict]:
    import agent_temperature_validation as m
    items = {it["id"]: it for it in m.load_retrieval_items()}
    ids = ["ret_swks_markets_served_2024", "ret_qrvo_customer_risk",
           "ret_aapl_product_categories", "ret_glw_business_segments"]
    return [items[i] for i in ids]


def full_document_text(ticker: str, fiscal_year: int) -> str:
    from copilot.storage.db import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT tc.text FROM text_chunks tc
                   JOIN filings f ON tc.accn = f.accn
                   WHERE tc.ticker=%s AND f.fiscal_year=%s
                   AND COALESCE(tc.chunk_type,'text') <> 'table'
                   ORDER BY tc.id""",
                (ticker, fiscal_year),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return "\n\n".join(r["text"] for r in rows)


def ask_with_full_document(question: str, doc_text: str, model: str = "gpt-4o-mini") -> dict:
    from openai import OpenAI
    from copilot.config import settings

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None, timeout=120)
    prompt = (
        "Answer the question using only the SEC 10-K filing text below. "
        "Quote or closely paraphrase the relevant sentence(s). If the filing "
        "does not address the question, say so explicitly rather than guessing.\n\n"
        f"QUESTION: {question}\n\n"
        f"--- FILING TEXT ---\n{doc_text}\n--- END FILING TEXT ---"
    )
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.time() - t0
    answer = resp.choices[0].message.content
    return {
        "answer": answer,
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
        "latency_s": round(elapsed, 2),
    }


def run() -> None:
    from copilot.eval.harness import _llm_judge, _check_key_phrase

    items = load_items()
    print(f"{len(items)} items, full-document ceiling check (no retrieval, no tools).\n")

    results = []
    total_in = total_out = 0
    for it in items:
        doc = full_document_text(it["ticker"], it["fiscal_year"])
        print(f"=== {it['id']}  ({len(doc)} chars, ~{len(doc)//4} tokens)")
        r = ask_with_full_document(it["question"], doc)
        total_in += r["input_tokens"]
        total_out += r["output_tokens"]

        phrase = it["golden_citations"][0]["key_phrase"]
        needle = " ".join(phrase.lower().split())[:50]
        literal_hit = needle in " ".join((r["answer"] or "").lower().split())

        judge = _llm_judge(it["question"], it.get("golden_answer", ""), r["answer"])

        print(f"  literal_key_phrase_hit={literal_hit}  judge={judge.get('score')}/3")
        print(f"  answer: {(r['answer'] or '')[:220]}")
        print()

        results.append({
            "id": it["id"], "literal_hit": literal_hit, "judge_score": judge.get("score"),
            "judge_reason": judge.get("reason"), "answer": r["answer"],
            "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
            "latency_s": r["latency_s"],
        })

    cost = round(total_in / 1_000_000 * 0.15 + total_out / 1_000_000 * 0.60, 5)
    n_literal = sum(1 for r in results if r["literal_hit"])
    n_judge_pass = sum(1 for r in results if (r["judge_score"] or 0) >= 2)
    print("=" * 70)
    print(f"literal key-phrase hit: {n_literal}/{len(results)}   judge>=2: {n_judge_pass}/{len(results)}   cost=${cost}")
    print("=" * 70)

    out = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "cost_usd": cost, "results": results}
    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"full_doc_ceiling_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    run()
