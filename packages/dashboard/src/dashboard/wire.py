"""The dashboard's own response shapes — never a View's result object.

ADR-0011: the API never serializes ``ReviewResult`` or ``Portfolio`` directly. This module is
where that translation lives, so a View's internal shape stays free to change (ADR-0005)
without becoming a browser contract. `core.review.Reading` in particular exposes `market_value`,
`pnl` and `pnl_pct` as computed properties, not fields — a reflexive `.model_validate(book)`
would silently drop exactly the three numbers a review screen is largely made of. `ReviewCell`
carries both a cell's text and its number for the same reason: `render.Cell` is a `NamedTuple`
from the View, and it is translated field by field rather than handed to the browser directly.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel
from review import render


class MandateSummary(BaseModel):
    name: str


class MandateList(BaseModel):
    mandates: list[MandateSummary]


def summarise(book) -> MandateSummary:
    """``book.name`` — the pot's name, not ``book.mandate.name`` (the policy's). See the
    scout's collision row: `Portfolio.name` and `Portfolio.mandate.name` are different things
    that happen to share a type. Untyped like `review.cli.load_books`'s own return: naming
    `oracle.portfolios.Portfolio` here is exactly the import `test_boundaries.py` forbids.
    """
    return MandateSummary(name=book.name)


class ReviewCell(BaseModel):
    """One grid cell. `value` is `null` for the four text columns and for a number the
    Reading could not compute — never `0`, which would read as a real answer."""
    text: str
    value: float | None = None


class ReviewRow(BaseModel):
    # Carried beside `cells[0]` — React needs a key, and a key read out of a positional cell
    # breaks silently the day a column moves.
    ticker: str
    cells: list[ReviewCell]
    # A field rather than a cell edit: #87's `test_the_grid_is_cell_by_cell_identical_to_the_
    # terminal` pins every cell's text against `render()`'s own output, so a row-level mark is
    # the only place this can live.
    unpriced: bool


class ReviewHeader(BaseModel):
    written: str | None       # render.written_text; None when the book carries no `updated:`
    stale_banner: str | None  # render.stale_banner when book.is_stale, else None
    prices: str                # the Freshness message, verbatim — already ends " — STALE"
    mismatches: list[str]      # render.mismatch_lines


class ReviewTotals(BaseModel):
    market_value: float
    unpriced: int
    # The terminal's own tail, unindented, so the browser prints the total and the P&L line
    # without owning their wording. No `pnl`/`pnl_pct`/`ungraded`: nothing in #87–#92 reads
    # them, and an unread field in a generated browser contract is a thing to keep true for
    # nobody.
    lines: list[str]


class ReviewGrid(BaseModel):
    columns: list[str]
    rows: list[ReviewRow]
    totals: ReviewTotals


class ReviewDocument(BaseModel):
    mandate: str
    as_of: date
    grid: ReviewGrid
    header: ReviewHeader


def review_document(result, *, as_of: date, freshness) -> ReviewDocument:
    """`ReviewResult` (untyped, exactly as `summarise` takes a `book`) -> the browser's grid.

    Ranks first and totals over the ranked list, then passes that total as every row's `total`
    — the same order `render()` uses, so the WT denominator and the printed totals are
    bit-for-bit the terminal's.

    ``result.book.name``, never a caller-supplied name: the path a request came in on is
    untrusted input, and under a test's dependency override it is not what produced the
    result.

    ``freshness`` is untyped for the same reason `review.cli.price_freshness`'s return is:
    naming `oracle.freshness.Freshness` here is the import `test_boundaries.FORBIDDEN` blocks.
    Only `.message` is read off it.
    """
    readings = render.ranked(result.readings)
    t = render.totals(readings)
    rows = [
        ReviewRow(
            ticker=reading.holding.ticker,
            cells=[ReviewCell(text=c.text, value=c.value)
                   for c in render.row_cells(reading, total=t.market_value)],
            unpriced=reading.price is None,
        )
        for reading in readings
    ]
    grid = ReviewGrid(
        columns=list(render.HEADERS),
        rows=rows,
        totals=ReviewTotals(market_value=t.market_value, unpriced=t.unpriced,
                            lines=render.totals_lines(t)),
    )
    book = result.book
    header = ReviewHeader(
        written=render.written_text(book.age_days(on=as_of)),
        stale_banner=(render.stale_banner(book.age_days(on=as_of))
                     if book.is_stale(on=as_of) else None),
        prices=freshness.message,
        mismatches=render.mismatch_lines(result.mismatched),
    )
    return ReviewDocument(mandate=book.name, as_of=as_of, grid=grid, header=header)
