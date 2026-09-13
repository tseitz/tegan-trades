"""How often has price left the range the premium/discount gate is reading?

`core.dealing_range.permits` fails **closed**: outside the range, `position_at` returns None,
`zone_at` returns None, and long *and* short are both refused. That is the right default — a
price below the low would otherwise clamp to 0.0 and read as the deepest possible discount,
which is the strongest signal drawn from the weakest evidence. But it means an asset that has
broken out is not merely gated, it is *ungateable*, and the queue reports that under the same
`wrong_side_of_range` label it uses for a price sitting honestly in the wrong half.

Those are two different facts and this probe separates them — the same split `IMPROVEMENTS.md`
§27 made when it pulled a ranging weekly out of `weekly_disagrees` and recovered 23 candidates.

    uv run python scripts/probe_range_staleness.py

Free. The measurement reads routing, the price cache and `core` — no LLM, no order, no money.
The reconciliation at the end costs one venue mark sweep; `--no-reconcile` skips it and makes
the whole run offline.

**Reconciles with the queue, exactly, or not at all.** The tail must equal
`uv run setups --list --limit 0 --no-triggers`. Both halves of that command matter: the mark
sweep drops assets whose venue price contradicts their route, and the H1 trigger decides each
asset's zone rung. Skipping either priced 410 assets against the engine's 404 and reported 1726
refusals against its 1704 — close enough to look right and wrong enough to hide drift.

Read `scripts/probe_chart_first.py` first; it prints the same facts for one asset, and this is
the population that card's readings are drawn from.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from core.canon import load_registry
from core.dealing_range import dealing_range
from core.setups import build_context
from core.structure import BULLISH, breaks, on_or_before
from oracle import assemble, cache, corpus, listings, trigger_feed
from oracle.resample import to_weekly
from oracle.route import Priceable, load_routing_table, route

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "cfg"

# Where "stale" starts, for the headline count only — every age is also reported as a
# distribution, so this threshold labels rather than decides. Six weeks is two confirmation
# cycles: a weekly swing needs two closed bars either side, and `to_weekly` drops the running
# week, so ~3 weeks is the *freshest* a weekly range can ever be. Twice that is a range that
# has had a fair chance to redraw and has not.
STALE_DAYS = 42


def measure(asset: str, *, as_of: date, table, series_cache):
    """Every timeframe's range for one asset, or None when it cannot be priced.

    Built the way ``oracle.assemble`` builds it — the setup rung, via ``trigger_feed`` — because
    ``context.price`` is the last bar of that rung and it is what decides inside-vs-outside.
    ``review``'s path passes daily bars and would answer a slightly different question with the
    same words; see ``probe_chart_first``'s module docstring.
    """
    resolved = route(asset, table)
    if not isinstance(resolved, Priceable):
        return None
    daily = assemble.load_daily(resolved, table=table, series_cache=series_cache)
    if daily is None:
        return None
    weekly = to_weekly(daily)
    rung, rung_series = trigger_feed.setup_rung(trigger_feed.load_cached(resolved))
    bars = daily.bars if rung_series is None else rung_series.bars
    context = build_context(bars, weekly.bars, as_of=as_of, setup_timeframe=rung)
    if context is None:
        return None
    return {
        "price": context.price,
        "rung": rung,
        "weekly": context.dealing_range,
        "daily": dealing_range(daily.bars, as_of=as_of),
        "rung_range": dealing_range(bars, as_of=as_of) if rung_series is not None else None,
        "reset": reset_range(weekly.bars, as_of=as_of, confirmed=context.dealing_range),
    }


def reset_range(bars, *, as_of: date, confirmed=None) -> tuple[float, float] | None:
    """The range TraderMayne calls a **range reset**, as ``(low, high)``.

    > "We just broke this high. This is an MSB. We've made a higher high. Where's my higher
    > low? ... That higher low to higher high, that's my new dealing range. Mark out the 50%.
    > Mark out the discount, the premium."  — TraderMayne, 2026-05-04

    **Why this is not just another way to draw a range.** ``core.dealing_range`` needs two
    *confirmed* swings, and a swing needs ``SWING_WIDTH`` closed bars either side, so after a
    breakout there is nothing to draw with until price turns back — measured 2026-09-13, 133 of
    410 priced assets had price outside their confirmed range, and ``permits`` refuses long
    *and* short there. A reset is built from the break's own origin swing and the extreme since,
    neither of which waits on confirmation, so it exists exactly when the confirmed one does not.

    **One deliberate ambiguity, resolved by Tegan on 2026-09-13.** A later episode (2026-08-17)
    puts the new low at "the first meaningful pullback" *after* the expansion rather than at the
    origin swing before it. That reading needs price to come back and leaves "meaningful"
    undefined; this one is computable the moment the break confirms. The two give different
    premium/discount lines, so if this is ever revisited, that is the fork.

    **The extreme is bounded at ``as_of`` on both ends, and the first version was not.** The
    original read every bar from the break to the end of the array, which is correct only
    because this probe runs at today. ``oracle.asof`` records that the engine never truncates
    its series — *"every reader in `core.structure` filters by `as_of`"* — so an unfiltered read
    here is a look-ahead that a past ``--as-of`` would silently answer with tomorrow's high.

    **``confirmed`` bounds which break may reset it, by level rather than by date.** Without a
    bound, ``found[-1]`` can be a break from years ago whose "extreme since" spans the whole
    history, producing a range so wide it contains everything: ``permits`` then passes and
    nothing on the card looks wrong. The bound is that the break must have closed through *this*
    range's own bound — a bullish break above ``high``, a bearish one below ``low``. A break of
    lower structure is not a reset of this range, it is a reset of something this range already
    replaced.

    **Dates were tried first and were badly wrong: 47 of 133 recovered against this rule's 89**
    (measured 2026-09-13; no bound at all recovers 95). ``DealingRange.confirmed_at`` is a
    *confirmation* date and lags the swing itself by ``SWING_WIDTH`` bars, so testing a break
    date against it compares two different clocks — and price breaking out before a later
    pullback low confirms is the ordinary case, not an edge one. Comparing levels has no clock
    in it at all. Bounding on the swing dates instead recovers 76 or 82 depending on which swing
    is used, which is closer but still answers a question about position with a date.

    With no confirmed range there is nothing to be a reset *of*, so any break is accepted. That
    is the ``no_dealing_range`` case, where having a range at all beats having none.
    """
    found = [b for b in breaks(bars, as_of=as_of) if b.origin is not None]
    if confirmed is not None:
        found = [b for b in found
                 if (b.level >= confirmed.high if b.kind == BULLISH
                     else b.level <= confirmed.low)]
    if not found:
        return None
    last = found[-1]
    after = [b for b in bars
             if on_or_before(last.date, b.date) and on_or_before(b.date, as_of)]
    if not after:
        return None
    extreme = (max(b.high for b in after) if last.kind == BULLISH
               else min(b.low for b in after))
    low, high = sorted((last.origin.price, extreme))
    return None if high <= low else (low, high)


def reset_verdict(found, price: float) -> str:
    """Same three states as ``verdict``, for a bare ``(low, high)`` pair."""
    if found is None:
        return "none"
    low, high = found
    return "inside" if low <= price <= high else "outside"


def verdict(dealing, price: float) -> str:
    """``none`` | ``outside`` | ``inside`` — the three states the gate actually distinguishes."""
    if dealing is None:
        return "none"
    return "inside" if dealing.position_at(price) is not None else "outside"


def age_days(dealing, *, as_of: date) -> int | None:
    if dealing is None:
        return None
    confirmed = dealing.confirmed_at
    return (as_of - (confirmed.date() if isinstance(confirmed, datetime) else confirmed)).days


def percentile(values: list[int], fraction: float) -> int:
    """Nearest-rank, on an already-sorted list. No numpy for four numbers."""
    return values[min(len(values) - 1, int(fraction * len(values)))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="How often price has left the range the premium/discount gate reads.")
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="measure as at a past date (YYYY-MM-DD)")
    parser.add_argument("--no-reconcile", action="store_true",
                        help="skip the full candidate build at the end. Faster, and drops the "
                             "one check that proves this probe still matches the engine.")
    args = parser.parse_args(argv)
    as_of = args.as_of or datetime.now(UTC).date()

    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    table = load_routing_table(
        CONFIG_DIR, [(r.asset, r.domain) for r in rows], listings=listings_map)

    theses_per_asset = Counter(r.asset for r in rows)
    assets = sorted(theses_per_asset)
    series_cache: dict = {}

    measured = {}
    for asset in assets:
        found = measure(asset, as_of=as_of, table=table, series_cache=series_cache)
        if found is not None:
            measured[asset] = found

    gated = Counter(verdict(m["weekly"], m["price"]) for m in measured.values())
    outside = {a: m for a, m in measured.items() if verdict(m["weekly"], m["price"]) == "outside"}
    ages = sorted(a for m in measured.values()
                  if (a := age_days(m["weekly"], as_of=as_of)) is not None)

    print(f"\nRANGE STALENESS — as of {as_of} · {len(measured)} of {len(assets)} assets priced")

    print("\n  the gated range (weekly), where price sits")
    for state, label in (("inside", "price inside"),
                         ("outside", "price OUTSIDE — long AND short both refused"),
                         ("none", "no range at all — fewer than two confirmed swings")):
        count = gated[state]
        share = f"{count / len(measured):.0%}" if measured else "—"
        print(f"    {label:52} {count:4}  ({share})")

    if ages:
        print("\n  days since the gated range was confirmed")
        print(f"    p50 {median(ages):.0f} · p75 {percentile(ages, 0.75)} "
              f"· p90 {percentile(ages, 0.90)} · max {ages[-1]}")
        stale = sum(1 for a in ages if a > STALE_DAYS)
        print(f"    older than {STALE_DAYS}d: {stale} ({stale / len(ages):.0%})")

    if outside:
        print("\n  for those OUTSIDE, what the other timeframes say")
        for key, label in (("daily", "daily range"), ("rung_range", "setup-rung range")):
            tally = Counter(verdict(m[key], m["price"]) for m in outside.values())
            print(f"    {label:20} inside {tally['inside']:4} · outside {tally['outside']:4} "
                  f"· none {tally['none']:4}")

        print("\n  and what a RANGE RESET would say for the same assets")
        reset = Counter(reset_verdict(m["reset"], m["price"]) for m in outside.values())
        print(f"    reset range          inside {reset['inside']:4} · outside "
              f"{reset['outside']:4} · none {reset['none']:4}")
        # Where inside, which half — because a reset that lands everything in premium refuses
        # every long and permits every short, which is a different outcome from "fixed" and
        # has to be seen before the gate is changed.
        halves: Counter = Counter()
        for m in outside.values():
            if reset_verdict(m["reset"], m["price"]) != "inside":
                continue
            low, high = m["reset"]
            halves["premium" if m["price"] > (low + high) / 2 else "discount"] += 1
        print(f"      of those inside:   premium {halves['premium']:4} "
              f"· discount {halves['discount']:4}")

        affected = sum(theses_per_asset[a] for a in outside)
        print(f"\n  supply affected: {len(outside)} assets carrying {affected} corpus theses")
        widest = sorted(outside, key=lambda a: -theses_per_asset[a])[:10]
        print("    most-discussed among them: " + ", ".join(
            f"{a}({theses_per_asset[a]})" for a in widest))

    if args.no_reconcile:
        print("\n  reconciliation skipped — the split above is unverified against the engine\n")
        return 0

    # **The real mark sweep, deliberately — this is the one part of the probe that costs a
    # network round trip.** Skipping it (`marks_index={}`) was tried first and made the check
    # useless: `oracle.confirm` drops assets whose venue mark contradicts their route, so an
    # empty sweep priced 410 assets against the engine's 404 and reported 1726 refusals against
    # its 1704. A reconciliation that is 22 out does not detect drift, it hides it.
    _, stats = assemble.build_candidates(
        rows, registry, as_of=as_of, listings_map=listings_map, triggers_on=False)
    refused = stats.rejections.get("wrong_side_of_range", 0)
    print(f"\n  reconciliation: {stats.assets_priced} assets priced "
          f"· {refused} rows refused wrong_side_of_range")
    print("    must equal `uv run setups --list --limit 0 --no-triggers`. `--no-triggers` is "
          "not optional in that comparison: the H1 trigger decides each asset's zone rung, so "
          "a run with it on prices different zones and refuses a different count.")
    print(f"    the measurement above covers {len(measured)} assets, which is this number plus "
          "the ones only the mark sweep rejects.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
