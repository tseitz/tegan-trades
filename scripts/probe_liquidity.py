"""How many liquidity pools would each live candidate's ladder gain, and at what tolerance?

``core.liquidity.pools`` clusters untaken swings within an absolute tolerance, which the caller
scales from ATR. This probe sweeps that multiple over the live queue and reports, per series
(weekly, and the setup rung — H12 or daily, chosen exactly as ``setups`` chooses it):

- pools between entry and target at least ``MATERIAL_R`` out: the rungs the ladder would gain;
- their touch counts;
- whether an untaken pool exists past the range boundary (the single runner);
- sweep entries whose target would move to a nearer pool, which changes the placed order;
- pools within tolerance of an existing rung (dropped as near-duplicates);
- pools sitting exactly on an ``extreme`` rung (kept as ``extreme``).

MEASURED 2026-10-16, 86 candidates (58 daily rung, 28 H12), ladders today median 3 / p90 8:
- **Weekly pools never land inside the range** (median 0 at every multiple): the bar that made
  the weekly range high wicked through every lower weekly high before it. 80 of 86 weekly pools
  sit within tolerance of an existing rung, i.e. they are the boundary itself.
- **Setup-series pools are the new partials:** median 2 / p90 5 at 0.1 × ATR, 1 / 4 at 0.25,
  1 / 3 at 0.5; H12 runs denser (p90 7 / 5 / 4). Singles dominate — 2+-touch pools are 12-20%.
- **No sweep entry's target moves** at any multiple, so no placed order changes.
- **A runner past the boundary exists** for 67 of 86 from weekly pools (median 5.2 R) and 58 from
  the setup series (4.0 R).

- **Wired in, same date and flags** (``setups --list --no-triggers --no-sync --limit 0``): all 63
  targets, every score and the rejection tally are unchanged; ladder rungs 211 -> 350 (89 pool
  partials, a pool runner on 50 of 63 cards); authored rungs 2 -> 2. ``setups`` CPU 2.7 -> 3.8 s.

Reads the price cache only. The hourly cache is read as-is: the trigger pass's fetch is replaced
with a cache read, so this never reaches the network and never writes the cache.

    uv run python scripts/probe_liquidity.py
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import median

from core.canon import load_registry
from core.exits import EXTERNAL, EXTREME, MATERIAL_R, RANGE_BOUND
from core.imbalance import atr
from core.liquidity import pools
from core.structure import SWING_HIGH, SWING_LOW, on_or_before
from oracle import assemble, cache, corpus, listings, trigger_feed
from oracle.assemble import CONFIG_DIR, build_candidates, load_daily
from oracle.resample import to_weekly
from oracle.route import DerivedRef, OracleRef, load_routing_table, route

MULTIPLES = (0.05, 0.1, 0.25, 0.5)
WEEKLY, SETUP = "weekly", "setup"


def _series(candidates, rows, listings_map):
    """``{asset: {WEEKLY: bars, SETUP: bars, "rung": name}}``, built the way ``build_candidates`` does."""
    table = load_routing_table(CONFIG_DIR, [(r.asset, r.domain) for r in rows],
                               listings=listings_map)
    found, series_cache = {}, {}
    for asset in sorted({c.asset for c in candidates}):
        resolved = route(asset, table)
        if not isinstance(resolved, OracleRef | DerivedRef):
            continue
        daily = load_daily(resolved, table=table, series_cache=series_cache)
        if daily is None:
            continue
        rung, rung_series = trigger_feed.setup_rung(trigger_feed.load_cached(resolved))
        found[asset] = {
            WEEKLY: to_weekly(daily).bars,
            SETUP: daily.bars if rung_series is None else rung_series.bars,
            "rung": rung,
        }
    return found


def _rungs(candidate):
    return ((candidate.target, candidate.target_source),
            *((level.price, level.kind) for level in candidate.ladder))


def _measure(candidate, bars, as_of, multiple):
    """One candidate, one series, one tolerance multiple. None when ATR is not yet defined."""
    upto = [b for b in bars if on_or_before(b.date, as_of)]
    yardstick = atr(upto, len(upto) - 1) if upto else None
    if yardstick is None:
        return None
    tolerance = multiple * yardstick
    sign = 1 if candidate.direction == "long" else -1
    side = SWING_HIGH if sign > 0 else SWING_LOW
    risk = abs(candidate.entry - candidate.stop)
    out = lambda p: (p - candidate.entry) * sign  # noqa: E731
    found = [p for p in pools(bars, as_of=as_of, tolerance=tolerance)
             if p.kind == side and out(p.price) >= MATERIAL_R * risk]

    rungs = _rungs(candidate)
    bound = next((price for price, kind in rungs if kind == RANGE_BOUND), None)
    inside = [p for p in found if out(p.price) < out(candidate.target)]
    past = sorted((p for p in found if bound is not None and out(p.price) > out(bound)),
                  key=lambda p: out(p.price))
    duplicate = [p for p in found if any(abs(p.price - price) <= tolerance for price, _ in rungs)]
    extreme = [p for p in found if any(p.price == price for price, kind in rungs if kind == EXTREME)]
    fresh = [p for p in inside if p not in duplicate]
    return {
        "inside": len(fresh),
        "touches": [p.touches for p in fresh],
        "runner": (out(past[0].price) / risk) if past else None,
        "has_bound": bound is not None,
        "moves": candidate.entry_liquidity == EXTERNAL and bool(fresh),
        "duplicate": len(duplicate),
        "extreme": len(extreme),
    }


def _pct(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0


def main() -> int:
    as_of = datetime.now(UTC).date()
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    # The live rung choice, with the trigger pass's network fetch swapped for a cache read.
    assemble.trigger_feed.load_or_fetch = lambda ref, **_: trigger_feed.load_cached(ref)
    candidates, _ = build_candidates(rows, registry, as_of=as_of, listings_map=listings_map,
                                     marks_index={})
    series = _series(candidates, rows, listings_map)
    rungs = Counter(series[c.asset]["rung"] for c in candidates if c.asset in series)
    print(f"as_of {as_of.isoformat()}  |  {len(candidates)} candidates  |  setup rungs {dict(rungs)}")
    print(f"current ladder length: median {median(len(c.ladder) for c in candidates):.0f}, "
          f"p90 {_pct([len(c.ladder) for c in candidates], 0.9)}\n")

    for multiple in MULTIPLES:
        print(f"── tolerance {multiple:.2f} × ATR ──")
        combined: dict[str, int] = defaultdict(int)
        for name in (WEEKLY, SETUP):
            results, no_atr = [], 0
            by_rung: dict[str, list[int]] = defaultdict(list)
            for candidate in candidates:
                bars = series.get(candidate.asset)
                if bars is None:
                    continue
                measured = _measure(candidate, bars[name], as_of, multiple)
                if measured is None:
                    no_atr += 1
                    continue
                results.append(measured)
                combined[candidate.key] += measured["inside"]
                by_rung[bars["rung"]].append(measured["inside"])
            inside = [r["inside"] for r in results]
            touches = Counter(t for r in results for t in r["touches"])
            bounded = [r for r in results if r["has_bound"]]
            runners = [r["runner"] for r in bounded if r["runner"] is not None]
            rung_line = ", ".join(f"{k} median {median(v):.0f} p90 {_pct(v, 0.9)}"
                                  for k, v in sorted(by_rung.items()))
            print(f"  {name:6}  new partials median {median(inside) if inside else 0:.0f} "
                  f"p90 {_pct(inside, 0.9)} max {max(inside, default=0)}  ({rung_line})")
            print(f"          touches {dict(sorted(touches.items()))}  |  no ATR {no_atr}")
            print(f"          runner past boundary {len(runners)}/{len(bounded)}"
                  + (f", median {median(runners):.2f}R" if runners else ""))
            print(f"          target moves (sweep) {sum(r['moves'] for r in results)}  |  "
                  f"near-duplicate {sum(r['duplicate'] for r in results)}  |  "
                  f"on an extreme {sum(r['extreme'] for r in results)}")
        both = list(combined.values())
        print(f"  both    new partials median {median(both) if both else 0:.0f} "
              f"p90 {_pct(both, 0.9)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
