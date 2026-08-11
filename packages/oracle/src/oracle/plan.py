"""Work planning for a backfill — pure, so the expensive part is inspectable up front.

``fetch-prices --dry-run`` prints exactly this plan before spending a single request.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta

from oracle.benchmarks import benchmark_refs
from oracle.route import (
    DERIVED,
    DerivedRef,
    OracleRef,
    RoutingTable,
    Unpriceable,
    route,
)

# Entry price is the close on the publish date. When that lands on a weekend or holiday
# the series must already hold the preceding bar, so windows open a little early.
DEFAULT_PAD_DAYS = 7


@dataclass(frozen=True)
class FetchJob:
    ref: OracleRef
    start: date
    end: date


def _parse(value: str | None) -> date | None:
    """Tolerant ISO parse, matching ``core.rank.parse_date`` — undated input degrades."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _legs(ref: OracleRef) -> tuple[str, ...]:
    """The symbols this asset needs on disk — its priced one, plus its traded one if they
    differ. Ordered priced-first so a dry-run plan reads in the same order as the map."""
    if ref.tradeable is None or ref.tradeable == ref.symbol:
        return (ref.symbol,)
    return (ref.symbol, ref.tradeable)


def _leg_ref(ref: OracleRef, symbol: str) -> OracleRef:
    """The job for one leg. The traded leg drops ``tradeable`` so it does not describe itself
    as a proxy for itself, and drops ``needs_validation`` because a curated proxy is named by
    hand — probing it would ask Yahoo to confirm a symbol we chose deliberately."""
    if symbol == ref.symbol:
        return ref
    return replace(ref, symbol=symbol, tradeable=None, needs_validation=False)


def plan_fetches(
    rows: Iterable,
    table: RoutingTable,
    *,
    today: date,
    pad_days: int = DEFAULT_PAD_DAYS,
    cached_spans: Mapping[tuple[str, str], tuple[date, date]] | None = None,
    floor_start: date | None = None,
) -> tuple[list[FetchJob], list[Unpriceable]]:
    """-> (jobs to run, assets that cannot be priced and why).

    One job per *asset*, not per thesis, windowed to that asset's own earliest mention —
    an asset first discussed last month needs a month of history, not two years.

    **``floor_start`` is the second requirement, and it points the other way.** The window
    above serves *grading*, which reads bars FORWARD from a publish date, so opening at the
    earliest mention is exactly right. Structure reads BACKWARD: weekly trend, dealing range
    and order blocks all need lookback predating the first time anyone named the asset. No
    series may start later than ``floor_start``, and because it is a floor rather than an
    assignment, an asset discussed before it keeps its own earlier window — both requirements
    are real and ``min`` satisfies both.

    Default None, so every caller that does not ask for lookback plans exactly what it planned
    before this existed. Measured 2026-08-10, before it did: 95 of 329 cached series held under
    90 daily bars, and those assets refuse in ``core.setups`` as ``no_dealing_range`` — a
    structure verdict standing in for "nobody fetched the history".
    """
    cached_spans = cached_spans or {}

    earliest: dict[str, date] = {}
    undated_only: set[str] = set()
    for row in rows:
        when = _parse(getattr(row, "published_at", None))
        if when is None:
            # Blank dates sort before every real ISO date; a naive min() would drag the
            # window back to the epoch. Track separately instead.
            undated_only.add(row.asset)
            continue
        current = earliest.get(row.asset)
        if current is None or when < current:
            earliest[row.asset] = when

    # A derived asset's legs inherit its date requirement, and this has to happen before any
    # job is windowed. Two reasons, and the second is the one that bites: a leg need not be a
    # corpus asset at all — nobody has to have a thesis on BTC for ``ETH/BTC`` to need BTC's
    # bars — and even when it is, its own earliest mention can be later than the ratio's, which
    # would fetch a series too short to divide. Mutating the dict the loop reads is why this is
    # a separate pass over a snapshot of the keys.
    for asset in list(earliest):
        resolved = route(asset, table)
        if not isinstance(resolved, DerivedRef):
            continue
        for leg in (resolved.numerator, resolved.denominator):
            current = earliest.get(leg)
            if current is None or earliest[asset] < current:
                earliest[leg] = earliest[asset]

    jobs: list[FetchJob] = []
    skipped: list[Unpriceable] = []
    corpus_start = min(earliest.values()) - timedelta(days=pad_days) if earliest else None
    # The floor reaches benchmarks too. A benchmark already spans the whole corpus; if the
    # corpus itself is being deepened for structure, leaving the benchmark at the old span
    # would make it the one series too short to measure the deepened ones against.
    if floor_start is not None:
        corpus_start = floor_start if corpus_start is None else min(corpus_start, floor_start)

    for asset in sorted(set(earliest) | undated_only):
        if asset not in earliest:
            skipped.append(Unpriceable(asset=asset, reason="undated", detail="no usable date"))
            continue
        resolved = route(asset, table)
        if isinstance(resolved, Unpriceable):
            skipped.append(resolved)
            continue
        # Matched on the type that has ``source``/``symbol`` rather than by excluding the ones
        # that don't. Adding ``DerivedRef`` to the ``Route`` union broke exactly this line —
        # it read ``resolved.source`` on anything that wasn't ``Unpriceable`` and raised
        # ``AttributeError``, killing the whole run rather than one asset. A positive match
        # means the next variant added to that union degrades here instead of crashing.
        if not isinstance(resolved, OracleRef):
            skipped.append(Unpriceable(
                asset=asset, reason=DERIVED,
                detail="computed from its legs, which are planned separately"))
            continue

        start = earliest[asset] - timedelta(days=pad_days)
        if floor_start is not None:
            start = min(start, floor_start)
        # A benchmark must span the whole corpus, not just its own mentions — every call
        # is measured against it, including ones made before anyone first named it.
        if (resolved.source, resolved.symbol) in benchmark_refs() and corpus_start:
            start = min(start, corpus_start)
        # An asset with a tradeable proxy needs BOTH series over the same window: `symbol` is
        # the grading basis `score-roster` reads, `tradeable` is the instrument `setups` draws
        # zones on. Planning one and not the other is a silent half-failure — the asset simply
        # lands in `assets_uncached` with no indication which leg was missing.
        for symbol in _legs(resolved):
            cached = cached_spans.get((resolved.source, symbol))
            if cached and cached[0] <= start and cached[1] >= today:
                continue
            jobs.append(FetchJob(ref=_leg_ref(resolved, symbol), start=start, end=today))

    # Benchmarks that are never mentioned as assets still need fetching.
    planned = {(j.ref.source, j.ref.symbol) for j in jobs}
    for source, symbol in sorted(benchmark_refs()):
        if (source, symbol) in planned or corpus_start is None:
            continue
        cached = cached_spans.get((source, symbol))
        if cached and cached[0] <= corpus_start and cached[1] >= today:
            continue
        jobs.append(
            FetchJob(
                ref=OracleRef(asset=f"__benchmark__{symbol}", source=source, symbol=symbol),
                start=corpus_start,
                end=today,
            )
        )

    return jobs, skipped
