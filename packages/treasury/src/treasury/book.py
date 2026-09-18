"""The seam: a loaded ``TreasuryBook`` in, one ``TreasuryResult`` out.

Pure — no file or network I/O of its own. ``benchmarks.report`` is the only thing this reaches
for directly, and ``anchor_root`` is threaded straight through to it for the same reason
``test_benchmarks.py`` already injects it in every test: a first-ever call for a mandate's
`flat_rate` benchmark writes to that path, and a test letting that land on the real
``data/benchmarks/anchors.json`` would have a side effect on first sight. ``store_read`` is
injected the same way `compare.card` and `review.altsignal` already inject it, so the Safety
reads never touch a real file either.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

from core import safety
from oracle import benchmarks
from oracle.altsignal_config import VenueEntry
from oracle.treasury_file import TreasuryBook

SOURCE_DEFILLAMA = "defillama"

# A configured venue (has a `venues:` row) that the store has never actually seen — zero
# `venue_safety_facts` readings, ever. Distinct from `safety.UNCONFIGURED` (no row at all) and
# from a real gate failure (facts were read and did not clear it) — collapsing either pair
# would tell a reader "no audit on record" about a venue nobody has looked at yet, the exact
# confident-wrong-answer trap `compare.card`'s four-state `Cell` exists to avoid.
NOT_FETCHED = "not_fetched"


@dataclass(frozen=True, slots=True)
class IdleCash:
    """Dry powder sitting in one Mandate's one account, waiting to be spent on that Mandate's
    kind of thing (`CONTEXT.md`'s "Idle cash"). One row per *account*, not per mandate file —
    `oracle.portfolios.Portfolio.cash_by_account` exists because you cannot buy in the Roth
    with Traditional money, so collapsing this would name a sum that cannot be spent anywhere.
    """
    mandate: str
    account: str
    amount: float


class ReadingsAsOf(NamedTuple):
    """The freshest and oldest ``observed_at`` behind ``TreasuryResult.advice`` — `None` for
    both when nothing has been fetched yet. `treasury` fetches nothing itself, so the APY it
    ranks on is whatever `fetch-altsignal` last wrote; a stale reading must be visible, not
    silent, the same reason `compare.card.CompareResult` carries `freshest`/`oldest` through.
    """
    freshest: datetime | None
    oldest: datetime | None


class TreasuryResult(NamedTuple):
    """Everything the treasury view can say about one book, from a single ``treasury_for``
    call — bundled rather than left as separate return values so a renderer never has to
    reassemble the same arguments a second time, the same reasoning ``review.cli.ReviewResult``
    and ``compare.card.CompareResult`` already give for their own bundles. A named field can be
    added here without breaking any caller that does not read it — #75 and #76 will each add
    one.

    ``total`` is **typed principal, not net worth**. It sums each row's hand-typed ``amount``
    — never re-priced, never compounded — so accrued yield since a row's ``since:`` date is
    real money that never enters this number. Any caller building a net-worth figure on top of
    this (#76) has to add that accrual back in separately; it does not live here. ``idle`` is a
    wholly separate field for the same reason: nothing here folds spendable cash into the
    parked total.

    ``weighted_apy`` is ``Σ(amount × apy) / Σ(amount)`` restricted to rows that state an
    ``apy`` — ``None`` when no row does. ``apy_rows``/``apy_amount`` ride along so a renderer
    can say when the figure describes a minority of the money: 2 rows of 8 gives a true number
    about the wrong pot, the same shape `compare.card.ProtocolReadings` already solved for a
    summed metric that not every source fed.

    ``benchmark`` carries a ``benchmarks.Unresolved`` through untouched rather than dropping
    it — a benchmark that could not be priced must print as "could not be priced", never as a
    blank that reads like 0%, the same rule `compare`'s four-state ``Cell`` and
    ``series.close_on``'s ``None`` already follow.

    ``safety`` is keyed on ``ParkedRow.venue`` — one entry per distinct venue string among
    ``rows``, never per row, since two rows at the same venue share one set of facts. Each
    value is a ``(GateResult, SafetyScore)`` pair, or one of two sentinels: ``safety.UNCONFIGURED``
    (no `venues:` row names this venue at all — an off-chain bank like SoFi can never clear a
    DeFi gate, and that is correct, not a bug) or ``NOT_FETCHED`` (configured, but
    `fetch-altsignal` has not run against it yet). **A parked row's own `SafetyScore` reports
    only `incidents`** — the row names a venue, never a specific pool, so `incentive_share` and
    `apy_volatility` would borrow a number from a pool this money may not actually sit in; the
    row's own hand-typed `apy` already carries the number that matters for that row.

    ``advice`` is every `stablecoin: true` pool that clears the gate, ranked by `core.safety.rank`
    — independent of whether there is any idle cash to spend on it; deciding whether that makes
    the block worth printing is `render`'s job, not this one's.
    """
    mandate: object  # oracle.portfolios.Mandate
    rows: tuple
    total: float
    weighted_apy: float | None
    apy_rows: int
    apy_amount: float
    benchmark: dict[str, float | None] | benchmarks.Unresolved
    updated: date | None
    age_days: int | None
    as_of: date
    safety: Mapping[str, tuple[safety.GateResult, safety.SafetyScore] | str] = MappingProxyType({})
    idle: tuple[IdleCash, ...] = ()
    advice: tuple[safety.RankedVenue, ...] = ()
    readings_as_of: ReadingsAsOf = ReadingsAsOf(freshest=None, oldest=None)


def treasury_for(
    book: TreasuryBook, *, as_of: date, anchor_root: Path = benchmarks.ANCHOR_ROOT,
    books: tuple = (), venues: tuple[VenueEntry, ...] = (), store_read=None,
) -> TreasuryResult:
    """The whole card for one loaded treasury book. ``book`` carries exactly one benchmark
    entry in the common case and up to a handful when a mandate declares more than one
    ``flat_rate`` — ``benchmarks.report`` resolves the first one; see its own docstring for why
    two on one mandate share one anchor.

    ``books`` is every *other* loaded ``oracle.portfolios.Portfolio`` — the idle-cash source,
    read via ``oracle.portfolios`` by the CLI, never by this module (a view may not import a
    view, ADR-0004, and `portfolios` is the sensor both `review` and `treasury` sit on top of).
    ``venues``/``store_read`` are both absent by default, in which case ``safety``/``advice``
    stay empty and ``total`` is computed exactly as it always was — #73's whole feature is
    additive.
    """
    total = sum(row.amount for row in book.rows)

    apy_rows = [row for row in book.rows if row.apy is not None]
    apy_amount = sum(row.amount for row in apy_rows)
    weighted_apy = (
        sum(row.amount * row.apy for row in apy_rows) / apy_amount if apy_amount else None
    )

    benchmark = benchmarks.report(
        book.mandate.benchmarks[0], mandate_name=book.mandate.name, as_of=as_of,
        anchor_root=anchor_root,
    )

    if store_read is None:
        row_safety = MappingProxyType({})
        advice: tuple = ()
        readings_as_of = ReadingsAsOf(freshest=None, oldest=None)
    else:
        protocol_facts, all_rows = _protocol_facts(store_read=store_read)
        candidates, pool_rows = _pool_candidates(venues, protocol_facts, store_read=store_read)
        all_rows = all_rows + pool_rows

        venues_by_name = {entry.venue: entry for entry in venues}
        row_safety = MappingProxyType(_row_safety(book.rows, venues_by_name, protocol_facts, as_of=as_of))

        stablecoin_candidates = tuple(c for c in candidates if c.stablecoin)
        advice = safety.rank(stablecoin_candidates, as_of=as_of, by_slug=protocol_facts)
        readings_as_of = _readings_as_of(all_rows)

    return TreasuryResult(
        mandate=book.mandate,
        rows=book.rows,
        total=total,
        weighted_apy=weighted_apy,
        apy_rows=len(apy_rows),
        apy_amount=apy_amount,
        benchmark=benchmark,
        updated=book.updated,
        age_days=(as_of - book.updated).days,
        as_of=as_of,
        safety=row_safety,
        idle=_idle_rows(books),
        advice=advice,
        readings_as_of=readings_as_of,
    )


def _idle_rows(books) -> tuple[IdleCash, ...]:
    rows: list[IdleCash] = []
    for portfolio in books:
        if portfolio.cash_by_account:
            for account, amount in sorted(portfolio.cash_by_account.items()):
                rows.append(IdleCash(mandate=portfolio.mandate.name, account=account, amount=amount))
        elif portfolio.cash is not None:
            rows.append(IdleCash(mandate=portfolio.mandate.name, account=portfolio.name, amount=portfolio.cash))
    return tuple(rows)


def _latest(rows):
    """The most recent of several stored rows for one key — `altsignal_store.read` returns
    rows time-ordered ascending, so the last one is the latest."""
    return rows[-1]


def _latest_by_key(rows) -> dict:
    """`_latest`, applied per key, across rows spanning more than one key."""
    latest: dict = {}
    for row in rows:
        latest[row.key] = row
    return latest


def _protocol_facts(*, store_read):
    """Every stored ``venue_safety_facts``/``venue_first_tvl`` row, gathered once — keyed on
    every slug the store has ever seen, not just the configured ones, because `core.safety.gate`
    needs a resolved parent's own facts to validate a fork's lineage."""
    facts_rows = store_read(source=SOURCE_DEFILLAMA, kind="venue_safety_facts")
    age_rows = store_read(source=SOURCE_DEFILLAMA, kind="venue_first_tvl")

    latest_facts = _latest_by_key(facts_rows)
    latest_age = {slug: row.value for slug, row in _latest_by_key(age_rows).items()}

    out: dict[str, safety.VenueFacts] = {}
    for slug, row in latest_facts.items():
        value = row.value
        first_tvl_on = date.fromisoformat(latest_age[slug]) if slug in latest_age else None
        out[slug] = safety.VenueFacts(
            slug=slug,
            audited=value.get("audited"),
            first_tvl_on=first_tvl_on,
            forked_from=tuple(value.get("forked_from") or ()),
            incidents=value.get("incidents"),
        )
    return out, list(facts_rows) + list(age_rows)


def _pool_candidates(venues: tuple[VenueEntry, ...], protocol_facts: dict, *, store_read):
    candidates: list[safety.VenueFacts] = []
    all_rows = []
    for entry in venues:
        base = protocol_facts.get(entry.llama_protocol)
        for pool_id in entry.llama_pools:
            pool_rows = store_read(source=SOURCE_DEFILLAMA, kind="venue_pool", key=pool_id)
            if not pool_rows:
                continue
            all_rows.extend(pool_rows)
            value = _latest(pool_rows).value
            candidates.append(safety.VenueFacts(
                slug=entry.llama_protocol,
                pool_id=pool_id,
                audited=base.audited if base else None,
                first_tvl_on=base.first_tvl_on if base else None,
                forked_from=base.forked_from if base else (),
                incidents=base.incidents if base else None,
                apy=value.get("apy"),
                apy_reward=value.get("apy_reward"),
                sigma=value.get("sigma"),
                count=value.get("count"),
                outlier=value.get("outlier"),
                stablecoin=value.get("stablecoin"),
            ))
    return tuple(candidates), all_rows


def _row_safety(rows, venues_by_name: dict, protocol_facts: dict, *, as_of: date) -> dict:
    out: dict[str, tuple[safety.GateResult, safety.SafetyScore] | str] = {}
    for row in rows:
        if row.venue in out:
            continue
        entry = venues_by_name.get(row.venue)
        if entry is None:
            out[row.venue] = safety.UNCONFIGURED
            continue
        facts = protocol_facts.get(entry.llama_protocol)
        if facts is None:
            out[row.venue] = NOT_FETCHED
            continue
        gate_result = safety.gate(facts, as_of=as_of, by_slug=protocol_facts)
        row_score = safety.SafetyScore(
            incentive_share=None, apy_volatility=None, observations=None,
            incidents=facts.incidents,
        )
        out[row.venue] = (gate_result, row_score)
    return out


def _readings_as_of(rows) -> ReadingsAsOf:
    timestamps = [r.observed_at for r in rows]
    if not timestamps:
        return ReadingsAsOf(freshest=None, oldest=None)
    return ReadingsAsOf(freshest=max(timestamps), oldest=min(timestamps))
