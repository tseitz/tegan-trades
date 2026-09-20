"""Turn readings into something you can act on in thirty seconds.

``roster_text`` and ``where_text`` are public because ``digest`` reduces the same readings to
a nightly diff and has to word them identically. Two spellings of "at resistance (weekly
zone)" in two places would drift, and the terminal report and the email would slowly stop
describing the same thing.

Two blocks, and the split between them is the whole design. **The table holds every position
you own**, because a row missing from it is indistinguishable from a position you have sold —
the one mistake a portfolio review must never make. **The notes below hold only the rows
asking for a decision**, because a section that explains the HOLDs too is a section nobody
finishes reading, and the one line that wanted an answer is the line they miss.

Pure: strings and cells out, never a file or a clock read.
"""
from __future__ import annotations

from typing import NamedTuple

from core.nearby import DAILY_ZONE, GAP, RANGE_EDGE, RESISTANCE, WEEKLY_ZONE
from core.review import (
    ABOVE_RANGE,
    ADD,
    AT_RESISTANCE,
    AT_SUPPORT,
    BEARISH_ROSTER,
    BELOW_RANGE,
    BULLISH_ROSTER,
    BUY_ZONE,
    HOLD,
    MID,
    MIXED,
    NO_READ,
    NO_VIEW,
    SELL_ZONE,
    SILENT,
    TRIM,
    UNREADABLE,
    WATCH,
    Reading,
    chart_trims,
    roster_disagrees,
)
from core.structure import (
    DOWNTREND,
    DOWNTREND_FAILED_BREAKDOWN,
    RANGING,
    UPTREND,
    UPTREND_FAILED_BREAKOUT,
)

# Most urgent first. TRIM outranks ADD because it is the one that protects money you already
# have; everything below WATCH is there to be seen, not read. The six sentiment-led verdicts
# keep their relative order — that is what holds AC 5 — and the levels-led ones slot in beside
# the verdict they most resemble: a zone beside the upgrade it can become, a plain location
# beside HOLD, the two refusals last.
ORDER = (TRIM, SELL_ZONE, ADD, BUY_ZONE, WATCH, HOLD, AT_RESISTANCE, AT_SUPPORT, MID,
         ABOVE_RANGE, BELOW_RANGE, NO_VIEW, NO_READ)

# The verdicts that earn a paragraph. HOLD and NO_VIEW deliberately do not: they are the
# answer "nothing to do", and it needs a row, not an argument. The plain locations
# (AT_SUPPORT, MID, ...) stay quiet for the same reason — a levels-led mandate printing itself
# is not a call to act. BUY_ZONE and SELL_ZONE are the two levels-led calls, so they join TRIM,
# ADD and WATCH here.
LOUD = (TRIM, SELL_ZONE, ADD, BUY_ZONE, WATCH)

WHERE_LABEL = {
    AT_SUPPORT: "at support",
    AT_RESISTANCE: "at resistance",
    MID: "mid range",
    # Worded as what price did, not where it is. "Above range" alone reads like a location
    # inside a bigger structure; "broke above" says the range under it is stale.
    ABOVE_RANGE: "broke above range",
    BELOW_RANGE: "broke below range",
    UNREADABLE: "no read",
}

HEADERS = ("TICKER", "SHARES", "PRICE", "VALUE", "WT", "P&L", "P&L %", "ROSTER", "WEEKLY",
           "TREND", "")

# Which way the weekly is going, short enough for a column. A failed break keeps the word
# "failed" rather than collapsing to its parent trend: price refusing to break is the argument
# against the trend, and a reader who cannot see that would read it as confirmation.
TREND_LABEL = {
    UPTREND: "up",
    DOWNTREND: "down",
    RANGING: "range",
    UPTREND_FAILED_BREAKOUT: "up failed",
    DOWNTREND_FAILED_BREAKDOWN: "down failed",
}

# What each level is, in words. A weekly gap and a daily gap are both "gap"; the timeframe is
# printed beside it rather than baked in, so one entry covers every series a kind can come from.
KIND_LABEL = {
    WEEKLY_ZONE: "zone",
    DAILY_ZONE: "zone",
    GAP: "gap",
    RANGE_EDGE: "range edge",
}

LEVEL_HEADERS = ("TICKER", "PRICE", "SIDE", "LEVEL", "WHAT", "", "ROSTER", "")

LEVEL_GROUPS = ("standing on it", "closing in")
NOTHING_NEAR = "nothing near a level"

ALTSIGNAL_TITLE = "ALT-SIGNAL"
MACRO_LABEL = "MACRO"
NOTHING_CONFIGURED = "nothing configured yet (see cfg/altsignal.yaml)"


def render(readings, *, portfolio: str, as_of, age_days: int | None = None,
           stale: bool = False, cash: float | None = None, cash_by=None,
           mismatched=(), mandate=None, history=None, by_size: bool = False,
           yield_notes=()) -> str:
    """The whole report. ``as_of`` is passed in rather than read from a clock so a replay of
    a past date prints that date, not today's.

    ``age_days`` is how long ago the positions were written down, and it prints every run
    rather than only when it is bad. A hand-kept file is a snapshot pretending to be a feed;
    every verdict below is computed against holdings that may no longer exist, and the only
    thing standing between that and a confidently wrong answer is the reader knowing how old
    the input is.
    """
    clause = written_text(age_days)
    written = "" if clause is None else f" · {clause}"
    money = "" if cash is None else f" · {_money(cash)} cash"
    mandate_clause = "" if mandate is None else f" · {mandate.name} ({mandate.leads_with} leads)"
    head = (f"{portfolio}{mandate_clause} · {len(readings)} position(s){money} "
            f"· as of {as_of.isoformat()}{written}")
    history_line = _history_line(history, mandate)
    if not readings:
        tail = f"\n\n  {history_line}" if history_line else ""
        return f"{head}\n\n  no positions — nothing to review{tail}"

    ranked_readings = ranked(readings, by_size=by_size)
    lines = [head, ""]
    if history_line:
        # Same position as the STALE banner, and for the same reason: above the table, where
        # it is read before any verdict below it is trusted.
        lines += [f"  {history_line}", ""]
    if stale:
        # Above the table, never below it. Under the rows it reads as a footnote about
        # something else, and by then the reader has taken every verdict as fact.
        lines += [f"  {stale_banner(age_days)}", ""]
    t = totals(ranked_readings)
    mismatch = mismatch_lines(mismatched)
    if mismatch:
        lines += [f"  {line}" for line in mismatch] + [""]
    lines += _table(ranked_readings, t.market_value)

    lines += ["", *(f"  {line}" for line in totals_lines(t))]

    loud = [r for r in ranked_readings if r.verdict in LOUD]
    # 5 is the floor this column has always had — wide enough for WATCH, the longest of the
    # three original LOUD verdicts. A report with only TRIM/ADD rows and no WATCH must still
    # use 5, or AC 5 breaks the day a report happens not to have a WATCH in it. The width only
    # grows past 5 when a wider verdict (`SELL_ZONE`, `BUY_ZONE`) is actually present.
    width = max(5, max((len(r.verdict) for r in loud), default=0))
    notes = [_note(r, width) for r in loud]
    # Same order the table above them is ranked, so the two per-holding blocks never disagree
    # about which holding comes first — looked up by identity, not equality, since two distinct
    # readings can compare equal by value.
    position_of = {id(r): i for i, r in enumerate(ranked_readings)}
    yield_lines = [
        _yield_line(note, width)
        for note in sorted(yield_notes, key=lambda n: position_of[id(n.reading)])
    ]
    if notes or yield_lines:
        if notes:
            adds = sum(1 for r in ranked_readings if r.verdict == ADD)
            # Beside the decisions rather than only in the header, and reported rather than acted
            # on. What an ADD is worth is not something this file knows, so turning cash into a
            # gate would invent a position size nobody chose. Saying the number where the ADDs are
            # is enough for the one judgement it supports: whether there is room to act at all.
            if cash is not None and adds:
                lines += ["", f"  {_money(cash)} cash to fund {adds} ADD(s)"]
                # Only when the file covers more than one account, and it has to print here
                # rather than in the header. Two IRAs are one book to think about and their
                # positions genuinely sum, but their cash does not — you cannot buy in the Roth
                # with Traditional money. Beside the ADDs is the one place that distinction
                # changes what you do; anywhere else it is trivia.
                if cash_by and len(cash_by) > 1:
                    split = " · ".join(f"{name} {_money(value)}"
                                       for name, value in sorted(cash_by.items()))
                    lines.append(f"        spendable separately — {split}")
            else:
                lines.append("")
            lines += notes
        else:
            lines.append("")
        lines += yield_lines
    return "\n".join(lines)


def _history_line(history, mandate) -> str:
    """What to say about cached transaction history, or nothing at all.

    Prints only when there is something to say: a cache file exists, or the mandate declares
    ``held_flat`` and so is expected to have one eventually. Anything else — three hand-kept
    pots with no such benchmark — would gain a nightly line saying nothing, which this repo's
    own discipline refuses.
    """
    if history is not None:
        if history.oldest is None:
            return "transaction history — cached, but empty so far"
        return (f"transaction history — {history.count} cached, reaching back to "
                f"{history.oldest.isoformat()}")
    wants_history = mandate is not None and any(b.type == "held_flat" for b in mandate.benchmarks)
    if wants_history:
        return "transaction history — none cached yet, run `uv run plaid-sync`"
    return ""


def written_text(age_days: int | None) -> str | None:
    """The clause naming how old the positions are, with no leading separator — `render()`
    composes the ` · ` itself. `None` when `age_days is None`, mirroring `render()`'s own
    "print nothing" branch rather than inventing a sentence about an unknown age.

    Public alongside `roster_text`/`where_text`/`totals_lines` so a second Surface (the
    dashboard) words this identically rather than growing a second spelling.
    """
    if age_days is None:
        return None
    return f"written {'today' if age_days == 0 else f'{age_days} days ago'}"


def stale_banner(age_days: int | None) -> str:
    """The STALE sentence, unindented — `render()` re-adds its two-space indent and keeps
    deciding *whether* to print it. Public for the same reason `written_text` is."""
    return (f"STALE — these positions were written down {age_days} days ago. Anything traded "
            f"since is missing, and every verdict below is computed against holdings that may "
            f"no longer exist.")


def mismatch_lines(mismatched) -> list[str]:
    """The broker disagreeing with our own price for the same holding.

    **First thing on the page when it fires, and it should almost never fire.** A price this
    far out means the ticker probably resolved to a different instrument, in which case the
    verdict, the level and the P&L on that row are all confidently about the wrong company —
    and nothing else in the report would look wrong. `figi:` in the portfolio file is how you
    settle which one it really is.

    Unindented at its own level — the header at none, the detail rows at two spaces relative
    to it — and with no trailing blank line; `render()` re-adds its own two-space indent and
    the blank separator, the same contract `totals_lines` follows. Public for the same reason:
    the dashboard prints these verbatim rather than re-wording the "WRONG INSTRUMENT?" call.
    """
    if not mismatched:
        return []
    out = [f"WRONG INSTRUMENT? {len(mismatched)} holding(s) priced far from the broker's own "
           f"mark. Check `figi:` in the portfolio file before trusting these rows."]
    for ticker, ours, mark in mismatched:
        gap = f"{ours / mark:,.1f}x" if mark and ours / mark >= 2 else f"{(ours - mark) / mark:+.1%}"
        out.append(f"  {ticker}  ours {_money(ours)}  broker {_money(mark)}  ({gap})")
    return out


def _rank(reading: Reading) -> tuple[int, float]:
    """Urgency first, then size. Sorting the loud rows by what they are worth puts the
    decision that moves the most money at the top of the group that wants a decision."""
    urgency = ORDER.index(reading.verdict) if reading.verdict in ORDER else len(ORDER)
    return (urgency, -(reading.market_value or 0.0))


def _by_size(reading: Reading) -> float:
    """Size alone, largest first. For surveying allocation shape rather than triaging action —
    urgency plays no part."""
    return -(reading.market_value or 0.0)


def ranked(readings, *, by_size: bool = False) -> list:
    """The row order `render()` has always used, public so a second Surface (the dashboard)
    sorts identically rather than growing its own copy of `_rank`/`_by_size`."""
    return sorted(readings, key=_by_size) if by_size else sorted(readings, key=_rank)


class Cell(NamedTuple):
    """One grid cell: the exact text the terminal prints, and the number behind it when the
    column is numeric. Public alongside `row_cells` so the dashboard renders the same grid
    the terminal does rather than growing a second spelling of these five formatters.
    """
    text: str
    value: float | None = None


def row_cells(reading: Reading, *, total: float) -> tuple[Cell, ...]:
    """One row, in `HEADERS` order. `total` is the value the WT column weighs against — see
    `_weight`."""
    weight = None if reading.market_value is None or not total else reading.market_value / total
    return (
        Cell(reading.holding.ticker),
        Cell(_num(reading.holding.shares), reading.holding.shares),
        Cell(_money(reading.price), reading.price),
        Cell(_money(reading.market_value), reading.market_value),
        Cell(_weight(reading.market_value, total), weight),
        Cell(_signed(reading.pnl), reading.pnl),
        Cell(_pct(reading.pnl_pct), reading.pnl_pct),
        Cell(roster_text(reading)),
        Cell(where_text(reading)),
        Cell(trend_text(reading)),
        Cell(reading.verdict),
    )


class GridTotals(NamedTuple):
    """The account-level numbers under the table. Public alongside `totals`/`totals_lines` so
    the dashboard sums the same way the terminal's tail does, rather than a second sum that
    can drift from it.
    """
    market_value: float
    unpriced: int
    pnl: float | None
    pnl_pct: float | None
    ungraded: int
    count: int


def totals(readings) -> GridTotals:
    """`pnl`/`pnl_pct` are `None` when nothing is graded — never `0.0` — so `totals_lines` can
    tell "the account broke even" from "nothing here has a cost basis" apart."""
    market_value = sum(r.market_value for r in readings if r.market_value is not None)
    unpriced = sum(1 for r in readings if r.price is None)
    graded = [r for r in readings if r.pnl is not None]
    pnl: float | None = None
    pnl_pct: float | None = None
    if graded:
        pnl = sum(r.pnl for r in graded)
        basis = sum(abs(r.holding.cost * r.holding.shares) for r in graded)
        if basis:
            pnl_pct = pnl / basis
    return GridTotals(market_value=market_value, unpriced=unpriced, pnl=pnl, pnl_pct=pnl_pct,
                       ungraded=len(readings) - len(graded), count=len(readings))


def totals_lines(t: GridTotals) -> list[str]:
    """The terminal's own tail, unindented — `render()` re-adds its two-space indent, and the
    dashboard prints these verbatim. `[]` for an empty book, matching `_table`'s own "nothing
    to show" case.
    """
    if t.count == 0:
        return []
    line = f"total {_money(t.market_value)}"
    if t.unpriced:
        # Named rather than netted out. A total quietly missing three holdings reads as your
        # whole account, which is a worse error than a total that admits its own hole.
        line += f" (excludes {t.unpriced} with no price)"
    lines = [line]
    if t.pnl is not None:
        # A row that could be valued cannot always be graded — a wallet knows what a coin is
        # worth and never what it cost, so on a synced crypto account this line is correctly
        # absent while the total above it is complete. `is not None`, not truthy: a book that
        # nets to exactly 0.00 is still graded and still earns the line.
        share = f" ({_pct(t.pnl_pct)})" if t.pnl_pct is not None else ""
        missing = f" (excludes {t.ungraded} with no cost basis)" if t.ungraded else ""
        lines.append(f"P&L   {_signed(t.pnl)}{share}{missing}")
    return lines


def _table(readings, total: float) -> list[str]:
    rows = [[c.text for c in row_cells(r, total=total)] for r in readings]

    widths = [max(len(str(cell)) for cell in column)
              for column in zip(HEADERS, *rows, strict=True)]
    out = ["  " + "  ".join(h.ljust(w) for h, w in zip(HEADERS, widths, strict=True)).rstrip()]
    for row in rows:
        out.append("  " + "  ".join(c.ljust(w) for c, w in
                                    zip(row, widths, strict=True)).rstrip())
    return out


def roster_text(reading: Reading) -> str:
    """How the split stands, and how old it is — never the split alone. A 3-0 bearish read
    means something entirely different at eight days than at eight hundred, and a cell that
    shows only the count invites acting on the second as if it were the first.

    A ``via`` suffix marks a fold borrowed from another asset (``lean_from``) — a wrapper fund
    like ``HODL`` reading BTC's split rather than its own. Applies even to ``silent``: "silent
    via BTC" is a real and useful answer, and dropping the suffix there would make a borrowed
    fold look identical to a holding nobody covers at all.
    """
    lean = reading.roster
    via = "" if reading.lean_from is None else f" via {reading.lean_from}"
    if lean.people == 0:
        return "silent" + via
    parts = []
    if lean.bulls:
        parts.append(f"{lean.bulls} bull")
    if lean.bears:
        parts.append(f"{lean.bears} bear")
    # Computed before the undecided branch, not after it: every shape that had people speak
    # carries the age, or the cell this docstring warns about is exactly what undecided prints.
    age = "" if lean.age_days is None else f" {lean.age_days}d"
    if not parts:
        # People spoke, but nobody picked a side. Distinct from silence and it has to read
        # that way, or an asset the roster is openly undecided on looks like one it ignores.
        return f"{lean.people} undecided{age}{via}"
    return "/".join(parts) + age + via


def where_text(reading: Reading) -> str:
    label = WHERE_LABEL.get(reading.location.where, reading.location.where)
    if reading.price is None:
        return "no price"
    if reading.location.basis == "zone":
        return f"{label} (weekly zone)"
    if reading.location.position is not None:
        return f"{label} ({reading.location.position:.0%})"
    return label


def trend_text(reading: Reading) -> str:
    """Public alongside ``roster_text`` and ``where_text`` so that if ``digest`` comes to word
    a trend, it words it from here rather than growing a second spelling."""
    if reading.weekly_trend is None:
        return "—"
    return TREND_LABEL.get(reading.weekly_trend, reading.weekly_trend)


def note_text(reading: Reading) -> str:
    """Public alongside ``roster_text`` and ``where_text`` so the dashboard renders the same
    sentence beside the row rather than growing a second spelling. ``width`` stays behind in
    ``_note`` deliberately — it is a fixed-width column artifact, not part of the wording."""
    lean = reading.roster
    age = "" if lean.age_days is None else f", newest {lean.age_days}d ago"
    # A lean of SILENT covers two different rooms: nobody spoke, and people spoke without
    # picking a side. The table already tells them apart, so the paragraph has to as well —
    # "nobody" beside a date somebody's statement supplied is a contradiction on one line.
    if lean.lean == SILENT and lean.people:
        side = "undecided"
        who = f"{lean.people} {'person' if lean.people == 1 else 'people'} spoke, no side"
    else:
        side = {BULLISH_ROSTER: "bullish", BEARISH_ROSTER: "bearish",
                MIXED: "split", SILENT: "silent"}.get(lean.lean, lean.lean)
        who = ", ".join(lean.voices) if lean.voices else "nobody"
    # Matches `roster_text`'s suffix exactly, so the table and the paragraph never disagree
    # about where a wrapper fund's fold came from.
    via = "" if reading.lean_from is None else f" via {reading.lean_from}"
    # Says why a row that looks like an ADD or a TRIM came back as WATCH. Without it the
    # grid looks broken: the roster is bullish, price is at support, and the verdict is the
    # cautious one for a reason nothing on the line explains.
    thin = " [one voice — needs a second]" if lean.thin else ""
    # The mirror case: says why a row came back louder than the roster alone would explain.
    # A TRIM beside a bullish roster reads as a broken grid without it. Recomputed from the
    # same function the verdict used rather than inferred from the verdict, so a row can never
    # carry this marker for a TRIM the chart did not in fact argue for.
    chart = (" [weekly falling into resistance]"
             if chart_trims(reading.location.where, reading.weekly_trend,
                            lean.lean, lean.age_days) else "")
    # A chart-led call (BUY_ZONE/SELL_ZONE) the roster's current lean argues against. Recomputed
    # from `roster_disagrees` rather than inferred from the verdict, so this can never fire for
    # a call the roster did not actually disagree with — the same discipline `chart` above
    # already follows for `chart_trims`.
    disagrees = (" [roster disagrees]"
                if roster_disagrees(reading.verdict, lean.lean) else "")
    return (f"roster {side}{via} ({who}{age}){thin}{chart}{disagrees}; "
            f"price {where_text(reading)}"
            f"{'' if reading.weekly_trend is None else f', weekly {trend_text(reading)}'}")


def _note(reading: Reading, width: int) -> str:
    return f"  {reading.verdict:<{width}} {reading.holding.ticker} — {note_text(reading)}"


def yield_text(note) -> str:
    """Public alongside ``note_text`` so the dashboard renders the same sentence beside the row
    rather than growing a second spelling. ``width`` stays behind in ``_yield_line``
    deliberately — it is a fixed-width column artifact, not part of the wording."""
    already = ""
    if note.already:
        parts = ", ".join(f"{_num(shares)} {ticker}" for ticker, shares in note.already)
        already = f"; {parts} already wrapped"
    gate_word = "passed" if note.gate.passed else "failed"
    # The ticker is used twice — here and by `_yield_line`'s own prefix — so it is recomputed
    # rather than threaded through as a parameter.
    ticker = note.reading.holding.ticker
    apy = f"{note.apy:.2f}%" if note.apy is not None else "—"
    return (f"{note.wrapper} pays {apy} for the same {ticker} exposure "
            f"({note.protocol}, Safety gate {gate_word}){already}")


def _yield_line(note, width: int) -> str:
    """A same-asset wrapper suggested beside the holding it is about — see
    ``review.yield_note``. ``width`` is shared with ``_note`` so the two read as one column of
    per-holding sentences, and ``"YIELD"`` (5 characters) is the floor that column already has.
    """
    ticker = note.reading.holding.ticker
    return f"  {'YIELD':<{width}} {ticker} — {yield_text(note)}"


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _signed(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+,.2f}"


def _weight(value: float | None, total: float) -> str:
    """A holding's share of the book.

    **The denominator is the value total printed under the table, and cash is deliberately
    outside it.** Every weight here can therefore be checked by hand against a number already
    on the page. Folding cash in would make a column that reconciles with nothing, and it
    would move every weight on the day a deposit landed and nothing was bought.
    """
    if value is None or not total:
        return "—"
    return f"{value / total:.1%}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.1%}"


def _num(value: float) -> str:
    """Trailing zeros trimmed. A share count is 0.35 or 42.5, and padding both to a fixed
    precision makes a crypto position and an ETF position hard to tell apart at a glance."""
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def _kind_phrase(kind: str) -> str:
    """A level kind named in full, for the header line.

    ``KIND_LABEL`` deliberately gives weekly and daily zones the same word, because the rows
    print the timeframe in its own column next to it. The header has no such column, so
    without this it reads "counting zone, daily zone" — and one of those is not a thing.
    """
    if kind == WEEKLY_ZONE:
        return "weekly zone"
    if kind == DAILY_ZONE:
        return "daily zone"
    return KIND_LABEL.get(kind, kind)


def render_levels(standing, closing, suppressed: int, *, kinds=()) -> str:
    """The chart's own section: what price is at, whatever the roster is doing.

    Deliberately independent of the verdict grid above it — on a sentiment-led mandate. On a
    levels-led mandate the "standing on" group is folded into the verdict instead (ADR-0002):
    the location a holding is standing on is now the call, so repeating it here would be the
    same fact twice. ``main()`` does that fold, not this function — see ``review.cli.main``.
    "Closing in" still prints on both paths, because an approaching level is not yet a verdict.
    """
    head = levels_headline(standing, closing, kinds=kinds)
    if not standing and not closing:
        return f"{head}\n\n  {NOTHING_NEAR}"

    # Widths come from the data rows alone. A group label in a cell would pad every side below
    # it to the width of a phrase that is not a side.
    rows = [level_row(spot) for spot in (*standing, *closing)]
    widths = [max(len(cell) for cell in column)
              for column in zip(LEVEL_HEADERS, *rows, strict=True)]

    def line(cells):
        return "  " + "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()

    out = [head, "", line(LEVEL_HEADERS)]
    cursor = 0
    for label, group in zip(LEVEL_GROUPS, (standing, closing), strict=True):
        if not group:
            continue
        out.append(f"  {label}")
        out += [line(row) for row in rows[cursor:cursor + len(group)]]
        cursor += len(group)

    if suppressed:
        # Never a silent cap. A truncated list that says nothing reads as the complete picture,
        # which about a portfolio is the one thing it must not imply.
        out += ["", f"  {suppressed} more not shown — `--levels` prints every one"]
    return "\n".join(out)


def levels_headline(standing, closing, *, kinds=()) -> str:
    """The `LEVELS —` line, lifted out of `render_levels` so the dashboard prints the same
    counts without capping first. Counts are the lists it is handed — a caller that caps
    before calling gets the capped counts, which is `render_levels`'s own behaviour, not a
    bug this function needs to correct."""
    counted = ", ".join(_kind_phrase(k) for k in kinds) or "nothing"
    return (f"LEVELS — {len(standing)} standing on one · {len(closing)} closing in "
            f"· counting {counted}")


def level_row(spot) -> list[str]:
    """One `LEVEL_HEADERS`-ordered row: the exact cells `render_levels` prints for a
    `Spotlight`. Public alongside `row_cells` so the dashboard prints the terminal's `_money`
    and dies/distance rules rather than a second spelling of them."""
    level = spot.level
    band = (_money(level.bottom) if level.top == level.bottom
            else f"{_money(level.bottom)}–{_money(level.top)}")
    what = f"{level.timeframe} {KIND_LABEL.get(level.kind, level.kind)}".strip()

    if level.inside:
        # Where the level dies is only meaningful for something price is already in — it is the
        # answer to "and if this fails?". **The direction follows the side**: a resistance zone
        # is built on a swing high, so it dies when price gets ABOVE it. Printing `dies <` on
        # one of those points at the wrong half of the market.
        arrow = "<" if level.side != RESISTANCE else ">"
        note = (f"dies {arrow}{_money(level.invalidation)}"
                if level.invalidation is not None else "")
    else:
        band = _money(level.near_edge)
        note = f"{'+' if level.side == RESISTANCE else '-'}{level.distance:.1%}"

    return [
        spot.reading.holding.ticker,
        _money(spot.reading.price),
        level.side,
        band,
        what,
        note,
        roster_text(spot.reading),
        # Named, not just "+3 more": this cell sits against ROSTER, where a bare count reads
        # as more voices rather than more levels.
        f"+{spot.others} levels" if spot.others else "",
    ]


def macro_text(row) -> str:
    """One finished MACRO line — `review.altsignal.MacroRow` -> ``"<why>: <top> (+N more)"``."""
    # A Polymarket key is "<event slug>:<market slug>" — only the market half reads
    # as a line item here, the event half is already carried by `why`.
    top = ", ".join(f"{key.rsplit(':', 1)[-1]} {value:.0%}" for key, value in row.top)
    suffix = f" (+{row.others} more)" if row.others else ""
    return f"{row.why}: {top}{suffix}"


def render_altsignal(chains, macro) -> str:
    """Phase 5 alt-signal: DefiLlama confirms a holding, Kalshi/Polymarket confirm macro
    context. Independent of the verdict grid and of LEVELS above it, same reasoning as
    ``render_levels`` — this reports what an outside source says, not what to do about it."""
    if not chains and not macro:
        return f"{ALTSIGNAL_TITLE} — {NOTHING_CONFIGURED}"

    out = [ALTSIGNAL_TITLE]
    if chains:
        out.append("")
        for c in chains:
            out.append(f"  {c.reading.holding.ticker}")
            out += [f"    {line}" for line in c.lines]
    if macro:
        out.append("")
        out.append(f"  {MACRO_LABEL}")
        for row in macro:
            out.append(f"    {macro_text(row)}")
    return "\n".join(out)
