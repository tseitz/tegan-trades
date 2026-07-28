"""How wide is a candidate's stop, measured in the noise of its own instrument?

The measurement behind ``core.setups.STOP_PAD_ATR``, and the one to re-run before changing it.

``OrderBlock.stop`` is the zone's far edge **exactly**, so the raw ``risk`` is the zone's height
and nothing else. A zone's height is a fact about one candle on the day it formed; it carries no
information about how much the instrument moves *today*. Where the two coincide, ordinary noise
takes the trade out rather than being wrong does. ``cross_reference`` therefore pads the stop by
``STOP_PAD_ATR`` ATRs, and this script prices both halves of that decision:

1. **Is it a population problem or one anecdote?** The distribution of stop width in ATRs across
   the live candidate set. Measured 2026-07-28: 41 of 93 candidates under 1 ATR, 39 of them
   daily zones — 67% of the daily population against 6% of the weekly one.
2. **What does ``k`` cost?** ``--sweep`` runs the whole engine at each multiple. Padding widens
   risk, so every reward-to-risk ratio falls and some candidates drop through
   ``MIN_REWARD_RISK``. 1.0 was chosen there: it empties the sub-1-ATR band outright for 5 of 93.

It also answers §19's question about whether a very high R:R is itself the symptom of a broken
denominator — section 4 of the output. It is, monotonically.

Reproduces the shipped engine rather than modelling it: at ``k=0`` the candidate count
reconciles with ``setups --list --limit 0``. Reads the price cache and the distilled corpus
only — no network, no LLM, no cost.

    uv run python scripts/probe_stop_padding.py
    uv run python scripts/probe_stop_padding.py --sweep
    uv run python scripts/probe_stop_padding.py --detail GOOGL
"""
from __future__ import annotations

import argparse
import statistics
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from core.canon import load_registry, resolve_asset
from core.rank import parse_date
from core.setups import (
    ZONE_TIMEFRAMES,
    NotASetup,
    build_context,
    collapse,
    cross_reference,
)
from oracle import cache, carry, corpus, listings
from oracle.resample import to_weekly
from oracle.route import Unpriceable, load_routing_table, route
from oracle.setups_cli import CONFIG_DIR, _load_daily

# The motivating case, and the one worth being able to read line by line.
WORKED_EXAMPLE = "GOOGL"

# Reference points for the histogram. A stop under 1 ATR is inside a single ordinary bar's
# range — the condition where ordinary noise takes the trade out.
BANDS = (0.5, 1.0, 1.5, 2.0, 3.0)

# Candidate multiples to price. 0.0 is the shipped default and reconciles against
# ``setups --list --limit 0``; the top of the range is deliberately past anything plausible so
# the cost curve's shape is visible rather than just its first two points.
SWEEP = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)


@dataclass(frozen=True, slots=True)
class Row:
    """One candidate, with the volatility yardstick its asset was priced against."""
    asset: str
    direction: str
    zone_timeframe: str
    entry: float
    stop: float
    risk: float
    atr: float | None
    reward_risk: float
    score: float

    @property
    def in_atr(self) -> float | None:
        """The stop's width in ATRs. None when the asset has too little history for an ATR."""
        if not self.atr:
            return None
        return self.risk / self.atr


def build_contexts(rows, *, as_of: date):
    """One ``Context`` per priceable asset, plus the daily series it came from.

    Mirrors ``setups_cli.build_candidates``' first loop, and is duplicated for the same reason
    ``probe_timeframe_conflict`` duplicates it: that function returns collapsed candidates and
    throws the contexts away, and ``Context.atr`` is the entire subject of this probe.
    """
    assets = sorted({r.asset for r in rows})
    table = load_routing_table(
        CONFIG_DIR, [(r.asset, r.domain) for r in rows],
        listings=listings.load_or_fetch(cache.DATA_ROOT / "_listings.json"),
    )
    # Carry is attached because ``carry_dominates`` is a gate: omitting funding would hand this
    # probe a different candidate count than the CLI reports and make the baseline unreconcilable.
    outlooks = carry.outlooks_for(assets, venue=carry.DEFAULT_VENUE)

    series_cache: dict = {}
    contexts: dict[str, tuple] = {}
    for asset in assets:
        resolved = route(asset, table)
        if isinstance(resolved, Unpriceable):
            continue
        daily = _load_daily(resolved, table=table, series_cache=series_cache)
        if daily is None:
            continue
        ctx = build_context(daily.bars, to_weekly(daily).bars, as_of=as_of)
        if ctx is None:
            continue
        outlook = outlooks.get(asset)
        if outlook is not None:
            ctx = replace(ctx, funding=outlook)
        contexts[asset] = (daily, ctx)
    return contexts


def candidates_under(rows, contexts, registry, **kwargs):
    """Run the whole engine and collapse, exactly as ``build_candidates`` does.

    ``agreement_count=0`` throughout: agreement is scored, never gated, so it cannot move the
    candidate *count* this probe reconciles against ``setups --list --limit 0``. Scores here are
    therefore not the queue's scores, and nothing below reads them as such.
    """
    ranks: dict[str, int | None] = {}
    outcomes = []
    rejections: Counter = Counter()
    for row in rows:
        entry = contexts.get(row.asset)
        if entry is None:
            continue
        daily, ctx = entry
        if row.asset not in ranks:
            _, _, ranks[row.asset] = resolve_asset(row.asset, registry)
        published = parse_date(row.published_at)
        published_close = daily.close_on(published) if published is not None else None
        for zone_timeframe in ZONE_TIMEFRAMES:
            outcome = cross_reference(
                row, ctx, published_close=published_close,
                zone_timeframe=zone_timeframe,
                asset_rank=ranks[row.asset], agreement_count=0,
                **kwargs,
            )
            outcomes.append(outcome)
            if isinstance(outcome, NotASetup):
                rejections[outcome.reason] += 1
    return collapse(outcomes), rejections


def measure(candidates, contexts) -> list[Row]:
    """Attach each candidate's asset ATR to its stop width."""
    found: list[Row] = []
    for c in candidates:
        entry = contexts.get(c.asset)
        atr = entry[1].atr if entry is not None else None
        found.append(Row(
            asset=c.asset, direction=c.direction, zone_timeframe=c.zone_timeframe,
            entry=c.entry, stop=c.stop, risk=abs(c.entry - c.stop), atr=atr,
            reward_risk=c.reward_risk, score=c.score,
        ))
    return found


def quantiles(values: list[float]) -> str:
    if not values:
        return "—"
    ordered = sorted(values)

    def at(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    return (f"p10 {at(0.10):5.2f}  p25 {at(0.25):5.2f}  med {statistics.median(ordered):5.2f}  "
            f"p75 {at(0.75):5.2f}  p90 {at(0.90):5.2f}")


def priced_rows(found: list[Row]) -> list[tuple[Row, float]]:
    """Each candidate paired with its stop width in ATRs, dropping those without an ATR."""
    return [(r, width) for r in found if (width := r.in_atr) is not None]


def report(found: list[Row]) -> None:
    priced = priced_rows(found)
    print(f"\n{'=' * 78}\n1. STOP WIDTH IN ATRs\n{'=' * 78}")
    print(f"{len(found)} candidates, {len(priced)} with a computable ATR "
          f"({len(found) - len(priced)} short of the 14-bar window)\n")

    print(f"  all      n={len(priced):3d}  {quantiles([w for _, w in priced])}")
    for timeframe in ZONE_TIMEFRAMES:
        subset = [w for r, w in priced if r.zone_timeframe == timeframe]
        print(f"  {timeframe:<8} n={len(subset):3d}  {quantiles(subset)}")

    print(f"\n{'=' * 78}\n2. HOW MANY SIT INSIDE ORDINARY NOISE\n{'=' * 78}")
    print("  A stop under 1 ATR is narrower than a single ordinary bar's range.\n")
    for band in BANDS:
        under = [r for r, w in priced if w < band]
        by_tf = Counter(r.zone_timeframe for r in under)
        share = 100 * len(under) / len(priced) if priced else 0
        spread = "  ".join(f"{tf} {by_tf.get(tf, 0)}" for tf in ZONE_TIMEFRAMES)
        print(f"  under {band:>4.1f} ATR   {len(under):3d}  ({share:4.1f}%)   {spread}")

    print(f"\n{'=' * 78}\n3. THE TIGHTEST STOPS IN THE QUEUE\n{'=' * 78}")
    print(f"  {'asset':<10} {'dir':<6} {'tf':<7} {'stop':>10} {'ATR':>10} "
          f"{'in ATR':>7} {'R:R':>8} {'score':>6}")
    for r, width in sorted(priced, key=lambda pair: pair[1])[:15]:
        print(f"  {r.asset:<10} {r.direction:<6} {r.zone_timeframe:<7} {r.risk:>10.4g} "
              f"{r.atr:>10.4g} {width:>7.2f} {r.reward_risk:>8.2f} {r.score:>6.3f}")

    inflation(priced)


def inflation(priced: list[tuple[Row, float]]) -> None:
    """Does a tight stop *produce* the implausible reward-to-risk ratios §19 flags?

    ``reward_risk`` divides by the stop width, so a stop that is a fraction of a day's range
    inflates the ratio mechanically. §19 leaves open whether "a very high R:R is itself the
    symptom of a broken denominator"; if that is right, the two populations coincide.
    """
    print(f"\n{'=' * 78}\n4. TIGHT STOPS AGAINST IMPLAUSIBLE R:R (§19's open question)\n{'=' * 78}")
    print(f"  {'R:R band':<16} {'n':>4}  median stop in ATR")
    bands = ((0.0, 3.0), (3.0, 10.0), (10.0, 30.0), (30.0, float("inf")))
    for low, high in bands:
        subset = [w for r, w in priced if low <= r.reward_risk < high]
        label = f"{low:g} – {high:g}" if high != float("inf") else f"{low:g}+"
        median = f"{statistics.median(subset):.2f}" if subset else "—"
        print(f"  {label:<16} {len(subset):>4}  {median:>18}")


def detail(asset: str, found: list[Row]) -> None:
    rows = [r for r in found if r.asset.upper() == asset.upper()]
    print(f"\n{'=' * 78}\n5. WORKED EXAMPLE — {asset.upper()}\n{'=' * 78}")
    if not rows:
        print(f"  {asset.upper()} is not in the candidate set today.")
        return
    for r in rows:
        atr = f"{r.atr:.4g}" if r.atr else "unknown"
        print(f"  {r.zone_timeframe:<7} {r.direction:<6} entry {r.entry:<10.4g} "
              f"stop {r.stop:<10.4g} risk {r.risk:<10.4g} ATR {atr}")
        width = r.in_atr
        if width is not None:
            print(f"          the stop is {width:.2f} ATRs wide, R:R {r.reward_risk:.2f}")


def sweep(rows, contexts, registry, *, multiples: tuple[float, ...]) -> None:
    """What each ``k`` costs in candidates.

    Padding widens risk, so every reward-to-risk ratio falls and some candidates drop through
    ``MIN_REWARD_RISK``. That cost cannot be predicted from the ATR histogram alone, because
    ``risk`` also feeds ``_reasonable``: a wider stop makes a stated target *less* believable,
    so padding changes which target a candidate gets, not only the arithmetic on it.
    """
    print(f"\n{'=' * 78}\n6. WHAT EACH k COSTS\n{'=' * 78}")
    print(f"  {'k':>5} {'cands':>6} {'lost':>5}  {'under 1 ATR':>13}  "
          f"{'median stop':>11}  {'median R:R':>10}")

    baseline = None
    for k in multiples:
        candidates, _ = candidates_under(rows, contexts, registry, stop_pad_atr=k)
        found = priced_rows(measure(candidates, contexts))
        if baseline is None:
            baseline = len(candidates)
        widths = [w for _, w in found]
        ratios = [r.reward_risk for r, _ in found]
        noisy = sum(1 for w in widths if w < 1.0)
        share = 100 * noisy / len(widths) if widths else 0
        print(f"  {k:>5.2f} {len(candidates):>6} {baseline - len(candidates):>5}  "
              f"{noisy:>4} ({share:4.1f}%)  {statistics.median(widths):>11.2f}  "
              f"{statistics.median(ratios):>10.2f}")

    print("\n  'lost' is against k=0. 'under 1 ATR' is the population the padding exists to")
    print("  empty — a stop inside a single ordinary bar's range.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detail", default=WORKED_EXAMPLE,
                    help=f"asset to dump candidate by candidate (default {WORKED_EXAMPLE})")
    ap.add_argument("--sweep", action="store_true",
                    help="also sweep the padding multiple k against the live population")
    args = ap.parse_args(argv)

    as_of = datetime.now(UTC).date()
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    contexts = build_contexts(rows, as_of=as_of)
    candidates, _ = candidates_under(rows, contexts, registry)
    found = measure(candidates, contexts)

    print(f"as_of {as_of} — {len(rows)} corpus rows, {len(contexts)} assets priced, "
          f"{len(candidates)} candidates")
    report(found)
    detail(args.detail, found)
    if args.sweep:
        sweep(rows, contexts, registry, multiples=SWEEP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
