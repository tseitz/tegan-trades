"""What the X ingest actually contributes to the queue, against what it costs.

`ingest-x` is the only command in the repo billed in real dollars, and nothing measured what it
bought. The cost side has been visible since `ingestion.spend` existed; the benefit side was
never counted at all, so "is X worth it" has been answered by feel.

**The join is the thesis id's platform prefix, never the person.** A thesis id is
`x/<handle>-<date>#<hash>` or `youtube/<video>#<hash>`, and `Candidate.thesis_ids` carries them
verbatim. Attributing by *person* would be wrong in the one direction that flatters X: most of
the roster posts on both platforms, so a candidate backed entirely by YouTube would count as
X-supported the moment one of its people also has an X handle. The prefix says where the words
came from, which is the only thing `ingest-x` is responsible for.

Every candidate falls in exactly one bucket:

    X-ONLY        every supporting thesis came from X   -> dies if you stop paying
    MIXED         X and YouTube both back it            -> survives; X adds agreement
    NO-X          no X thesis backs it                  -> unaffected

**X-ONLY is the number that answers the question.** Those candidates do not exist without the
spend. MIXED is real but weaker: the zone is already on screen from YouTube, and X is adding
confirmation to something you would have seen anyway.

The one place MIXED still bites is **freshness**, which `Candidate` takes from the newest
supporting view rather than an average — deliberately, so a current voice carries a zone the
others have gone quiet on. So a MIXED candidate whose *newest* view is an X post is one whose
score X is actively holding up. That is counted separately as FRESHEST-X.

## What it measured, 2026-08-18

**X buys about one candidate a month that YouTube would not have found.** Four as-of dates,
each rebuilding the queue as it stood that day:

    as-of        queue   X-ONLY   MIXED   NO-X
    2026-08-18      58        1       6     51
    2026-08-01      35        0       3     32
    2026-07-15      42        0       2     40
    2026-06-15      34        1       6     27

**That column is a STOCK, not a flow, and reading it as a rate is the easy mistake.** A queue is
every zone standing on that date — ``asof.live_rows`` admits every thesis published on or before
it, and freshness is a decay curve rather than a cutoff, so nothing ages out of the corpus. One
X-ONLY candidate on Aug 18 means one of the 58 zones *currently standing* is X's, not one per
day and not one ever.

**The flow is ~1.4 distinct X-ONLY zones a month** (`--since 2026-05-01`, weekly grid, zones
counted by `Candidate.key` so the same zone standing four weeks running is one, not four):

    5 distinct X-ONLY zones over 16 weeks — 1.4 per month

At $18.07/month that is **~$13 per zone X alone found**. Twelve of the sixteen weekly builds had
no X-ONLY candidate standing at all.

**The queue total drifts a candidate or two between runs against live marks.**
``build_candidates`` sweeps venue marks to contradict a guessed route, so the same as-of built
twice minutes apart can gate one row differently — 60 and 58 on two runs of one afternoon. Past
dates pass ``marks_index={}`` and are therefore reproducible; only a same-day build drifts. Do
not read a ±2 change in the total as signal.

X-ONLY never exceeds 1. The corpus split says why: 136 X theses against 5,077 from YouTube —
**2.6% of the corpus**. That is not a distillation failure, it is the shape of the source. An
X digest is one handle's day; a YouTube video is forty minutes of someone walking through
charts, and it yields theses accordingly.

Against $18.07 of xAI in August, the one X-ONLY candidate cost **$18.07**, or $2.58 per
candidate X touches at all. The honest framing is the middle column: X is mostly *seconding*
zones the roster already surfaced on YouTube.

**The strongest case for keeping it is freshness, not discovery.** 3 of the 6 MIXED candidates
have an X post as their newest supporting view, and `Candidate.freshness` reads the newest view
rather than an average — so X is actively holding those three scores up. Whether a fresher
score is a *better* score is the edge question below, which this cannot answer.

## What this cannot tell you

**Contribution is not edge.** Every number here counts candidates X put in front of you, not
trades that made money. A queue slot is a cost until it is graded — and X-sourced candidates
being *more* numerous would be a reason to look harder at them, not a reason to keep paying.
Settling that needs closed trades, of which there are very few; `execution/outcome.py` is where
that answer will come from, not here.

**A quiet month is not a cheap month.** `ingest-x` bills per call in the window, not per thesis
extracted. A handle that posted nothing still costs what it costs to ask.

**Empty theses are invisible here and that flatters nothing.** A transcript that distilled to
zero theses contributes no candidate and no row; it is counted in the corpus totals so the
per-thesis yield is honest, but it cannot reach the queue either way.

Free. Reads `data/theses/` and the price cache exactly as `setups` does, places nothing, and
calls no model. Builds the same candidates the queue does, so it inherits the queue's gates —
run it after a nightly, not against a stale cache.

    uv run python scripts/probe_x_contribution.py                      # the standing queue
    uv run python scripts/probe_x_contribution.py --as-of 2026-08-01   # as it stood then
    uv run python scripts/probe_x_contribution.py --since 2026-05-01   # the flow — see below
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime

from core.canon import load_registry
from oracle import asof, cache, corpus, listings
from oracle.assemble import build_candidates
from oracle.setups_cli import CONFIG_DIR

X = "x"
YOUTUBE = "youtube"


def platform_of(thesis_id: str) -> str:
    """`x/handle-date#hash` -> `x`. The prefix is the provenance; see the module docstring."""
    return thesis_id.split("/", 1)[0]


def classify(thesis_ids) -> str:
    platforms = {platform_of(t) for t in thesis_ids}
    if platforms == {X}:
        return "X-ONLY"
    if X in platforms:
        return "MIXED"
    return "NO-X"


def freshest_platform(thesis_ids, by_id) -> str | None:
    """Which platform the newest supporting thesis came from.

    Returns None when no supporting thesis resolves — possible because `thesis_ids` outlives the
    rows it points at (a thesis can be re-distilled away while a candidate is still keyed on it),
    and silently treating that as YouTube would understate X.
    """
    rows = [by_id[t] for t in thesis_ids if t in by_id]
    if not rows:
        return None
    return platform_of(max(rows, key=lambda r: r.published_at).id)


def report_flow(since: date, until: date, step_days: int, queue_on) -> int:
    """DISTINCT X-ONLY zones across a span — the flow, against the snapshot's stock.

    The single-date report answers "how many of the zones standing today exist only because of
    X", which is a stock. It cannot answer "how many did a month of spending buy", because the
    same zone standing all month is one candidate, not thirty. Zones are counted by
    ``Candidate.key`` — content-addressed on the zone's own prices and date, so the same zone
    seen on four consecutive weeks collapses to one entry rather than four.
    """
    seen: dict[str, date] = {}
    print(f"═══ X-ONLY flow · {since.isoformat()} → {until.isoformat()} "
          f"(every {step_days}d) ═══\n")
    print(f"  {'as-of':<12} {'queue':>6} {'X-ONLY':>7} {'new':>5}")
    for when in asof.grid(since, until, step_days=step_days):
        candidates, _ = queue_on(when)
        x_only = [c for c in candidates if classify(c.thesis_ids) == "X-ONLY"]
        fresh = [c for c in x_only if c.key not in seen]
        for c in fresh:
            seen[c.key] = when
        print(f"  {when.isoformat():<12} {len(candidates):>6} {len(x_only):>7} {len(fresh):>5}")

    weeks = max((until - since).days / 7, 1)
    print(f"\n  {len(seen)} distinct X-ONLY zones over {weeks:.0f} weeks "
          f"— {len(seen) / weeks * 4.35:.1f} per month")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="What the X ingest contributes to the queue, against what it costs.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="build the queue as it stood on this date (default: today)")
    parser.add_argument("--since", type=date.fromisoformat, default=None,
                        help="walk weekly from this date to --as-of and count DISTINCT "
                             "X-ONLY zones over the span (the flow, not the standing stock)")
    parser.add_argument("--step-days", type=int, default=7,
                        help="grid spacing for --since (default: 7)")
    args = parser.parse_args()
    as_of = args.as_of or datetime.now(UTC).date()

    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    by_id = {r.id: r for r in rows}
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    today = datetime.now(UTC).date()

    def queue_on(when: date):
        """The candidates standing on ``when``.

        ``funding_venue=None`` skips the carry lookup: carry is display-only — reported, never
        scored — so it cannot change which candidates exist or how they rank, which is all this
        probe reads. It drops a network round-trip per asset.

        ``marks_index={}`` for any past date, per ``build_candidates``: a mark fetched now says
        what an instrument costs *today*, and letting it contradict a route as that route stood
        in June is an anachronism, not a check. It is also why the same as-of built twice
        against live marks can differ by a candidate or two — the grid below is stable because
        it does not use them.
        """
        return build_candidates(
            rows, registry, as_of=when, listings_map=listings_map, funding_venue=None,
            marks_index=None if when >= today else {},
        )

    if args.since:
        return report_flow(args.since, as_of, args.step_days, queue_on)

    candidates, stats = queue_on(as_of)

    # ── the corpus, before any gate ──
    corpus_by_platform = Counter(platform_of(r.id) for r in rows)
    theses_total = sum(corpus_by_platform.values())

    print(f"═══ X contribution · as-of {as_of.isoformat()} ═══\n")
    print("CORPUS  (theses that survived distillation)")
    for platform, n in corpus_by_platform.most_common():
        print(f"  {platform:<10} {n:>6}  {n / theses_total:>6.1%}")
    print(f"  {'TOTAL':<10} {theses_total:>6}")

    # ── the queue ──
    buckets = Counter(classify(c.thesis_ids) for c in candidates)
    n = len(candidates)
    print(f"\nQUEUE   ({n} candidates, from {stats.assets_priced} priced assets)")
    for bucket in ("X-ONLY", "MIXED", "NO-X"):
        count = buckets.get(bucket, 0)
        share = count / n if n else 0.0
        print(f"  {bucket:<10} {count:>6}  {share:>6.1%}")

    freshest_x = sum(
        1 for c in candidates
        if classify(c.thesis_ids) == "MIXED" and freshest_platform(c.thesis_ids, by_id) == X
    )
    print(f"\n  of the MIXED, {freshest_x} have an X post as their NEWEST view")
    print("  (freshness comes from the newest view, so X is holding those scores up)")

    # ── against the money ──
    #
    # Read from `ingestion.spend` rather than summing the nightly logs: the ledger is what the
    # monthly cap gates on, and it includes manual `ingest-x` runs the logs never saw.
    from ingestion import spend

    month_spend = spend.total()
    x_only = buckets.get("X-ONLY", 0)
    x_touching = x_only + buckets.get("MIXED", 0)
    print(f"\nCOST    ${month_spend:.2f} spent this month (xAI, real money)")
    if x_only:
        print(f"  ${month_spend / x_only:.2f} per X-ONLY candidate")
    else:
        print("  no X-ONLY candidates — every X thesis is backing a zone YouTube already found")
    if x_touching:
        print(f"  ${month_spend / x_touching:.2f} per candidate X touches at all")

    x_theses = corpus_by_platform.get(X, 0)
    if x_theses:
        print(f"  ${month_spend / x_theses:.2f} per X thesis in the corpus "
              f"(all months' theses vs this month's spend — a floor, not a rate)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
