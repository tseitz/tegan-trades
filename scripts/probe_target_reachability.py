"""Is the engine's target a level price has to negotiate, or just a number someone said?

Motivation: a live LINK long carried a target of 11.70 (R:R 9.29) drawn from one author's
level bag ``[6.31, 11.7, 15.0, 20.0]``, while its own zone's post-break extreme sat at 8.907
(R:R 2.31) and a live *weekly bearish* order block spanned 9.090–10.867 directly in the path.
11.70 also sits above the weekly dealing-range high of 10.867. The engine gates the entry on
structure and then lets the target ignore structure completely.

Three questions:

1. **Obstruction** — does a *material* opposing live block sit between entry and target?
   Material means at least ``MATERIAL_R`` risk-units from entry: a bearish block a fifth of an
   R above a bullish zone is the other side of the same congestion, not a waypoint, and
   counting those reported 48 of 49 candidates blocked, which is a broken yardstick rather
   than a finding.
2. **Waypoint** — where is the nearest structural level on the trade's side? That is the
   first-partial question, and today nothing computes it.
3. **Overshoot** — when an authored target wins over the structural one, what did that cost?

Contexts here are daily+weekly and the candidates are built with ``triggers_on=False``, so
both sides of every comparison read the same rung. The live queue promotes some assets to H12,
so an individual row here can differ from what ``setups`` prints; the population still answers
the question this probe asks.

**What it measured, 2026-08-05, and what changed.** Before ``core.exits``: 31 of 49 obstructed,
12 authored targets of which 9 were the further one (median +1.73 R, worst +8.61 R — KMI
printed 10.05 against a structural 1.45), and not one candidate whose target was the first
level price meets. After: **0 of 47 obstructed**, 2 authored targets and both now the *nearer*
read (-2.04, -1.42), sources 25 opposing_zone / 15 range_bound / 5 extreme / 2 authored.

The cost is real and was paid deliberately: the live queue went 47 → 44 candidates and
``reward_risk_too_low`` rejections 66 → 105. Those are trades whose nearest genuine obstacle
sits under 1 R from entry — they did not get worse, they stopped being quoted against a level
price had no clear run to.

Section 3 puts that in §48's units: median target 4.0 daily ranges against the 7.1 that
``probe_replay.py`` still reports, p90 8.9 against 21.4. **Not the same rows** — this is
today's queue and those are recorded decisions — so it indicates rather than proves. The stop
side moved too (median 1.9 → 2.4) and nothing in this change touched stops, which is the
clearest evidence that the two populations differ and neither number should be quoted alone.

Run: ``uv run python scripts/probe_target_reachability.py``
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from statistics import median

from core.canon import load_registry
from core.exits import STRUCTURAL_KINDS
from core.setups import BEARISH, BULLISH, build_context
from core.structure import SWING_HIGH, SWING_LOW, confirmed_by, swings
from oracle import cache, corpus, listings
from oracle.resample import to_weekly
from oracle.route import DerivedRef, OracleRef, load_routing_table, route
from oracle.setups_cli import CONFIG_DIR, _load_daily, build_candidates

# How far from entry an opposing block must sit to count as an obstruction rather than as the
# far side of the entry's own congestion. In risk units, so it scales with the zone.
MATERIAL_R = 1.0

# The yardstick for section 3, taken from ``probe_replay.py`` rather than re-chosen so the two
# probes' distance numbers can be read against each other.
RANGE_LOOKBACK = 14
MIN_RANGE_BARS = 5


def obstructions(candidate, zones, *, material_r: float = MATERIAL_R) -> list:
    """Live opposing blocks whose near edge lies between entry and target, beyond the noise."""
    long_side = candidate.direction == "long"
    opposing = BEARISH if long_side else BULLISH
    risk = abs(candidate.entry - candidate.stop)
    lo, hi = sorted((candidate.entry, candidate.target))
    found = []
    for zone in zones:
        block = zone.block
        if block.kind != opposing:
            continue
        edge = block.bottom if long_side else block.top  # met first on the way up/down
        if not lo < edge < hi:
            continue
        if risk and abs(edge - candidate.entry) / risk < material_r:
            continue
        found.append((zone, edge, abs(edge - candidate.entry) / risk if risk else 0.0))
    return sorted(found, key=lambda hit: hit[2])


def waypoints(candidate, bars, as_of) -> list[float]:
    """Confirmed swing levels between entry and target — where partials would go."""
    long_side = candidate.direction == "long"
    kind = SWING_HIGH if long_side else SWING_LOW
    lo, hi = sorted((candidate.entry, candidate.target))
    found = confirmed_by(swings(bars), as_of)
    levels = sorted({round(s.price, 6) for s in found if s.kind == kind and lo < s.price < hi})
    return levels if long_side else levels[::-1]


def _geometry(candidates, contexts) -> None:
    """Stop and target distance in units of the instrument's own daily range.

    The same statistic as ``probe_replay.py`` section 5, computed on **today's queue** rather
    than on recorded decisions — and that distinction is the whole reason this lives here.
    ``probe_replay`` reads ``decisions.jsonl``, every row of which was written by whatever
    engine was running at the time, so it cannot see a change to target selection until new
    decisions accumulate. Running it to check this change measures the old one.

    Same denominator as there (median high-low of the last ``RANGE_LOOKBACK`` bars, median so
    one gap cannot set the scale) so the two numbers are directly comparable.
    """
    stops, targets = [], []
    for candidate in candidates:
        found = contexts.get(candidate.asset)
        if found is None:
            continue
        _ctx, daily = found
        prior = daily.bars[-RANGE_LOOKBACK:]
        if len(prior) < MIN_RANGE_BARS:
            continue
        scale = median(bar.high - bar.low for bar in prior)
        if not scale:
            continue
        stops.append(abs(candidate.entry - candidate.stop) / scale)
        targets.append(abs(candidate.target - candidate.entry) / scale)

    if not targets:
        return
    print("\n── geometry: how far are the two levels, in daily ranges? ──")
    print(f"  comparable with probe_replay.py section 5, which reads recorded decisions and so"
          f"\n  still reports the pre-{'exits'} numbers until new rows accumulate.")
    for name, values in (("stop  ", stops), ("target", targets)):
        values.sort()
        p10, mid, p90 = (values[int(len(values) * q)] for q in (0.10, 0.50, 0.90))
        print(f"  {name}  distance  p10 {p10:5.1f}  median {mid:5.1f}  p90 {p90:5.1f}")


def main() -> int:
    as_of = datetime.now(UTC).date()
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    candidates, _ = build_candidates(
        rows, registry, as_of=as_of, listings_map=listings_map, triggers_on=False,
    )

    table = load_routing_table(
        CONFIG_DIR, [(r.asset, r.domain) for r in rows], listings=listings_map
    )
    series_cache: dict = {}
    contexts: dict = {}
    for asset in sorted({c.asset for c in candidates}):
        resolved = route(asset, table)
        if not isinstance(resolved, OracleRef | DerivedRef):
            continue
        daily = _load_daily(resolved, table=table, series_cache=series_cache)
        if daily is None:
            continue
        ctx = build_context(daily.bars, to_weekly(daily).bars, as_of=as_of)
        if ctx is not None:
            contexts[asset] = (ctx, daily)

    print(f"as_of {as_of.isoformat()}  |  {len(candidates)} candidates")
    print(f"target_source: {Counter(c.target_source for c in candidates).most_common()}\n")

    blocked, way_counts = [], []
    for candidate in sorted(candidates, key=lambda c: -c.score):
        found = contexts.get(candidate.asset)
        if found is None:
            continue
        ctx, daily = found
        way_counts.append(len(waypoints(candidate, daily.bars, as_of)))
        hits = obstructions(candidate, ctx.zones)
        if hits:
            blocked.append((candidate, hits[0][1], hits[0][2]))

    scored = len(way_counts)
    print(f"── obstruction: {len(blocked)} of {scored} have a material opposing zone "
          f"(≥{MATERIAL_R:.0f}R from entry) between entry and target ──")
    print(f"{'asset':10} {'dir':5} {'tf':7} {'src':10} {'entry':>10} {'target':>10} "
          f"{'R:R':>6} | {'blocker':>10} {'R@block':>8}")
    for candidate, edge, rr_at_edge in blocked:
        print(f"{candidate.asset:10} {candidate.direction:5} {candidate.zone_timeframe:7} "
              f"{candidate.target_source:10} {candidate.entry:10.4f} {candidate.target:10.4f} "
              f"{candidate.reward_risk:6.2f} | {edge:10.4f} {rr_at_edge:8.2f}")

    if way_counts:
        way_counts.sort()
        none_at_all = sum(1 for n in way_counts if n == 0)
        print("\n── waypoints: swing levels between entry and target ──")
        print(f"median {way_counts[len(way_counts) // 2]}, max {max(way_counts)}, "
              f"{none_at_all} of {scored} have none (target is the first level price meets)")

    _geometry(candidates, contexts)

    authored = [c for c in candidates if c.target_source not in STRUCTURAL_KINDS]
    print(f"\n── overshoot: {len(authored)} authored targets vs the structural one ──")
    print(f"{'asset':10} {'dir':5} {'tf':7} {'src':10} {'target':>10} {'R:R':>6} "
          f"| {'struct':>10} {'R:R':>6} {'delta':>7}")
    deltas = []
    for candidate in sorted(authored, key=lambda c: -c.score):
        found = contexts.get(candidate.asset)
        if found is None:
            continue
        ctx, _daily = found
        sign = 1 if candidate.direction == "long" else -1
        family = BULLISH if sign > 0 else BEARISH
        matching = [
            z.structural_target for z in ctx.zones
            if z.structural_target is not None and z.block.kind == family
            and (z.structural_target - candidate.entry) * sign > 0
        ]
        if not matching:
            continue
        struct = min(matching) if sign > 0 else max(matching)  # nearest = conservative
        risk = abs(candidate.entry - candidate.stop)
        rr_struct = abs(struct - candidate.entry) / risk
        delta = candidate.reward_risk - rr_struct
        deltas.append(delta)
        print(f"{candidate.asset:10} {candidate.direction:5} {candidate.zone_timeframe:7} "
              f"{candidate.target_source:10} {candidate.target:10.4f} "
              f"{candidate.reward_risk:6.2f} | {struct:10.4f} {rr_struct:6.2f} {delta:+7.2f}")
    if deltas:
        deltas.sort()
        further = sum(1 for d in deltas if d > 0)
        print(f"\nauthored minus structural R:R — median {deltas[len(deltas) // 2]:+.2f}, "
              f"range {min(deltas):+.2f}..{max(deltas):+.2f}; "
              f"{further} of {len(deltas)} authored targets are the further one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
