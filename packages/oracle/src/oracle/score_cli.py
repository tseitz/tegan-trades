"""``score-roster`` — grade the corpus against cached prices and rank the roster.

Joins the two halves: ``oracle`` supplies prices, ``core.grade``/``core.score`` supply the
pure logic. Reads ore, writes a report; never mutates ``data/theses/``.
"""
from __future__ import annotations

import argparse
import collections
from datetime import UTC, datetime
from pathlib import Path

from core.canon import load_registry
from core.grade import DEFAULT_HORIZONS, Grade, Horizons, Pending, Ungradeable, grade
from core.score import MIN_SAMPLE, fold_restatements, group_scores

from oracle import cache, corpus, listings
from oracle.benchmarks import BENCHMARKS, DEFAULT_DOMAIN
from oracle.route import OracleRef, load_routing_table, route

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = REPO_ROOT / "cfg"

# Multi-author feeds — attribution is feed-level in this phase, so every report must say
# so rather than let a two-person score read as one person's track record.
MULTI_AUTHOR_MARKER = "+"


def _load_series(table, asset, *, series_cache):
    """Resolve an asset to a cached PriceSeries, or None when it can't be priced."""
    if asset in series_cache:
        return series_cache[asset]
    resolved = route(asset, table)
    series = None
    if isinstance(resolved, OracleRef):
        series = cache.load(resolved.source, resolved.symbol)
    series_cache[asset] = series
    return series


def _fmt_pct(value, width=7):
    return f"{'—':>{width}}" if value is None else f"{value:>{width}.1%}"


def _fmt_ci(ci):
    return "—" if ci is None else f"[{ci[0]:+.1%}, {ci[1]:+.1%}]"


def build_outcomes(rows, table, *, today, horizons, series_cache):
    # Benchmarks are read once, not per row — there are only two of them against 3.7k calls.
    benchmarks = {
        domain: cache.load(source, symbol)
        for domain, (source, symbol) in BENCHMARKS.items()
    }
    missing = [d for d, series in benchmarks.items() if series is None]
    if missing:
        print(f"  warning: no cached benchmark for {missing} — "
              f"those calls score without a benchmark edge")
    return [
        grade(
            row,
            _load_series(table, row.asset, series_cache=series_cache),
            today=today,
            horizons=horizons,
            benchmark=benchmarks.get(row.domain, benchmarks[DEFAULT_DOMAIN]),
        )
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the roster against the price oracle.")
    parser.add_argument("--no-fold", action="store_true",
                        help="grade every restatement instead of folding repeats into one call")
    parser.add_argument("--window-days", type=int, default=30,
                        help="restatement folding window (default: 30)")
    parser.add_argument("--min-sample", type=int, default=MIN_SAMPLE)
    parser.add_argument("--by", choices=["asset", "timeframe", "direction"],
                        help="break a person's score down; use with --person")
    parser.add_argument("--person", help="restrict output to one canonical person")
    parser.add_argument("--horizon-sweep", action="store_true",
                        help="re-score at several horizon scalings to test rank stability")
    args = parser.parse_args(argv)

    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    table = load_routing_table(CONFIG_DIR, [(r.asset, r.domain) for r in rows], listings=listings_map)
    today = datetime.now(UTC).date()

    total_rows = len(rows)
    if not args.no_fold:
        rows = fold_restatements(rows, window_days=args.window_days)
    if args.person:
        rows = [r for r in rows if r.person == args.person]

    series_cache: dict = {}
    outcomes = build_outcomes(rows, table, today=today,
                              horizons=DEFAULT_HORIZONS, series_cache=series_cache)

    print(f"corpus {total_rows} theses -> {len(rows)} calls after folding "
          f"(window {args.window_days}d)" if not args.no_fold else
          f"corpus {total_rows} theses, unfolded")
    graded = [o for o in outcomes if isinstance(o, Grade)]
    pending = [o for o in outcomes if isinstance(o, Pending)]
    ungradeable = [o for o in outcomes if isinstance(o, Ungradeable)]
    print(f"  graded {len(graded)} · pending {len(pending)} · ungradeable {len(ungradeable)}")
    reasons = collections.Counter(o.reason for o in ungradeable)
    if reasons:
        print("  ungradeable: " + ", ".join(f"{k}={v}" for k, v in reasons.most_common()))

    if args.by and args.person:
        key = {"asset": lambda o: o.asset, "timeframe": lambda o: o.timeframe,
               "direction": lambda o: o.direction}[args.by]
        scores = group_scores(outcomes, key=key, min_sample=args.min_sample)
        _print_table(scores, label=args.by, min_sample=args.min_sample)
        return 0

    scores = group_scores(outcomes, min_sample=args.min_sample)
    _print_table(scores, label="person", min_sample=args.min_sample)

    if args.horizon_sweep:
        _print_sweep(rows, table, today=today, series_cache=series_cache,
                     min_sample=args.min_sample)
    return 0


def _print_table(scores, *, label, min_sample):
    ranked = sorted(
        scores.values(),
        key=lambda s: (s.benchmark_edge if s.benchmark_edge is not None else -9e9),
        reverse=True,
    )
    print(f"\n{label:<42} {'n':>5} {'hit':>7} {'null':>7} {'bench edge':>11}  {'95% CI':<20} "
          f"{'dir edge':>9} {'long%':>6}")
    print("─" * 118)
    for s in ranked:
        flag = "  ⚠ low-n" if s.insufficient_sample else ""
        name = s.person[:40] + (f" {MULTI_AUTHOR_MARKER}" if "+" in s.person else "")
        print(f"{name:<42} {s.n:>5} {_fmt_pct(s.hit_rate)} {_fmt_pct(s.null_hit_rate)} "
              f"{_fmt_pct(s.benchmark_edge, 11)}  {_fmt_ci(s.benchmark_edge_ci):<20} "
              f"{_fmt_pct(s.direction_edge, 9)} {s.long_share:>6.0%}{flag}")
    print(f"\n  bench edge = mean(call return − benchmark return over the same window). "
          f"The headline.\n"
          f"  dir edge   = mean(call return − always-long the same asset). Structurally 0 "
          f"for a long-only\n"
          f"               caller, so read it together with long%: it measures shorting, "
          f"not skill overall.\n"
          f"  ⚠ low-n    = fewer than {min_sample} graded calls; not rankable.\n"
          f"  {MULTI_AUTHOR_MARKER} = multi-author feed — the score covers the feed, not "
          f"one person.")


def _print_sweep(rows, table, *, today, series_cache, min_sample):
    """Re-score at scaled horizons. If the ordering churns, the ranking is an artifact of
    the horizon constants rather than a fact about the roster."""
    print("\nhorizon sweep (benchmark edge by horizon scaling):")
    scalings = [0.5, 1.0, 2.0]
    orderings = {}
    for factor in scalings:
        horizons = Horizons(
            scalp=max(1, int(7 * factor)), swing=max(1, int(30 * factor)),
            position=max(1, int(180 * factor)), macro=max(1, int(365 * factor)),
        )
        outcomes = build_outcomes(rows, table, today=today, horizons=horizons,
                                  series_cache=series_cache)
        scores = group_scores(outcomes, min_sample=min_sample)
        ranked = [s.person for s in sorted(
            scores.values(),
            key=lambda s: (s.benchmark_edge if s.benchmark_edge is not None else -9e9),
            reverse=True) if not s.insufficient_sample]
        orderings[factor] = ranked
        print(f"  ×{factor}: " + " > ".join(p.split(" (")[0][:18] for p in ranked[:6]))
    base = orderings[1.0]
    stable = all(orderings[f][:3] == base[:3] for f in scalings)
    print(f"  top-3 {'STABLE' if stable else 'CHURNS'} across horizons"
          + ("" if stable else " — treat the ranking as horizon-sensitive"))


if __name__ == "__main__":
    raise SystemExit(main())
