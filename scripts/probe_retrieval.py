"""Does the vector index discriminate? Re-runnable measurement behind IMPROVEMENTS.md §8.

§8 read "scores compress into 0.72-0.81" as "retrieval doesn't discriminate". Absolute
cosine is the wrong statistic: it depends on how the QUERY embeds, so it is not
comparable across queries. What discrimination actually means is separation from the
corpus baseline for the same query — which is what this measures (top1 vs. p50/p99).

Run it before changing anything about retrieval, and again after:

    uv run python scripts/probe_retrieval.py

Free — pure local retrieval, no LLM, no network. Takes ~30s on 18k chunks.
"""
from __future__ import annotations

import numpy as np

from brain import vector_store as v
from brain.embed import FastEmbedder

# Includes the four questions IMPROVEMENTS.md §3 lists as unresolved in the spec.
QUERIES = [
    "what do they call the down move before the up move that breaks market structure",
    "what is a fair value gap and how do you identify one",
    "does the middle candle of a fair value gap need displacement",
    "what is displacement and how do you define it",
    "how do you determine the boundaries of the dealing range",
    "what is the optimal trade entry OTE and where is the sweet spot",
    "what is a judas swing",
    "premium and discount of a range, where do you buy and sell",
    "15 minute entry trigger failed breakdown and reclaim",
    "what is an order block and why does price return to it",
    "what is SMT divergence",
    "what is a liquidity sweep or stop hunt",
]

# Vocabulary that marks genuine instruction vs. stream chatter. Crude but the point is
# a relative signal across queries, not a precise classifier.
TEACH = ("if you", "you need", "that is your", "we assume", "look for", "this is a",
         "means", "when price", "so the", "right?", "example")
CHATTER = ("discord", "giveaway", "chart request", "paid group", "link below",
           "subscribe", "smash that", "patreon", "promo", "sign up", "telegram")


def classify(text: str) -> str:
    low = text.lower()
    if any(w in low for w in CHATTER):
        return "CHATTER"
    if any(w in low for w in TEACH):
        return "teach"
    return "-"


def main() -> None:
    conn = v.connect(v.DB_PATH)
    embedder = FastEmbedder()

    # Baseline: the score distribution over the WHOLE corpus for each query is what
    # "discrimination" has to beat. §8's real claim is that top-k barely rises above it.
    rows = conn.execute("SELECT vector FROM chunks").fetchall()
    matrix = np.stack([np.frombuffer(r[0], dtype=np.float32) for r in rows])

    print(f"corpus: {len(rows)} chunks\n")
    print(f"{'query':<52} {'top1':>6} {'top5':>6} {'p50':>6} {'sep':>6}  quality")
    print("-" * 100)

    seps = []
    for q in QUERIES:
        vec = np.asarray(embedder.embed_query(q), dtype=np.float32)
        all_scores = matrix @ vec
        p50 = float(np.median(all_scores))
        p99 = float(np.percentile(all_scores, 99))

        hits = v.search(conn, vec, k=5)
        top1 = hits[0].score
        top5 = sum(h.score for h in hits) / len(hits)
        sep = top1 - p50
        seps.append(sep)

        labels = [classify(h.text) for h in hits]
        n_teach = labels.count("teach")
        n_chatter = labels.count("CHATTER")
        quality = f"{n_teach}/5 teach" + (f"  {n_chatter} CHATTER" if n_chatter else "")

        short = q if len(q) <= 50 else q[:47] + "..."
        print(f"{short:<52} {top1:>6.3f} {top5:>6.3f} {p50:>6.3f} {sep:>6.3f}  {quality}")
        print(f"{'':<52} {'':>6} {'':>6} p99={p99:.3f}")

        for h, lab in zip(hits, labels):
            tag = "!!" if lab == "CHATTER" else ("  " if lab == "teach" else " ?")
            text = " ".join(h.text.split())[:150]
            print(f"    {tag} {h.score:.3f} {h.person[:22]:<22} {h.published_at} {text}")
        print()

    print("-" * 100)
    print(f"mean separation (top1 - corpus median): {sum(seps)/len(seps):.3f}")


if __name__ == "__main__":
    main()
