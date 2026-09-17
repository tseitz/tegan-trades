"""The seam: a loaded ``TreasuryBook`` in, one ``TreasuryResult`` out.

Pure — no file or network I/O of its own. ``benchmarks.report`` is the only thing this reaches
for, and ``anchor_root`` is threaded straight through to it for the same reason
``test_benchmarks.py`` already injects it in every test: a first-ever call for a mandate's
`flat_rate` benchmark writes to that path, and a test letting that land on the real
``data/benchmarks/anchors.json`` would have a side effect on first sight.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import NamedTuple

from oracle import benchmarks
from oracle.treasury_file import TreasuryBook


class TreasuryResult(NamedTuple):
    """Everything the treasury view can say about one book, from a single ``treasury_for``
    call — bundled rather than left as separate return values so a renderer never has to
    reassemble the same arguments a second time, the same reasoning ``review.cli.ReviewResult``
    and ``compare.card.CompareResult`` already give for their own bundles. A named field can be
    added here without breaking any caller that does not read it — #73, #75 and #76 will each
    add one.

    ``total`` is **typed principal, not net worth**. It sums each row's hand-typed ``amount``
    — never re-priced, never compounded — so accrued yield since a row's ``since:`` date is
    real money that never enters this number. Any caller building a net-worth figure on top of
    this (#76) has to add that accrual back in separately; it does not live here.

    ``weighted_apy`` is ``Σ(amount × apy) / Σ(amount)`` restricted to rows that state an
    ``apy`` — ``None`` when no row does. ``apy_rows``/``apy_amount`` ride along so a renderer
    can say when the figure describes a minority of the money: 2 rows of 8 gives a true number
    about the wrong pot, the same shape `compare.card.ProtocolReadings` already solved for a
    summed metric that not every source fed.

    ``benchmark`` carries a ``benchmarks.Unresolved`` through untouched rather than dropping
    it — a benchmark that could not be priced must print as "could not be priced", never as a
    blank that reads like 0%, the same rule `compare`'s four-state ``Cell`` and
    ``series.close_on``'s ``None`` already follow.
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


def treasury_for(
    book: TreasuryBook, *, as_of: date, anchor_root: Path = benchmarks.ANCHOR_ROOT
) -> TreasuryResult:
    """The whole card for one loaded treasury book. ``book`` carries exactly one benchmark
    entry in the common case and up to a handful when a mandate declares more than one
    ``flat_rate`` — ``benchmarks.report`` resolves the first one; see its own docstring for why
    two on one mandate share one anchor.
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
    )
