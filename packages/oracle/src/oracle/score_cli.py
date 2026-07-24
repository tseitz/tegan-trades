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
from core.stability import kendall_tau, rank_stability

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
        key=lambda s: (s.skill_edge if s.skill_edge is not None else -9e9),
        reverse=True,
    )
    print(f"\n{label:<42} {'n':>5} {'hit':>7} {'skill edge':>11}  {'95% CI':<20} "
          f"{'bench':>7} {'best stance':>12} {'long%':>6}")
    print("─" * 122)
    for s in ranked:
        flag = "  ⚠ low-n" if s.insufficient_sample else ""
        name = s.person[:40] + (f" {MULTI_AUTHOR_MARKER}" if "+" in s.person else "")
        stance = (f"{s.best_static_direction or '—'} {s.best_static_edge:+.1%}"
                  if s.best_static_edge is not None else "—")
        print(f"{name:<42} {s.n:>5} {_fmt_pct(s.hit_rate)} "
              f"{_fmt_pct(s.skill_edge, 11)}  {_fmt_ci(s.skill_edge_ci):<20} "
              f"{_fmt_pct(s.benchmark_edge)} {stance:>12} {s.long_share:>6.0%}{flag}")
    print(f"\n  skill edge  = benchmark edge − the BEST FIXED STANCE on that person's own "
          f"slate. The headline.\n"
          f"                A stance needs no judgement, so beating the market while losing "
          f"to one is\n"
          f"                regime-matching bias, not a read. Chosen in hindsight, so this "
          f"is conservative.\n"
          f"  best stance = which fixed stance that was, and what it earned. Compare it to "
          f"bench.\n"
          f"  bench       = mean(call return − benchmark return). Confounded by directional "
          f"bias; kept for context.\n"
          f"  ⚠ low-n     = fewer than {min_sample} graded calls; not rankable.\n"
          f"  {MULTI_AUTHOR_MARKER} = multi-author feed — the score covers the feed, not "
          f"one person.")


def _ordering(outcomes, min_sample):
    scores = group_scores(outcomes, min_sample=min_sample)
    ok = [s for s in scores.values() if not s.insufficient_sample and s.skill_edge is not None]
    return [s.person for s in sorted(ok, key=lambda s: -s.skill_edge)]


def _noise_floor(outcomes, *, min_sample, trials=200, seed=11):
    """How much the ranking moves when nothing changes — bootstrap-resample each person's
    own calls at a fixed horizon. Without this band, a sweep result is uninterpretable:
    you cannot tell a horizon effect from ordinary sampling noise."""
    import random

    baseline = _ordering(outcomes, min_sample)
    by_person: dict = {}
    for outcome in outcomes:
        by_person.setdefault(outcome.person, []).append(outcome)
    rng = random.Random(seed)
    taus = []
    for _ in range(trials):
        resampled = []
        for group in by_person.values():
            resampled.extend(rng.choices(group, k=len(group)))
        tau = kendall_tau(baseline, _ordering(resampled, min_sample))
        if tau is not None:
            taus.append(tau)
    if not taus:
        return baseline, None
    taus.sort()
    return baseline, (taus[int(0.05 * len(taus))], taus[int(0.95 * len(taus)) - 1])


def _print_sweep(rows, table, *, today, series_cache, min_sample):
    """Sweep horizons, but judge the result against the noise floor rather than in a vacuum."""
    base_outcomes = build_outcomes(rows, table, today=today, horizons=DEFAULT_HORIZONS,
                                   series_cache=series_cache)
    baseline, floor = _noise_floor(base_outcomes, min_sample=min_sample)

    print("\nhorizon sweep — Kendall tau of the skill-edge ranking vs the 1x baseline")
    if floor:
        print(f"  noise floor (same data, same horizons, bootstrap-resampled): "
              f"tau p05={floor[0]:+.2f} p95={floor[1]:+.2f}")
        print("  a sweep tau at or above p05 means the horizon moved the ranking no more "
              "than chance.")
    verdicts = []
    for factor in (0.5, 0.75, 1.5, 2.0):
        horizons = Horizons(
            scalp=max(1, int(7 * factor)), swing=max(1, int(30 * factor)),
            position=max(1, int(180 * factor)), macro=max(1, int(365 * factor)),
        )
        outcomes = build_outcomes(rows, table, today=today, horizons=horizons,
                                  series_cache=series_cache)
        tau = kendall_tau(baseline, _ordering(outcomes, min_sample))
        verdict = rank_stability(observed=tau, noise_floor=floor)
        verdicts.append(verdict)
        shown = f"{tau:+.2f}" if tau is not None else "  n/a"
        print(f"  x{factor:<5} tau={shown}   {verdict}")

    if verdicts and all(v in {"within-noise", "marginal"} for v in verdicts):
        print("\n  VERDICT: no horizon perturbation moves the ranking clearly beyond the")
        print("  noise floor, so the horizon constants are defensible as they stand.")
        print("  What limits this ranking is sample size, not horizon choice — the fix is")
        print("  more calls per person, not better constants.")
    else:
        print("\n  VERDICT: at least one perturbation moved the ranking clearly beyond the")
        print("  noise floor — the horizon constants are genuinely load-bearing here.")


if __name__ == "__main__":
    raise SystemExit(main())
