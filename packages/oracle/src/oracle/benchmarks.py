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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from core import held_flat
from core.transactions import InvestmentTransaction, TransactionSpan

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
    """A benchmark this module could not price — no cached series, no inputs supplied, or a
    held_flat basket that cannot be answered honestly (see `resolve`'s `held_flat` branch)."""
    benchmark: portfolios.Benchmark
    reason: str


@dataclass(frozen=True)
class HeldFlat:
    """A resolved held_flat basket, bundled with the `price_on` it was built against — so
    `report`/`since_inception_days` keep switching on one value, the same shape as a
    `PriceSeries` or a `flat_rate` float."""
    basket: held_flat.Basket
    price_on: Callable[[str, date], float | None]


def resolve(
    benchmark: portfolios.Benchmark, *,
    holdings: dict[str, float] | None = None,
    transactions: tuple[InvestmentTransaction, ...] | None = None,
    price_on: Callable[[str, date], float | None] | None = None,
    mandate_name: str | None = None,
    anchor_root: Path = ANCHOR_ROOT,
) -> PriceSeries | float | HeldFlat | Unresolved:
    """A `symbol` resolves to its cached series; `flat_rate` reads its own typed number
    straight back — no fetch, no cache, per ADR-0003 "Cash rate has no live source".

    A `held_flat` benchmark needs its inputs supplied by the caller, because this module owns
    no routing table for a mandate's holdings/transactions/prices. Its anchor — the oldest day
    the transaction feed reaches — is persisted the first time it is seen (widening the same
    `_anchor` mechanism `flat_rate` uses) and never moves afterward, even if a later, deeper
    backfill reaches further back."""
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
    if benchmark.type == "held_flat":
        return _resolve_held_flat(
            benchmark, holdings=holdings, transactions=transactions, price_on=price_on,
            mandate_name=mandate_name, anchor_root=anchor_root,
        )
    return Unresolved(benchmark, f"{benchmark.type} benchmarks are not resolved here (see #71)")


def _resolve_held_flat(
    benchmark: portfolios.Benchmark, *,
    holdings: dict[str, float] | None,
    transactions: tuple[InvestmentTransaction, ...] | None,
    price_on: Callable[[str, date], float | None] | None,
    mandate_name: str | None,
    anchor_root: Path,
) -> HeldFlat | Unresolved:
    if holdings is None or transactions is None or price_on is None or mandate_name is None:
        return Unresolved(
            benchmark, "held_flat benchmark needs holdings, transactions, price_on and "
                       "mandate_name supplied by the caller",
        )
    span = TransactionSpan.of(transactions)
    if span.oldest is None:
        return Unresolved(benchmark, "no cached transaction history")

    anchor = _anchor(f"{mandate_name}:{benchmark.type}", as_of=span.oldest, root=anchor_root)
    basket = held_flat.build(holdings, transactions, anchor=anchor)
    if basket is None:
        return Unresolved(benchmark, "held_flat basket reconstruction went negative")
    if not basket.shares:
        return Unresolved(benchmark, "held_flat basket is empty at the anchor")

    unclassified_rows = held_flat.unclassified(transactions, anchor=anchor)
    if unclassified_rows:
        return Unresolved(
            benchmark, f"{len(unclassified_rows)} unclassified transaction(s) since the anchor",
        )

    unpriced = held_flat.unpriced_at(basket, price_on, anchor)
    if unpriced:
        return Unresolved(
            benchmark,
            f"{len(unpriced)} of {len(basket.shares)} anchor holdings unpriced on the anchor day",
        )
    return HeldFlat(basket=basket, price_on=price_on)


def _load_anchors(*, root: Path = ANCHOR_ROOT) -> dict[str, str]:
    """``"mandate:benchmark_type"`` -> first-seen ISO date. Serves both flat_rate's
    since-inception anchor and held_flat's basket anchor (#71) — one mechanism, one file."""
    try:
        return json.loads(root.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _save_anchors(anchors: dict[str, str], *, root: Path = ANCHOR_ROOT) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text(json.dumps(anchors, indent=2, sort_keys=True), encoding="utf-8")


def _anchor(key: str, *, as_of: date, root: Path = ANCHOR_ROOT) -> date:
    """The first day this key was ever seen. Set once, on first sight, and never moved —
    deleting the entry is the only way to re-anchor. Keyed on ``f"{mandate_name}:{benchmark
    .type}"`` so a mandate's `flat_rate` and `held_flat` entries never share one anchor.
    ``data/benchmarks/anchors.json`` is gitignored machine state, so an old bare-mandate-name
    key from before this widening is simply never read again — no migration needed."""
    anchors = _load_anchors(root=root)
    stored = anchors.get(key)
    if stored is not None:
        return date.fromisoformat(stored)
    anchors[key] = as_of.isoformat()
    _save_anchors(anchors, root=root)
    return as_of


def since_inception_days(resolved: PriceSeries | float | HeldFlat, *, mandate_name: str,
                          as_of: date, root: Path = ANCHOR_ROOT) -> int | None:
    """Day-count for the since-inception window. A `symbol` series uses its own full cached
    span (ADR-0003: "a symbol benchmark has no such cap since its own price series runs as far
    back as the corpus does") — not the account's age, which this module has no access to. A
    `flat_rate` anchors to the first day it was ever reported on (see `_anchor`), so the very
    first call returns 0 and every call after grows from there. A `held_flat` basket's anchor
    was already derived and persisted in `resolve()` — the transaction window's floor is the
    cap ADR-0003 asks for, so there is nothing further to compute here."""
    if isinstance(resolved, PriceSeries):
        span = resolved.span
        return None if span is None else (as_of - span[0]).days
    if isinstance(resolved, HeldFlat):
        return (as_of - resolved.basket.anchor).days
    anchor = _anchor(f"{mandate_name}:flat_rate", as_of=as_of, root=root)
    return (as_of - anchor).days


def report(benchmark: portfolios.Benchmark, *, mandate_name: str, as_of: date,
           windows: Windows = DEFAULT_WINDOWS,
           anchor_root: Path = ANCHOR_ROOT,
           holdings: dict[str, float] | None = None,
           transactions: tuple[InvestmentTransaction, ...] | None = None,
           price_on: Callable[[str, date], float | None] | None = None,
           ) -> dict[str, float | None] | Unresolved:
    """Trailing % return for each of the five standard windows, ending `as_of`. Keys:
    "7d", "30d", "90d", "1y", "since_inception". A window with no data (too new a series)
    reports `None` for that key rather than raising — a missing return must never masquerade
    as a zero return, same discipline as `PriceSeries.close_on`. `mandate_name` is only used to
    key the `flat_rate`/`held_flat` anchor file; a `symbol`/unresolved benchmark ignores it.
    This is the one place in the module with a side effect — a first-ever call for a mandate's
    `flat_rate` or `held_flat` benchmark writes `anchor_root` — so tests inject
    `anchor_root=tmp_path/...` the same way `cache.py`'s tests already inject `root=tmp_path`
    for `cache.load`/`cache.save`. `holdings`/`transactions`/`price_on` are passed straight
    through to `resolve()`; a `held_flat` benchmark needs its inputs supplied by the caller,
    because this module owns no routing table."""
    resolved = resolve(
        benchmark, holdings=holdings, transactions=transactions, price_on=price_on,
        mandate_name=mandate_name, anchor_root=anchor_root,
    )
    if isinstance(resolved, Unresolved):
        return resolved

    day_counts = {
        "7d": windows.seven_day, "30d": windows.thirty_day,
        "90d": windows.ninety_day, "1y": windows.one_year,
        "since_inception": since_inception_days(
            resolved, mandate_name=mandate_name, as_of=as_of, root=anchor_root),
    }
    return {label: _return_over(resolved, days, as_of=as_of) for label, days in day_counts.items()}


def _return_over(resolved: PriceSeries | float | HeldFlat, days: int | None, *,
                  as_of: date) -> float | None:
    if days is None:
        return None
    start = as_of - timedelta(days=days)
    if isinstance(resolved, HeldFlat):
        return held_flat.return_over(resolved.basket, resolved.price_on, start, as_of)
    if isinstance(resolved, PriceSeries):
        end_close = resolved.close_on(as_of)
        start_close = resolved.close_on(start)
        if end_close is None or start_close is None or start_close == 0:
            return None
        return (end_close - start_close) / start_close
    return resolved / 100 * (days / 365)  # simple interest, prorated — no compounding claim
