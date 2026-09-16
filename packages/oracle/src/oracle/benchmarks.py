"""What "just holding the market" means, per domain — and what a mandate's own benchmark
entries resolve to.

Lives in its own module because both halves need the same answer: ``fetch_cli`` must
guarantee the series exist over the **whole corpus span**, and ``score_cli`` reads them.

The full-span guarantee is the subtle part. A benchmark is also an ordinary asset — SPX is
discussed 204 times — so if it were fetched on the normal path its window would start at
its own first *mention*. ^GSPC was first mentioned 2024-09-25 while the corpus opens
2024-07-31, which silently stripped the benchmark off every stock/macro call in those
first two months and dropped them from the headline metric without a word.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from oracle import cache
from oracle.series import PriceSeries

if TYPE_CHECKING:
    from oracle import portfolios

DEFAULT_DOMAIN = "_default"

# One dict, one definition per series — everything else derives from this.
SERIES: dict[str, tuple[str, str]] = {
    "sp500": ("yahoo", "^GSPC"),
    "btc": ("coinbase", "BTC-USD"),
    "eth": ("coinbase", "ETH-USD"),
}

# score_cli's face, untouched in shape and values — only its values now come from SERIES
# instead of being typed a second time, so sp500/btc can never drift between the two callers.
_DOMAIN_KEY = {"crypto": "btc", DEFAULT_DOMAIN: "sp500"}
BENCHMARKS: dict[str, tuple[str, str]] = {
    domain: SERIES[key] for domain, key in _DOMAIN_KEY.items()
}


def benchmark_for(domain: str) -> tuple[str, str]:
    return BENCHMARKS.get(domain, BENCHMARKS[DEFAULT_DOMAIN])


def benchmark_refs() -> set[tuple[str, str]]:
    """The distinct (source, symbol) pairs that must be cached over the full corpus span."""
    return set(SERIES.values())


@dataclass(frozen=True)
class Windows:
    """Trailing day-counts for the five standard benchmark-reporting windows.

    Matches ADR-0003 "Reporting windows" verbatim. `since_inception` has no fixed day-count —
    see `since_inception_days()` below.
    """
    seven_day: int = 7
    thirty_day: int = 30
    ninety_day: int = 90
    one_year: int = 365


DEFAULT_WINDOWS = Windows()

# src/oracle/benchmarks.py -> src/oracle -> src -> oracle -> packages -> <repo root>
ANCHOR_ROOT = Path(__file__).resolve().parents[4] / "data" / "benchmarks" / "anchors.json"


@dataclass(frozen=True)
class Unresolved:
    """A benchmark this module could not price — no cached series, or a type it doesn't
    handle yet (`held_flat`, #71's job)."""
    benchmark: portfolios.Benchmark
    reason: str


def resolve(benchmark: portfolios.Benchmark) -> PriceSeries | float | Unresolved:
    """A `symbol` resolves to its cached series; `flat_rate` reads its own typed number
    straight back — no fetch, no cache, per ADR-0003 "Cash rate has no live source".
    `held_flat` is #71's job: this module recognizes the tag but cannot price it yet, so it
    reports `Unresolved` rather than guessing."""
    if benchmark.type == "flat_rate":
        if benchmark.rate is None:
            return Unresolved(benchmark, "flat_rate benchmark has no rate")
        return benchmark.rate
    if benchmark.type == "symbol":
        if benchmark.key not in SERIES:
            return Unresolved(benchmark, f"unknown benchmark key {benchmark.key!r}")
        source, symbol = SERIES[benchmark.key]
        series = cache.load(source, symbol)
        if series is None:
            return Unresolved(benchmark, f"no cached series for {source}:{symbol}")
        return series
    return Unresolved(benchmark, f"{benchmark.type} benchmarks are not resolved here (see #71)")


def _load_anchors(*, root: Path = ANCHOR_ROOT) -> dict[str, str]:
    """mandate name -> first-seen ISO date, for flat_rate's since-inception anchor."""
    try:
        return json.loads(root.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _save_anchors(anchors: dict[str, str], *, root: Path = ANCHOR_ROOT) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text(json.dumps(anchors, indent=2, sort_keys=True), encoding="utf-8")


def _flat_rate_anchor(mandate_name: str, *, as_of: date, root: Path = ANCHOR_ROOT) -> date:
    """The first day this mandate's flat_rate benchmark was ever reported on. Set once, on
    first sight, and never moved. Assumes at most one `flat_rate` entry per mandate — true of
    every real mandate today (SoFi, and Treasury once #72 lands); two on one mandate would
    share this one anchor, which is an accepted limit, not a silent bug."""
    anchors = _load_anchors(root=root)
    stored = anchors.get(mandate_name)
    if stored is not None:
        return date.fromisoformat(stored)
    anchors[mandate_name] = as_of.isoformat()
    _save_anchors(anchors, root=root)
    return as_of


def since_inception_days(resolved: PriceSeries | float, *, mandate_name: str,
                          as_of: date, root: Path = ANCHOR_ROOT) -> int | None:
    """Day-count for the since-inception window. A `symbol` series uses its own full cached
    span (ADR-0003: "a symbol benchmark has no such cap since its own price series runs as far
    back as the corpus does") — not the account's age, which this module has no access to. A
    `flat_rate` anchors to the first day it was ever reported on (see `_flat_rate_anchor`),
    so the very first call returns 0 and every call after grows from there."""
    if isinstance(resolved, PriceSeries):
        span = resolved.span
        return None if span is None else (as_of - span[0]).days
    anchor = _flat_rate_anchor(mandate_name, as_of=as_of, root=root)
    return (as_of - anchor).days


def report(benchmark: portfolios.Benchmark, *, mandate_name: str, as_of: date,
           windows: Windows = DEFAULT_WINDOWS,
           anchor_root: Path = ANCHOR_ROOT) -> dict[str, float | None] | Unresolved:
    """Trailing % return for each of the five standard windows, ending `as_of`. Keys:
    "7d", "30d", "90d", "1y", "since_inception". A window with no data (too new a series)
    reports `None` for that key rather than raising — a missing return must never masquerade
    as a zero return, same discipline as `PriceSeries.close_on`. `mandate_name` is only used to
    key the `flat_rate` anchor file; a `symbol`/unresolved benchmark ignores it. This is the
    one place in the module with a side effect — a first-ever call for a mandate's `flat_rate`
    benchmark writes `anchor_root` — so tests inject `anchor_root=tmp_path/...` the same way
    `cache.py`'s tests already inject `root=tmp_path` for `cache.load`/`cache.save`."""
    resolved = resolve(benchmark)
    if isinstance(resolved, Unresolved):
        return resolved

    day_counts = {
        "7d": windows.seven_day, "30d": windows.thirty_day,
        "90d": windows.ninety_day, "1y": windows.one_year,
        "since_inception": since_inception_days(
            resolved, mandate_name=mandate_name, as_of=as_of, root=anchor_root),
    }
    return {label: _return_over(resolved, days, as_of=as_of) for label, days in day_counts.items()}


def _return_over(resolved: PriceSeries | float, days: int | None, *, as_of: date) -> float | None:
    if days is None:
        return None
    start = as_of - timedelta(days=days)
    if isinstance(resolved, PriceSeries):
        end_close = resolved.close_on(as_of)
        start_close = resolved.close_on(start)
        if end_close is None or start_close is None or start_close == 0:
            return None
        return (end_close - start_close) / start_close
    return resolved / 100 * (days / 365)  # simple interest, prorated — no compounding claim
