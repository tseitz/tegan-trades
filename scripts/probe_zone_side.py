"""Which side of its zone is price actually on, across the live candidate set?

``core.setups.proximity_to`` measures ``abs(price - block.near_edge)`` — unsigned — and no
gate anywhere checks that price is on the *approach* side of the zone. (``wrong_side_of_range``
is about the dealing range, a different object; zones die only when ``invalidation`` is closed
through, which for a bullish block sits *below* it.) So a bullish block price has fallen
straight through can still be live, scored, and offered as a long entry above spot.

This counts how often that actually happens, and how much of the candidate set sits on the
flat ``proximity == 1.0`` shelf where the term stops discriminating at all. Reads the price
cache only — no network beyond the listings file the CLI already caches, no LLM, no cost.

    uv run python scripts/probe_zone_side.py
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from core.canon import load_registry
from core.setups import ARRIVAL, BULLISH, _DIRECTION_FAMILY
from oracle import cache, corpus, listings
from oracle.setups_cli import CONFIG_DIR, build_candidates

# Where price sits relative to the zone it would be entered on.
APPROACH = "approach"   # correct side, hasn't arrived — bullish: above; bearish: below
INSIDE = "inside"       # in the zone; proximity is pinned at 1.0 here
THROUGH = "through"     # wrong side: price has traded clean past the zone


def side_of(candidate) -> str:
    price, top, bottom = candidate.price, candidate.entry_top, candidate.entry_bottom
    if bottom <= price <= top:
        return INSIDE
    bullish = _DIRECTION_FAMILY.get(candidate.direction) == BULLISH
    above = price > top
    return APPROACH if above == bullish else THROUGH


def main() -> int:
    as_of = datetime.now(UTC).date()
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    candidates, stats = build_candidates(
        rows, registry, as_of=as_of, listings_map=listings_map
    )

    print(f"as_of {as_of} — {stats.candidate_count} candidates "
          f"({stats.assets_priced}/{stats.assets_total} assets priced)\n")

    sides = Counter(side_of(c) for c in candidates)
    total = len(candidates) or 1
    print("price vs. its own zone")
    for label in (APPROACH, INSIDE, THROUGH):
        n = sides[label]
        print(f"  {label:9} {n:4}  {n / total:5.1%}")

    arrived = [c for c in candidates if c.approach >= ARRIVAL]
    print(f"\nprice in the zone: {len(arrived)}/{total} ({len(arrived) / total:.1%})"
          "  — under v2 these all reported proximity 1.00, so the recorded field could not"
          "\n  separate them; approach keeps varying across the traverse")

    print("\nby zone timeframe")
    for tf in sorted({c.zone_timeframe for c in candidates}):
        sub = [c for c in candidates if c.zone_timeframe == tf]
        by = Counter(side_of(c) for c in sub)
        print(f"  {tf:7} n={len(sub):3}  " + "  ".join(
            f"{lbl}={by[lbl]}" for lbl in (APPROACH, INSIDE, THROUGH)))

    through = sorted((c for c in candidates if side_of(c) == THROUGH),
                     key=lambda c: -c.score)
    if through:
        print(f"\nthe {len(through)} on the wrong side, by score — entry is on the far "
              "side of spot, so these are not retests")
        for c in through[:15]:
            gap = (c.entry - c.price) / c.price
            print(f"  {c.asset:8} {c.direction:5} {c.zone_timeframe:6} "
                  f"price={c.price:<12.4g} entry={c.entry:<12.4g} ({gap:+.1%}) "
                  f"approach={c.approach:.2f} "
                  f"fresh={c.freshness:.2f} rr={c.reward_risk:.2f} score={c.score:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
