"""Do thesis-driven candidates beat structure-only ones? Measured, with an interval.

Free, local, re-runnable. Reads ``data/theses/`` and ``data/prices/`` only — no network, no
LLM, no fetch, nothing written. Slow: it runs the real engine once per (as-of date x arm), so
expect tens of minutes on the full grid. ``--weeks`` cuts it down for a smoke run.

**The deliverable is a confidence interval, not a verdict.** You cannot prove a null; you can
only bound it. A CI spanning zero means *not demonstrated at this sample size* — never *no
effect*. The report prints its own achieved width and minimum detectable effect so that
sentence has numbers behind it.

**The arms are a (universe x direction-rule) grid** so each factor is isolated by holding the
other fixed. ``T vs trend*P`` varies only the direction; ``trend*P vs trend*U`` varies only the
universe. Comparing ``T`` against a universe arm directly would tangle selection with direction
and answer neither.

**Pre-registered, before any number existed.** Primary contrast is ``T vs trend*P``; everything
else is descriptive. ``T > trend`` and ``T > random`` means theses earn their cost. ``T >
random`` but ``T < trend`` means they carry signal but less than following the weekly — keep the
corpus, change how direction is derived. ``T ~ random`` means the direction call adds nothing.

**Two constraints bound every number here.**

*``triggers_on=False``.* H1 bars only go back weeks, so this measures the daily/weekly engine
**without the entry trigger**. That is not the system you would trade.

*Arms are outcome-comparable, never score-comparable.* A synthetic row is published on ``as_of``
(freshness always maximal), carries no levels (``target_source`` always structural) and stands
alone (``agreement`` always zero); ``TREND`` additionally cannot trip ``weekly_disagrees``. This
report therefore emits **no cross-arm score column**, enforced rather than requested.

**Ambiguity drives more of this than any other convention**, because ``probe_replay`` found 69%
of stops reached on the very bar that filled the entry. The whole grid runs twice, resolving
one-bar stop-and-target rows pessimistically and then optimistically. **If the primary contrast
flips between them, the finding is "cannot be settled without intraday bars"** — which is an
honest result, and the report says so rather than picking the flattering half.

Usage::

    uv run python scripts/probe_evidence.py --weeks 4       # smoke run
    uv run python scripts/probe_evidence.py                 # the full grid
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[1]

from core.canon import load_registry  # noqa: E402
from core.score import BOOTSTRAP_ITERATIONS, DEFAULT_SEED  # noqa: E402
from oracle import cache, corpus, listings, replay  # noqa: E402
from oracle.asof import (  # noqa: E402
    ALWAYS_LONG,
    ALWAYS_SHORT,
    DEFAULT_WARMUP_DAYS,
    RANDOM,
    THESIS,
    TREND,
    grid,
    live_rows,
    synthetic_rows,
    warmed,
)
from oracle.assemble import CONFIG_DIR, build_candidates, load_daily  # noqa: E402
from oracle.route import Unpriceable, load_routing_table, route  # noqa: E402

# Days of forward bars a candidate is measured over. A boundary on the MEASUREMENT, not a claim
# that the trade ended — §2 wants the fixed horizons deleted and ``replay`` refuses to invent
# one. Unresolved rows are marked to market at the end of it rather than dropped.
TAIL_DAYS = 90

# Seeds for the RANDOM arm. One draw is itself a coin flip; the spread across seeds is the null
# band. Twenty is enough to see the band's width without dominating runtime.
RANDOM_SEEDS = 20

# Reusing ``core.score``'s bootstrap convention wholesale rather than adding a third — probe_replay
# already runs its own (seed 11 / 20k rounds) and two is one too many.
BOOTSTRAP_SEED = DEFAULT_SEED
BOOTSTRAP_ROUNDS = BOOTSTRAP_ITERATIONS

PAIRED, UNIVERSE = "P", "U"

# (label, universe, rule). ``flat`` is omitted: it takes no position, so it generates no rows and
# its mean R is 0 by definition rather than by measurement.
ARMS = (
    ("T", PAIRED, THESIS),
    ("trend*P", PAIRED, TREND),
    ("trend*U", UNIVERSE, TREND),
    ("random*P", PAIRED, RANDOM),
    ("random*U", UNIVERSE, RANDOM),
    ("long*P", PAIRED, ALWAYS_LONG),
    ("long*U", UNIVERSE, ALWAYS_LONG),
    ("short*P", PAIRED, ALWAYS_SHORT),
)

PRIMARY = ("T", "trend*P")


@dataclass(frozen=True)
class Row:
    """One candidate, resolved. ``r`` is 0.0 for a nofill — the queue delivered a limit that
    never traded, which is a real outcome of generating it and not a missing value."""
    arm: str
    as_of: date
    asset: str
    state: str
    r: float
    filled: bool
    resolved: bool
    same_bar: bool


def load_world():
    """Everything as-of generation needs, loaded once."""
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    known = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    table = load_routing_table(CONFIG_DIR, [(r.asset, r.domain) for r in rows], listings=known)

    series_by_asset, unroutable = {}, Counter()
    cache_miss = 0
    for asset in sorted({r.asset for r in rows}):
        resolved = route(asset, table)
        if isinstance(resolved, Unpriceable):
            unroutable[resolved.reason] += 1
            continue
        daily = load_daily(resolved, table=table, series_cache={})
        if daily is None or not daily.bars:
            cache_miss += 1
            continue
        series_by_asset[asset] = daily
    return registry, rows, known, series_by_asset, unroutable, cache_miss


def arm_rows(universe, rule, *, as_of, corpus_rows, series_by_asset, seed):
    """The rows this arm is offered on this date, and what it was refused."""
    live = live_rows(corpus_rows, as_of)
    mentioned = {r.asset for r in live}
    pool = {a: s for a, s in series_by_asset.items()
            if universe == UNIVERSE or a in mentioned}
    if rule == THESIS:
        # Restricted to warmed assets so every arm is offered the same universe. Without this
        # T would be judged on assets the null arms were never allowed to see.
        warm = {a for a, s in pool.items() if warmed(s.bars, as_of, warmup_days=WARMUP)}
        return [r for r in live if r.asset in warm], {}
    return synthetic_rows(pool, as_of, rule=rule, seed=seed, warmup_days=WARMUP)


def resolve_arm(candidates, series_by_asset, as_of, *, ambiguity):
    out = []
    for c in candidates:
        series = series_by_asset.get(c.asset)
        if series is None:
            continue
        outcome = replay.resolve(
            entry=c.entry, stop=c.stop, target=c.target, direction=c.direction,
            bars=series.bars, from_date=as_of, tail_days=TAIL_DAYS, ambiguity=ambiguity,
        )
        out.append((c, outcome))
    return out


def run_grid(*, dates, registry, corpus_rows, known, series_by_asset, ambiguity, out=print):
    rows: list[Row] = []
    gate_seen: Counter = Counter()
    gate_kept: Counter = Counter()
    for i, as_of in enumerate(dates, 1):
        out(f"  [{i}/{len(dates)}] {as_of}", flush=True)
        for label, universe, rule in ARMS:
            seeds = range(RANDOM_SEEDS) if rule == RANDOM else (0,)
            for seed in seeds:
                offered, _skipped = arm_rows(
                    universe, rule, as_of=as_of,
                    corpus_rows=corpus_rows, series_by_asset=series_by_asset, seed=seed,
                )
                if not offered:
                    continue
                candidates, _stats = build_candidates(
                    offered, registry, as_of=as_of, listings_map=known,
                    funding_venue=None, triggers_on=False,
                    marks_index={}, series_cache=dict(series_by_asset),
                )
                gate_seen[label] += len(offered)
                gate_kept[label] += len(candidates)
                for c, o in resolve_arm(candidates, series_by_asset, as_of,
                                        ambiguity=ambiguity):
                    rows.append(Row(
                        arm=label, as_of=as_of, asset=c.asset, state=o.state,
                        r=0.0 if o.r is None else o.r,
                        filled=o.state != replay.NOFILL,
                        resolved=o.state in replay.RESOLVED,
                        same_bar=o.same_bar,
                    ))
    return rows, gate_seen, gate_kept


# ── statistics ──────────────────────────────────────────────────────────────

def cluster_bootstrap(rows, *, rounds=BOOTSTRAP_ROUNDS, seed=BOOTSTRAP_SEED):
    """CI on mean R, resampling **assets** rather than rows.

    BTC alone is over a quarter of the corpus and every asset recurs across all as-of dates, so
    rows are nowhere near independent. Resampling them would shrink the interval by roughly the
    square root of the cluster size — which is not a tighter estimate, it is a wrong one.
    """
    by_asset = defaultdict(list)
    for r in rows:
        by_asset[r.asset].append(r.r)
    assets = list(by_asset)
    if len(assets) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(rounds):
        drawn = [rng.choice(assets) for _ in assets]
        pool = [v for a in drawn for v in by_asset[a]]
        if pool:
            means.append(mean(pool))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    return lo, hi


def summarise(rows):
    if not rows:
        return None
    filled = [r for r in rows if r.filled]
    return {
        "n": len(rows),
        "assets": len({r.asset for r in rows}),
        "fill_rate": len(filled) / len(rows),
        "resolution_rate": sum(1 for r in rows if r.resolved) / len(rows),
        "ambiguity_rate": (sum(1 for r in rows if r.state == replay.AMBIGUOUS) / len(filled)
                           if filled else 0.0),
        "same_bar_rate": (sum(1 for r in filled if r.same_bar) / len(filled)
                          if filled else 0.0),
        "mean_r": mean(r.r for r in rows),
        "mean_r_filled": mean(r.r for r in filled) if filled else 0.0,
        "ci": cluster_bootstrap(rows),
    }


def contrast(rows_by_arm, a, b):
    """Difference in mean R per candidate, clustered on asset over the UNION of both arms.

    **Not paired on shared assets, and the first version was — which measured nothing.** When
    two arms agree on an asset's direction they produce the same zone, so ``collapse`` gives
    them identical entry/stop/target and therefore identical outcomes. Restricting to shared
    assets restricts to the subset where the arms mostly agree, and the difference collapses to
    a literal zero with a zero-width interval. The smoke run reported exactly that.

    The effect lives in the *disagreements* and in the candidates one arm generates and the
    other does not. Resampling the union preserves both: an asset only one arm reached
    contributes to one side, which is a real difference between the arms rather than noise to
    be paired away.
    """
    left = defaultdict(list)
    right = defaultdict(list)
    for r in rows_by_arm.get(a, []):
        left[r.asset].append(r.r)
    for r in rows_by_arm.get(b, []):
        right[r.asset].append(r.r)
    assets = sorted(set(left) | set(right))
    if len(assets) < 2 or not left or not right:
        return None

    def diff_over(drawn):
        lv = [v for s in drawn for v in left.get(s, ())]
        rv = [v for s in drawn for v in right.get(s, ())]
        return (mean(lv) - mean(rv)) if lv and rv else None

    point = diff_over(assets)
    if point is None:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    draws = []
    for _ in range(BOOTSTRAP_ROUNDS):
        d = diff_over([rng.choice(assets) for _ in assets])
        if d is not None:
            draws.append(d)
    if len(draws) < BOOTSTRAP_ROUNDS // 2:
        return None
    draws.sort()
    return (point, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws))], len(assets))


def direction_agreement(rows_by_arm, a, b):
    """How often the two arms took the same side on the same asset and date.

    The diagnostic that explains a small contrast. Agreement means identical zones and
    therefore identical outcomes, so a contrast can only be as large as the disagreement rate
    permits — a near-zero difference on 95% agreement says the arms rarely differ, not that
    differing does not matter.
    """
    def keyed(arm):
        return {(r.as_of, r.asset) for r in rows_by_arm.get(arm, [])}
    left, right = keyed(a), keyed(b)
    both = left & right
    return len(both), len(left | right)


# ── report ──────────────────────────────────────────────────────────────────

def report(rows, gate_seen, gate_kept, *, ambiguity, world_stats, dates, out=print):
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r.arm].append(r)

    out(f"\n{'=' * 78}")
    out(f"AMBIGUITY RESOLVED {ambiguity.upper()}")
    out(f"{'=' * 78}")
    out("  triggers_on=FALSE — the daily/weekly engine WITHOUT the entry trigger.")
    out("  This is not the system you would trade. H1 history does not reach these dates.")
    out(f"  primary contrast (pre-registered): {PRIMARY[0]} vs {PRIMARY[1]}; the rest describe.")
    out(f"  as-of dates {len(dates)}  ({dates[0]} .. {dates[-1]}, weekly)"
        f"   tail {TAIL_DAYS}d   warmup {WARMUP}d")
    unroutable, cache_miss = world_stats
    out(f"  BOUNDS — corpus assets that do not route today: {sum(unroutable.values())} "
        f"({dict(unroutable)})")
    out(f"           routable but absent from the price cache: {cache_miss}")
    out("           both are survivorship: assets that died are simply not here.")

    out(f"\n  {'arm':<10} {'n':>6} {'assets':>7} {'fill':>6} {'resolv':>7} {'ambig':>6} "
        f"{'gate':>6} {'meanR':>8} {'95% CI':>18}")
    for label, _, _ in ARMS:
        s = summarise(by_arm.get(label, []))
        if s is None:
            out(f"  {label:<10} {'—':>6}   no rows")
            continue
        seen = gate_seen.get(label, 0)
        gate = f"{100 * gate_kept.get(label, 0) / seen:.0f}%" if seen else "—"
        ci = f"[{s['ci'][0]:+.3f}, {s['ci'][1]:+.3f}]" if s["ci"] else "—"
        out(f"  {label:<10} {s['n']:>6} {s['assets']:>7} {s['fill_rate']:>5.0%} "
            f"{s['resolution_rate']:>6.0%} {s['ambiguity_rate']:>5.0%} {gate:>6} "
            f"{s['mean_r']:>+8.3f} {ci:>18}")
    out("\n  no score column, by construction: the synthetic arms degenerate freshness,")
    out("  agreement and target_source, so a cross-arm score would compare different terms.")

    ambig_total = sum(1 for r in rows if r.state == replay.AMBIGUOUS)
    if not ambig_total:
        out("\n  AMBIGUITY: zero rows hit stop and target in one bar, so the pessimistic and")
        out("  optimistic runs are identical and the convention decides nothing here. Expected:")
        out("  targets sit ~7 daily ranges out, so catching both inside one session is rare.")

    out("\n  CONTRASTS (clustered on asset, over the union of both arms)")
    for a, b in ((PRIMARY[0], PRIMARY[1]), ("T", "random*P"), ("T", "long*P"),
                 ("trend*P", "trend*U")):
        c = contrast(by_arm, a, b)
        agreed, total = direction_agreement(by_arm, a, b)
        if c is None:
            out(f"    {a:<10} - {b:<10}   too few assets to bootstrap")
            continue
        diff, lo, hi, n = c
        spans = lo <= 0 <= hi
        verdict = "NOT DEMONSTRATED at this n" if spans else "separates"
        marker = "*" if (a, b) == PRIMARY else " "
        overlap = f"{100 * agreed / total:.0f}%" if total else "--"
        out(f"  {marker} {a:<10} - {b:<10} {diff:>+7.3f}R  "
            f"[{lo:+.3f}, {hi:+.3f}]  n={n:>3} assets  overlap {overlap:>4}   {verdict}")
        if (a, b) == PRIMARY:
            out(f"      CI width {hi - lo:.3f}R -- the smallest effect this sample could have"
                f" shown is ~{(hi - lo) / 2:.3f}R")
    out("\n  `overlap` is the share of (date, asset) cells both arms produced a candidate for.")
    out("  Where they agree on direction they draw the SAME zone and resolve identically, so a")
    out("  high overlap caps how large any contrast can be -- read it before reading the R.")
    out("  The response to a too-wide interval is more as-of dates or more corpus, not a")
    out("  softer threshold.")


def main() -> int:
    global WARMUP
    ap = argparse.ArgumentParser(description="Do theses beat structure? See __doc__.")
    ap.add_argument("--weeks", type=int, default=0,
                    help="use only the most recent N as-of dates (smoke run; 0 = full grid)")
    ap.add_argument("--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS)
    ap.add_argument("--tail-days", type=int, default=TAIL_DAYS)
    args = ap.parse_args()
    WARMUP = args.warmup_days

    print("loading corpus, routing table and price cache ...", flush=True)
    registry, corpus_rows, known, series_by_asset, unroutable, cache_miss = load_world()
    if not series_by_asset:
        print("no cached series — run `uv run fetch-prices` first", file=sys.stderr)
        return 1

    today = datetime.now(UTC).date()
    earliest = min(b.date for s in series_by_asset.values() for b in s.bars[:1])
    start = earliest + timedelta(days=WARMUP)
    end = today - timedelta(days=args.tail_days)
    dates = grid(start, end)
    if args.weeks:
        dates = dates[-args.weeks:]
    if not dates:
        print(f"no as-of dates: warmup {WARMUP}d + tail {args.tail_days}d exceeds the cache "
              f"(earliest bar {earliest})", file=sys.stderr)
        return 1

    print(f"{len(series_by_asset)} priced assets, {len(corpus_rows)} corpus rows, "
          f"{len(dates)} as-of dates\n")

    for ambiguity in (replay.PESSIMISTIC, replay.OPTIMISTIC):
        print(f"running the grid, ambiguity={ambiguity} ...", flush=True)
        rows, seen, kept = run_grid(
            dates=dates, registry=registry, corpus_rows=corpus_rows, known=known,
            series_by_asset=series_by_asset, ambiguity=ambiguity,
        )
        report(rows, seen, kept, ambiguity=ambiguity,
               world_stats=(unroutable, cache_miss), dates=dates)
    return 0


WARMUP = DEFAULT_WARMUP_DAYS

if __name__ == "__main__":
    raise SystemExit(main())
