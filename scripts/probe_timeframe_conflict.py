"""What is actually inside the ~1,000 ``timeframe_conflict`` rejections (§27)?

The gate (``core/setups.py:793``) refuses when the **daily** trend contradicts the thesis,
having already let the weekly through. Two facts about it are worth measuring rather than
arguing:

1. **It fires in two structurally different situations**, because ``_family_of(RANGING)`` is
   ``None`` and the weekly check skips ``None``. So a thesis reaches the daily leg either
   because the weekly *agrees* (a genuine two-timeframe conflict) or because the weekly is
   **ranging** — no macro opinion at all, and then the daily alone kills it. That is the same
   shape §6 fixed for ``weekly_disagrees``, where 630 of 2,247 rows were dying for the absence
   of an opinion rather than the presence of a contrary one.

2. **Both legs use the same ``TREND_DEPTH``/``SWING_WIDTH``**, but a daily swing and a weekly
   swing are not the same distance in time. Anchoring two swings back therefore reads a
   different *span of history* on each leg, and two legs measuring different spans will
   disagree as a matter of construction rather than as a finding about the market.

Reads the price cache and the distilled corpus only — no network, no LLM, no cost. Reproduces
the shipped engine exactly: 74 candidates and ``timeframe_conflict=1017`` both matched
``setups --list --limit 0`` on the audit run.

    uv run python scripts/probe_timeframe_conflict.py
    uv run python scripts/probe_timeframe_conflict.py --detail ETH/BTC
    uv run python scripts/probe_timeframe_conflict.py --release --sweep

## What the audit found, 2026-07-28 — do not re-derive these

**Question 2 was pointed the wrong way, and the original guess was backwards.** Measured over
315 priced assets, the weekly leg spans a median of **125 days** and the daily leg **21** — the
weekly reads *6x more* history, not less. A 3-week reading was vetoing a 4-month one. The
pre-audit text asserted the reverse, which is what made "``TREND_DEPTH`` is too coarse" look
like the answer.

**``TREND_DEPTH`` is not the knob.** Swept 2→12 on the daily leg: no interior optimum. Depths
3–6 cost candidates outright (74 → 58) while conflicts stay flat or rise; conflicts only
collapse at 8–12, by which point the daily leg reads 69–99% of the weekly's span and has
stopped being a second opinion. **Agreement bought by redundancy is not a fix** — the same tell
``probe_freshness_weight`` records for its own flat sweep. So ``TREND_DEPTH = 2`` is doing its
job; the defect was that a short-term reading was wired as a *veto* instead of as *timing*.

**745 of 761 genuine conflicts are one shape:** the daily retracing against the weekly, which is
the textbook entry condition rather than a contradiction. The sharpest case is the 112
``downtrend / uptrend_failed_breakout / short`` rows — ``_family_of`` maps failed breaks to the
bullish family (correctly, for its own stated purpose) and that is what makes them "conflict"
with a short. Its docstring argues against the use the gate put it to.

**A stronger claim was made and withdrawn the same day. Do not restate it.** The first pass
reported that 0 of the 48 blocked candidates were zones price had traded through, and read that
as the gate refusing candidates exactly when they become tradeable. **The control shows the
baseline is also 0 of 74** — ``price_past_stop`` removes traded-through zones upstream, so every
candidate in the engine is approaching-or-inside by construction. Any future "look how
well-placed the blocked ones are" measurement needs the kept population as its control.

**``SILVER long daily`` comes back with R:R 2930.22.** Unrelated to this gate — see
``scripts/probe_stop_padding.py``, which measures why: its stop is 0.02 ATR wide.
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
    WEEKLY,
    ZONE_TIMEFRAMES,
    NotASetup,
    _family_of,
    build_context,
    collapse,
    cross_reference,
)
from core.structure import (
    RANGING,
    SWING_HIGH,
    SWING_LOW,
    SWING_WIDTH,
    TREND_DEPTH,
    confirmed_by,
    swings,
    trend_state,
)
from oracle import cache, carry, corpus, listings
from oracle.resample import to_weekly
from oracle.route import Unpriceable, load_routing_table, route
from oracle.setups_cli import CONFIG_DIR, _load_daily

CONFLICT = "timeframe_conflict"

# The worked example §27 names: its weekly verdict is hand-verified (0.050 -> 0.030 across two
# years) so the ground truth needs no chart-reading.
WORKED_EXAMPLE = "ETH/BTC"


@dataclass(frozen=True, slots=True)
class Leg:
    """One timeframe's trend reading, with the history it was computed from."""
    state: str
    span_days: int | None      # newest swing back to the older of the two anchors
    high_change: float | None  # fractional move of the newest swing high vs its anchor
    low_change: float | None
    n_highs: int
    n_lows: int


def read_leg(bars, state: str, *, as_of: date,
             depth: int = TREND_DEPTH, width: int = SWING_WIDTH) -> Leg:
    """Re-derive what ``trend_state`` looked at, so a verdict can be read against its inputs.

    Deliberately recomputed from the same primitives rather than inferred from the verdict:
    the verdict is three-valued and the question here is about the distances behind it.
    """
    found = confirmed_by(swings(bars, width=width), as_of)
    highs = [s for s in found if s.kind == SWING_HIGH]
    lows = [s for s in found if s.kind == SWING_LOW]
    if len(highs) <= depth or len(lows) <= depth:
        return Leg(state, None, None, None, len(highs), len(lows))

    def change(seq) -> float | None:
        newest, anchor = seq[-1].price, seq[-1 - depth].price
        return None if anchor <= 0 else (newest - anchor) / anchor

    newest_date = max(highs[-1].date, lows[-1].date)
    anchor_date = min(highs[-1 - depth].date, lows[-1 - depth].date)
    return Leg(
        state=state,
        span_days=(newest_date - anchor_date).days,
        high_change=change(highs),
        low_change=change(lows),
        n_highs=len(highs),
        n_lows=len(lows),
    )


@dataclass(frozen=True, slots=True)
class Conflict:
    thesis_id: str
    asset: str
    person: str
    direction: str
    published: date | None
    price: float
    weekly: Leg
    daily: Leg

    @property
    def weekly_ranging(self) -> bool:
        """True when no macro opinion existed for the daily to conflict *with*."""
        return _family_of(self.weekly.state) is None


def build_contexts(rows, *, as_of: date):
    """One ``Context`` per priceable asset, plus the daily series it came from.

    Mirrors ``setups_cli.build_candidates``' first loop. Duplicated rather than imported
    because that function returns collapsed candidates and throws the contexts away, and the
    context is the entire subject of this probe.
    """
    assets = sorted({r.asset for r in rows})
    table = load_routing_table(
        CONFIG_DIR, [(r.asset, r.domain) for r in rows],
        listings=listings.load_or_fetch(cache.DATA_ROOT / "_listings.json"),
    )
    # Carry is attached for the same reason ``build_candidates`` attaches it: ``carry_dominates``
    # is a gate, so omitting funding would quietly hand this probe a different candidate count
    # than the CLI reports and make the release measurement unreconcilable.
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
        weekly = to_weekly(daily)
        ctx = build_context(daily.bars, weekly.bars, as_of=as_of)
        if ctx is None:
            continue
        outlook = outlooks.get(asset)
        if outlook is not None:
            ctx = replace(ctx, funding=outlook)
        contexts[asset] = (daily, weekly, ctx)
    return contexts


def collect(rows, contexts, registry, *, as_of: date) -> list[Conflict]:
    """Every thesis the daily leg refuses, deduped per thesis.

    ``timeframe_conflict`` is thesis-level — it fires before any zone is consulted — so one
    pass at one zone timeframe sees every one of them, exactly as ``build_candidates``'
    ``counted_once`` set assumes.
    """
    legs: dict[str, tuple[Leg, Leg]] = {}
    ranks: dict[str, int | None] = {}
    found: list[Conflict] = []
    seen: set[str] = set()

    for row in rows:
        entry = contexts.get(row.asset)
        if entry is None:
            continue
        daily, weekly, ctx = entry
        if row.asset not in ranks:
            _, _, ranks[row.asset] = resolve_asset(row.asset, registry)
        published = parse_date(row.published_at)
        outcome = cross_reference(
            row, ctx,
            published_close=daily.close_on(published) if published is not None else None,
            zone_timeframe=WEEKLY, asset_rank=ranks[row.asset], agreement_count=0,
        )
        if not isinstance(outcome, NotASetup) or outcome.reason != CONFLICT:
            continue
        if outcome.thesis_id in seen:
            continue
        seen.add(outcome.thesis_id)

        if row.asset not in legs:
            legs[row.asset] = (
                read_leg(weekly.bars, ctx.weekly_trend, as_of=as_of),
                read_leg(daily.bars, ctx.daily_trend, as_of=as_of),
            )
        wk, dy = legs[row.asset]
        found.append(Conflict(
            thesis_id=outcome.thesis_id, asset=row.asset, person=row.person,
            direction=row.direction, published=published, price=ctx.price,
            weekly=wk, daily=dy,
        ))
    return found


def pct(value: float | None) -> str:
    return "    —" if value is None else f"{value:+6.1%}"


def report(conflicts: list[Conflict], contexts) -> None:
    total = len(conflicts) or 1

    print(f"\n{'=' * 78}\n1. WHY THE THESIS REACHED THE DAILY LEG AT ALL\n{'=' * 78}")
    ranging = [c for c in conflicts if c.weekly_ranging]
    agreeing = [c for c in conflicts if not c.weekly_ranging]
    print(f"  weekly AGREED with the thesis  {len(agreeing):5}  {len(agreeing) / total:5.1%}"
          "   <- a real two-timeframe conflict")
    print(f"  weekly was RANGING             {len(ranging):5}  {len(ranging) / total:5.1%}"
          "   <- no macro opinion to conflict with")
    print(f"  {'total':30} {len(conflicts):5}")

    print(f"\n{'=' * 78}\n2. THE VERDICT PAIRS\n{'=' * 78}")
    pairs = Counter((c.weekly.state, c.daily.state, c.direction) for c in conflicts)
    print(f"  {'weekly':28} {'daily':28} {'dir':6} {'n':>5}")
    for (wk, dy, direction), n in pairs.most_common(12):
        print(f"  {wk:28} {dy:28} {direction:6} {n:5}")

    print(f"\n{'=' * 78}\n3. HOW MUCH HISTORY EACH LEG READ\n{'=' * 78}")
    print("  Both legs anchor TREND_DEPTH=2 swings back. In days, that is not the same "
          "\n  distance on the two series. Measured over every priced asset:\n")
    spans: dict[str, list[int]] = {"weekly": [], "daily": []}
    for daily, weekly, ctx in contexts.values():
        wk = read_leg(weekly.bars, ctx.weekly_trend, as_of=ctx.as_of)
        dy = read_leg(daily.bars, ctx.daily_trend, as_of=ctx.as_of)
        if wk.span_days is not None:
            spans["weekly"].append(wk.span_days)
        if dy.span_days is not None:
            spans["daily"].append(dy.span_days)
    print(f"  {'leg':8} {'n':>5} {'p25':>7} {'median':>7} {'p75':>7}")
    for label in ("weekly", "daily"):
        s = sorted(spans[label])
        if not s:
            continue
        q = statistics.quantiles(s, n=4) if len(s) > 3 else [s[0], s[len(s) // 2], s[-1]]
        print(f"  {label:8} {len(s):5} {q[0]:7.0f} {q[1]:7.0f} {q[2]:7.0f}   days")
    if spans["weekly"] and spans["daily"]:
        ratio = statistics.median(spans["weekly"]) / max(statistics.median(spans["daily"]), 1)
        print(f"\n  the weekly leg reads {ratio:.1f}x more history than the daily leg")

    print(f"\n{'=' * 78}\n4. A SAMPLE TO READ BY HAND\n{'=' * 78}")
    by_asset: dict[str, Conflict] = {}
    for c in conflicts:
        by_asset.setdefault(c.asset, c)
    sample = sorted(by_asset.values(), key=lambda c: c.asset)[:20]
    print(f"  {'asset':12} {'dir':6} {'weekly':22} {'daily':22} "
          f"{'wk hi':>6} {'wk lo':>6} {'d hi':>6} {'d lo':>6} {'wkD':>5} {'dD':>4}")
    for c in sample:
        print(f"  {c.asset:12} {c.direction:6} {c.weekly.state:22} {c.daily.state:22} "
              f"{pct(c.weekly.high_change)} {pct(c.weekly.low_change)} "
              f"{pct(c.daily.high_change)} {pct(c.daily.low_change)} "
              f"{c.weekly.span_days or 0:5} {c.daily.span_days or 0:4}")
    print(f"\n  {len(by_asset)} distinct assets carry a timeframe_conflict")


def detail(asset: str, contexts, conflicts: list[Conflict], *, as_of: date) -> None:
    """Every swing behind both verdicts for one asset — the worked example."""
    entry = contexts.get(asset)
    if entry is None:
        print(f"\n{asset}: not priced")
        return
    daily, weekly, ctx = entry
    print(f"\n{'=' * 78}\n5. WORKED EXAMPLE: {asset}\n{'=' * 78}")
    print(f"  price {ctx.price:.6g} · weekly {ctx.weekly_trend} · daily {ctx.daily_trend}")
    rows = [c for c in conflicts if c.asset == asset]
    print(f"  {len(rows)} theses refused timeframe_conflict "
          f"({Counter(c.direction for c in rows).most_common()})")

    for label, bars in (("weekly", weekly.bars), ("daily", daily.bars)):
        found = confirmed_by(swings(bars), as_of)
        highs = [s for s in found if s.kind == SWING_HIGH]
        lows = [s for s in found if s.kind == SWING_LOW]
        leg = read_leg(bars, "", as_of=as_of)
        print(f"\n  {label}: {len(highs)} swing highs, {len(lows)} swing lows, "
              f"anchor span {leg.span_days} days")
        for kind, seq, change in (("high", highs, leg.high_change),
                                  ("low", lows, leg.low_change)):
            if len(seq) <= TREND_DEPTH:
                print(f"    {kind:5} too few to reach the anchor")
                continue
            anchor, newest = seq[-1 - TREND_DEPTH], seq[-1]
            print(f"    {kind:5} anchor {anchor.date} {anchor.price:>12.6g}"
                  f"  ->  newest {newest.date} {newest.price:>12.6g}   {pct(change)}")
            tail = " ".join(f"{s.date}:{s.price:.6g}" for s in seq[-5:])
            print(f"          last 5: {tail}")


def candidates_under(rows, contexts, registry, *, release: bool):
    """Run the whole engine, optionally with the daily leg neutralised.

    ``daily_trend`` reaches exactly one decision — the gate at ``setups.py:791`` — and is
    otherwise carried only for display, so forcing it to ``RANGING`` makes ``_family_of``
    return ``None`` and skips that one check while leaving every other gate, the scorer and
    ``collapse`` untouched. That is a measurement of the gate's cost, **not** a proposed fix:
    deleting the daily leg outright is not what §27 is asking for.
    """
    ranks: dict[str, int | None] = {}
    outcomes = []
    rejections: Counter = Counter()
    for row in rows:
        entry = contexts.get(row.asset)
        if entry is None:
            continue
        daily, _weekly, ctx = entry
        if release:
            ctx = replace(ctx, daily_trend=RANGING)
        if row.asset not in ranks:
            _, _, ranks[row.asset] = resolve_asset(row.asset, registry)
        published = parse_date(row.published_at)
        published_close = daily.close_on(published) if published is not None else None
        for zone_timeframe in ZONE_TIMEFRAMES:
            outcome = cross_reference(
                row, ctx, published_close=published_close,
                zone_timeframe=zone_timeframe,
                asset_rank=ranks[row.asset], agreement_count=0,
            )
            outcomes.append(outcome)
            if isinstance(outcome, NotASetup):
                rejections[outcome.reason] += 1
    return collapse(outcomes), rejections


def release_test(rows, contexts, registry) -> None:
    """What would the queue gain if the daily leg stopped refusing?"""
    print(f"\n{'=' * 78}\n6. WHAT THE GATE COSTS IN CANDIDATES\n{'=' * 78}")
    base, _ = candidates_under(rows, contexts, registry, release=False)
    freed, rej = candidates_under(rows, contexts, registry, release=True)

    base_keys = {c.key for c in base}
    new = [c for c in freed if c.key not in base_keys]
    print(f"  gate on   {len(base):5} candidates")
    print(f"  gate off  {len(freed):5} candidates   ({len(new):+} new)")
    if not new:
        return

    print(f"\n  the {len(new)} candidates the daily leg is currently refusing:")
    print(f"  {'asset':12} {'dir':6} {'tf':7} {'score':>6} {'R:R':>6} {'weekly trend':22}")
    for c in sorted(new, key=lambda c: -c.score)[:25]:
        print(f"  {c.asset:12} {c.direction:6} {c.zone_timeframe:7} {c.score:6.3f} "
              f"{c.reward_risk:6.2f} {c.weekly_trend:22}")
    if len(new) > 25:
        print(f"  … and {len(new) - 25} more")

    wk = Counter("weekly ranging" if _family_of(c.weekly_trend) is None
                 else "weekly agrees" for c in new)
    print("\n  split by why they reached the daily leg:")
    for label, n in wk.most_common():
        print(f"    {label:18} {n:4}")

    # The test of the retracement reading. If a daily move against the thesis were genuinely
    # contrary evidence, these zones would mostly sit behind price — already blown through.
    # If it is the *approach*, price is still on the entry side, travelling toward the zone.
    def side_of(c) -> str:
        if c.entry_bottom <= c.price <= c.entry_top:
            return "inside the zone"
        # A long is approached from above (price falls into the zone), a short from below.
        long_side = c.direction == "long"
        above = c.price > c.entry_top
        return "approaching" if above == long_side else "already through"

    print("\n  where price sits relative to the refused zone:")
    for label, n in Counter(side_of(c) for c in new).most_common():
        print(f"    {label:18} {n:4}   {n / len(new):5.1%}")
    print(f"\n  remaining rejections with the gate off: "
          f"{', '.join(f'{k}={v}' for k, v in rej.most_common(6))}")


def depth_sweep(rows, contexts, registry, *, as_of: date,
                depths=(2, 3, 4, 6, 8, 12)) -> None:
    """§27's open question: is ``TREND_DEPTH = 2`` simply too coarse for the daily leg?

    Deepening the daily anchor is the obvious tune, so measure where it leads before arguing
    about it. The span column is the point: if the daily leg only stops conflicting once it
    reads as much history as the weekly leg, the tune has not fixed the gate — it has turned
    the daily leg into a second copy of the weekly one, which can agree by construction while
    measuring nothing new.
    """
    print(f"\n{'=' * 78}\n7. DOES A DEEPER DAILY ANCHOR FIX IT?\n{'=' * 78}")
    wk_spans = [
        leg.span_days for _d, weekly, ctx in contexts.values()
        if (leg := read_leg(weekly.bars, ctx.weekly_trend, as_of=as_of)).span_days is not None
    ]
    wk_median = statistics.median(wk_spans)
    print(f"  weekly leg reads a median of {wk_median:.0f} days at depth {TREND_DEPTH}\n")
    print(f"  {'daily':>5} {'median span':>12} {'vs weekly':>10} "
          f"{'conflicts':>10} {'candidates':>11} {'ranging':>8}")

    for depth in depths:
        deepened = {}
        spans, ranging = [], 0
        for asset, (daily, weekly, ctx) in contexts.items():
            state = trend_state(daily.bars, as_of=as_of, depth=depth)
            leg = read_leg(daily.bars, state, as_of=as_of, depth=depth)
            if leg.span_days is not None:
                spans.append(leg.span_days)
            if _family_of(state) is None:
                ranging += 1
            deepened[asset] = (daily, weekly, replace(ctx, daily_trend=state))
        cands, rej = candidates_under(rows, deepened, registry, release=False)
        median = statistics.median(spans) if spans else 0
        print(f"  {depth:5} {median:9.0f} d {median / wk_median:9.0%} "
              f"{rej[CONFLICT] // len(ZONE_TIMEFRAMES):10} {len(cands):11} "
              f"{ranging:7}/{len(contexts)}")
    print("\n  'conflicts' is halved to undo this probe's per-zone-timeframe double count,"
          "\n  so it is comparable with the CLI's thesis-level tally.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detail", default=WORKED_EXAMPLE,
                    help=f"asset to dump swing-by-swing (default {WORKED_EXAMPLE})")
    ap.add_argument("--release", action="store_true",
                    help="also measure how many candidates the gate is withholding")
    ap.add_argument("--sweep", action="store_true",
                    help="also sweep the daily leg's TREND_DEPTH")
    args = ap.parse_args(argv)

    as_of = datetime.now(UTC).date()
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    contexts = build_contexts(rows, as_of=as_of)
    conflicts = collect(rows, contexts, registry, as_of=as_of)

    print(f"as_of {as_of} — {len(rows)} corpus rows, {len(contexts)} assets priced, "
          f"{len(conflicts)} theses refused {CONFLICT}")
    report(conflicts, contexts)
    detail(args.detail, contexts, conflicts, as_of=as_of)
    if args.release:
        release_test(rows, contexts, registry)
    if args.sweep:
        depth_sweep(rows, contexts, registry, as_of=as_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
