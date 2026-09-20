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

from datetime import date, datetime
from typing import Literal

from core.review import LEVELS_LED
from pydantic import BaseModel
from review import render
from review.levels import SHOWN, cap


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


class ReviewNote(BaseModel):
    """A loud note or a yield note, detached from the terminal's fixed-width column. `label`
    is that column's own word — the verdict, or `"YIELD"` — carried even though it repeats
    `cells[10]` for a loud note: `"YIELD"` names a *kind* of note with no cell of its own, so
    the field has to exist regardless, and giving the loud note a different shape to avoid one
    repeated word would cost a branch on both ends of the wire. The ticker is deliberately
    absent — the row it lives on already names it."""
    label: str
    text: str


class ReviewRow(BaseModel):
    # Carried beside `cells[0]` — React needs a key, and a key read out of a positional cell
    # breaks silently the day a column moves.
    ticker: str
    cells: list[ReviewCell]
    # A field rather than a cell edit: #87's `test_the_grid_is_cell_by_cell_identical_to_the_
    # terminal` pins every cell's text against `render()`'s own output, so a row-level mark is
    # the only place this can live.
    unpriced: bool
    # Required, not defaulted: a pydantic default would generate `notes?: ReviewNote[]` in
    # `schema.d.ts`, the same optional `undefined` `web/src/grid/sort.ts` already carries a
    # comment about working around.
    notes: list[ReviewNote]


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


class LevelGroup(BaseModel):
    label: str              # render.LEVEL_GROUPS
    rows: list[list[str]]    # render.level_row, in render.LEVEL_HEADERS order


class LevelsSection(BaseModel):
    headline: str            # render.levels_headline over the UNCAPPED, post-fold groups
    columns: list[str]       # render.LEVEL_HEADERS
    groups: list[LevelGroup]  # empty groups omitted, as render_levels omits them
    shown: int               # review.levels.SHOWN — the per-group cap, not a TS literal
    # review.levels.cap(...)[2] at that cap — the terminal's own `suppressed`, so the
    # expander's count shares one definition with `--levels`' own tally instead of a second.
    withheld: int
    empty_note: str | None   # render.NOTHING_NEAR when both groups are empty, else None


class ReviewDocument(BaseModel):
    mandate: str
    as_of: date
    grid: ReviewGrid
    header: ReviewHeader
    levels: LevelsSection


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
            notes=([ReviewNote(label=reading.verdict, text=render.note_text(reading))]
                   if reading.verdict in render.LOUD else []),
        )
        for reading in readings
    ]
    # Walked from the notes, not the rows: `render.py`'s own `position_of` lookup is built the
    # same direction and raises a `KeyError` on a note whose reading is not in `readings`. Keying
    # this map the other way around would drop that same orphan silently instead — the browser
    # going quiet on data the terminal crashes on.
    row_by_reading_id = {id(reading): row for reading, row in zip(readings, rows, strict=True)}
    for note in result.yield_notes:
        row_by_reading_id[id(note.reading)].notes.append(
            ReviewNote(label="YIELD", text=render.yield_text(note))
        )
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
    return ReviewDocument(mandate=book.name, as_of=as_of, grid=grid, header=header,
                          levels=levels_section(result))


def levels_section(result) -> LevelsSection:
    """`ReviewResult.levels`'s uncapped triple -> the browser's Levels section.

    The headline counts everything the browser can reach, not a capped list — the terminal
    caps before rendering (`review.cli.main`), so its head undercounts on a Mandate the browser
    can fully expand. Sending the capped head would need the server to know the browser's cap,
    which inverts the rule that capping is the Surface's decision (`ReviewResult.levels`'s own
    docstring).

    The levels-led fold happens here, before anything is counted, and the expander does not
    undo it: on a levels-led mandate the verdict grid already carries the standing group
    (ADR-0002), so repeating it under an expander would be the same fact twice. `withheld` is
    computed over these same post-fold groups with `review.levels.cap`, so it counts the
    closing group's overflow only on a levels-led mandate — never the folded standing group.
    """
    standing, closing, _ = result.levels
    if result.book.mandate.leads_with == LEVELS_LED:
        standing = ()
    _, _, withheld = cap(standing, closing, limit=SHOWN)
    groups = [
        LevelGroup(label=label, rows=[render.level_row(spot) for spot in group])
        for label, group in zip(render.LEVEL_GROUPS, (standing, closing), strict=True)
        if group
    ]
    return LevelsSection(
        headline=render.levels_headline(standing, closing, kinds=result.book.level_kinds),
        columns=list(render.LEVEL_HEADERS),
        groups=groups,
        shown=SHOWN,
        withheld=withheld,
        empty_note=render.NOTHING_NEAR if not groups else None,
    )


# ── refresh (#93) ────────────────────────────────────────────────────────────

RefreshStepState = Literal["pending", "running", "succeeded", "failed"]
RefreshJobState = Literal["running", "succeeded", "failed"]


class RefreshStepStatus(BaseModel):
    name: str
    state: RefreshStepState
    detail: str | None = None


class RefreshJobStatus(BaseModel):
    id: str
    state: RefreshJobState
    steps: list[RefreshStepStatus]
    started: datetime
    finished: datetime | None = None


def refresh_job_status(record) -> RefreshJobStatus:
    """`RefreshJobs`' internal `JobRecord` -> the browser's poll response. `record` is untyped
    for the same reason `summarise`'s `book` is: importing `dashboard.refresh`'s dataclasses
    here would be exactly the coupling ADR-0011 avoids for `ReviewResult`."""
    return RefreshJobStatus(
        id=record.id,
        state=record.state,
        steps=[RefreshStepStatus(name=s.name, state=s.state, detail=s.detail)
              for s in record.steps],
        started=record.started,
        finished=record.finished,
    )
