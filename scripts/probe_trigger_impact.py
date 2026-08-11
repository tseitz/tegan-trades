"""What would the H1 trigger do to today's queue, if it were switched on?

The gate refuses a candidate whose trigger cannot be computed, and enters only where one has
actually fired. Both of those *remove* rows from a queue that is already small, so the number
that matters before wiring any of it in is: how many survive, and for what reasons do the rest
not. A gate that empties the queue is not a gate, it is an outage.

Reads the queue exactly as ``setups`` builds it — same corpus, same routing, same collapse — and
then evaluates the trigger against each candidate's own H1 bars, on the instrument an order
would actually reach (``trade_symbol``, so RUT is judged on IWM).

``zone_tagged`` is ``setups_cli.is_inside_zone``, not a second definition invented here: the
queue already decides whether price has reached a zone, and the trigger's step 1 is that same
question. Two answers to it would be a bug waiting.

MEASURED 2026-08-05 against a 49-candidate queue:

    as the gate would run it              ignoring the zone tag
      no_zone_tag   29  (59%)               no_trigger   38  (78%)
      no_trigger    16  (33%)               fired         6  (12%)
      no_hourly      3   (6%)               armed         2   (4%)
      fired          1   (2%)               unreadable    0   (0%)
      unreadable     0   (0%)

**One of 49 is enterable right now**, and six more would be if price reached their zones. That
is the trigger working as intended rather than a fault: the queue is mostly *watch*, not *act*,
and it had no way to say so before.

**The readability gate is inert, and that is the finding.** The plan decided that a candidate whose
trigger cannot be computed is not offered — but nothing on a real queue is ever in that state.
Every routable instrument clears the bar-count, volume and swing checks; even ``INTL``, the
worked example, has all three. What actually keeps ``INTL`` from being offered is the
single-print filter on gap edges, which makes it fail to *fire* rather than fail to *compute*.

So the gate is a safety net that currently catches nothing, and widening it is not obviously
right: `scripts/probe_intraday_gaps.py` looked for a global thinness threshold and found none
that separates a dead instrument from a liquid one with dead overnight hours. The useful change
is therefore to **annotate every candidate with its trigger state** rather than to drop rows —
which is also what was asked for: gated candidates "should at least be noted when I run our
execution run".

Note the ordering trap this probe exists to expose: ``detect`` refuses on the zone tag first, so
an instrument with no readable structure reports ``NO_ZONE_TAG`` and the gate never sees it.
"Not yet" and "never" are different questions and only the second column above answers the second.

Run: `uv run python scripts/probe_trigger_impact.py` (network: prices + H1, no cost tier).
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from core import trigger
from core.canon import load_registry
from oracle import cache, corpus, listings, trigger_feed
from oracle.assemble import CONFIG_DIR, build_candidates, is_inside_zone
from oracle.route import Unpriceable, route, the_routing_table


def main() -> int:
    as_of = datetime.now(UTC).date()
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    candidates, stats = build_candidates(
        rows, registry, as_of=as_of, listings_map=listings_map, funding_venue=None,
    )
    print(f"{stats.candidate_count} candidates in today's queue\n")

    table = the_routing_table(CONFIG_DIR, rows=rows, listings_map=listings_map)

    verdicts: Counter = Counter()
    ungated: Counter = Counter()
    tagged = 0
    print(f"{'asset':8} {'dir':6} {'tagged':7} {'state':12} {'entry':>10} {'stop':>10}")
    for candidate in candidates:
        resolved = route(candidate.asset, table)
        if isinstance(resolved, Unpriceable):
            verdicts["unroutable"] += 1
            continue
        hourly = trigger_feed.load_or_fetch(resolved)
        if hourly is None:
            verdicts["no_hourly"] += 1
            print(f"{candidate.asset:8} {candidate.direction:6} {'-':7} no hourly bars")
            continue
        in_zone = is_inside_zone(candidate)
        tagged += in_zone
        found = trigger.detect(hourly.bars, direction=candidate.direction, zone_tagged=in_zone)
        verdicts[found.state] += 1
        # The same bars judged as if price *were* in the zone. This separates "not yet" from
        # "never": ``detect`` refuses on the zone tag first, so an instrument with no readable
        # structure reports NO_ZONE_TAG and the gate never sees it.
        ungated[trigger.detect(hourly.bars, direction=candidate.direction,
                               zone_tagged=True).state] += 1
        entry = f"{found.entry:.6g}" if found.entry is not None else "-"
        stop = f"{found.stop:.6g}" if found.stop is not None else "-"
        print(f"{candidate.asset:8} {candidate.direction:6} {in_zone!s:7} "
              f"{found.state:12} {entry:>10} {stop:>10}")

    total = len(candidates)
    print(f"\n{total} candidates, {tagged} with price already in the zone")
    for state, count in verdicts.most_common():
        print(f"  {state:12} {count:4}  ({count / total:.0%})")
    print("\nignoring the zone tag — what these bars could support at all:")
    for state, count in ungated.most_common():
        print(f"  {state:12} {count:4}  ({count / total:.0%})")

    fired = verdicts[trigger.FIRED]
    armed = verdicts[trigger.ARMED]
    print(f"\nwould be offered as entries now: {fired}")
    print(f"waiting on a pullback (armed):    {armed}")
    print(f"refused by the gate (unreadable): {verdicts[trigger.UNREADABLE]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
