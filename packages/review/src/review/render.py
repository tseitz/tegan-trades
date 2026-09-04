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

Pure: strings in, one string out. Nothing here reads a file or a clock.
"""
from __future__ import annotations

from core.nearby import DAILY_ZONE, GAP, RANGE_EDGE, RESISTANCE, WEEKLY_ZONE
from core.review import (
    ABOVE_RANGE,
    ADD,
    AT_RESISTANCE,
    AT_SUPPORT,
    BEARISH_ROSTER,
    BELOW_RANGE,
    BULLISH_ROSTER,
    HOLD,
    MID,
    MIXED,
    NO_READ,
    NO_VIEW,
    SILENT,
    TRIM,
    UNREADABLE,
    WATCH,
    Reading,
    chart_trims,
)
from core.structure import (
    DOWNTREND,
    DOWNTREND_FAILED_BREAKDOWN,
    RANGING,
    UPTREND,
    UPTREND_FAILED_BREAKOUT,
)

# Most urgent first. TRIM outranks ADD because it is the one that protects money you already
# have; everything below WATCH is there to be seen, not read.
ORDER = (TRIM, ADD, WATCH, HOLD, NO_VIEW, NO_READ)

# The verdicts that earn a paragraph. HOLD and NO_VIEW deliberately do not: they are the
# answer "nothing to do", and it needs a row, not an argument.
LOUD = (TRIM, ADD, WATCH)

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


def render(readings, *, portfolio: str, as_of, age_days: int | None = None,
           stale: bool = False, cash: float | None = None, cash_by=None,
           mismatched=()) -> str:
    """The whole report. ``as_of`` is passed in rather than read from a clock so a replay of
    a past date prints that date, not today's.

    ``age_days`` is how long ago the positions were written down, and it prints every run
    rather than only when it is bad. A hand-kept file is a snapshot pretending to be a feed;
    every verdict below is computed against holdings that may no longer exist, and the only
    thing standing between that and a confidently wrong answer is the reader knowing how old
    the input is.
    """
    written = ""
    if age_days is not None:
        written = f" · written {'today' if age_days == 0 else f'{age_days} days ago'}"
    money = "" if cash is None else f" · {_money(cash)} cash"
    head = (f"{portfolio} · {len(readings)} position(s){money} "
            f"· as of {as_of.isoformat()}{written}")
    if not readings:
        return f"{head}\n\n  no positions — nothing to review"

    ranked = sorted(readings, key=_rank)
    lines = [head, ""]
    if stale:
        # Above the table, never below it. Under the rows it reads as a footnote about
        # something else, and by then the reader has taken every verdict as fact.
        lines += [f"  STALE — these positions were written down {age_days} days ago. "
                  f"Anything traded since is missing, and every verdict below is computed "
                  f"against holdings that may no longer exist.", ""]
    total = sum(r.market_value for r in ranked if r.market_value is not None)
    lines += _mismatch_block(mismatched)
    lines += _table(ranked, total)

    unpriced = [r for r in ranked if r.price is None]
    tail = f"  total {_money(total)}"
    if unpriced:
        # Named rather than netted out. A total quietly missing three holdings reads as your
        # whole account, which is a worse error than a total that admits its own hole.
        tail += f" (excludes {len(unpriced)} with no price)"
    lines += ["", tail, *_pnl_tail(ranked)]

    notes = [_note(r) for r in ranked if r.verdict in LOUD]
    if notes:
        adds = sum(1 for r in ranked if r.verdict == ADD)
        # Beside the decisions rather than only in the header, and reported rather than acted
        # on. What an ADD is worth is not something this file knows, so turning cash into a
        # gate would invent a position size nobody chose. Saying the number where the ADDs are
        # is enough for the one judgement it supports: whether there is room to act at all.
        if cash is not None and adds:
            lines += ["", f"  {_money(cash)} cash to fund {adds} ADD(s)"]
            # Only when the file covers more than one account, and it has to print here rather
            # than in the header. Two IRAs are one book to think about and their positions
            # genuinely sum, but their cash does not — you cannot buy in the Roth with
            # Traditional money. Beside the ADDs is the one place that distinction changes what
            # you do; anywhere else it is trivia.
            if cash_by and len(cash_by) > 1:
                split = " · ".join(f"{name} {_money(value)}"
                                   for name, value in sorted(cash_by.items()))
                lines.append(f"        spendable separately — {split}")
        else:
            lines.append("")
        lines += notes
    return "\n".join(lines)


def _pnl_tail(readings) -> list[str]:
    """The account's profit and loss under the value total, or nothing at all.

    A separate line rather than more words on the total: they answer different questions, and
    a row that could be valued cannot always be graded — a wallet knows what a coin is worth
    and never what it cost, so on a synced crypto account this line is correctly absent while
    the total above it is complete.
    """
    graded = [r for r in readings if r.pnl is not None]
    if not graded:
        return []
    gain = sum(r.pnl for r in graded)
    basis = sum(abs(r.holding.cost * r.holding.shares) for r in graded)
    share = f" ({_pct(gain / basis)})" if basis else ""
    ungraded = len(readings) - len(graded)
    missing = f" (excludes {ungraded} with no cost basis)" if ungraded else ""
    return [f"  P&L   {_signed(gain)}{share}{missing}"]


def _mismatch_block(mismatched) -> list[str]:
    """The broker disagreeing with our own price for the same holding.

    **First thing on the page when it fires, and it should almost never fire.** A price this
    far out means the ticker probably resolved to a different instrument, in which case the
    verdict, the level and the P&L on that row are all confidently about the wrong company —
    and nothing else in the report would look wrong. `figi:` in the portfolio file is how you
    settle which one it really is.
    """
    if not mismatched:
        return []
    out = [f"  WRONG INSTRUMENT? {len(mismatched)} holding(s) priced far from the broker's own "
           f"mark. Check `figi:` in the portfolio file before trusting these rows."]
    for ticker, ours, mark in mismatched:
        gap = f"{ours / mark:,.1f}x" if mark and ours / mark >= 2 else f"{(ours - mark) / mark:+.1%}"
        out.append(f"    {ticker}  ours {_money(ours)}  broker {_money(mark)}  ({gap})")
    return [*out, ""]


def _rank(reading: Reading) -> tuple[int, float]:
    """Urgency first, then size. Sorting the loud rows by what they are worth puts the
    decision that moves the most money at the top of the group that wants a decision."""
    urgency = ORDER.index(reading.verdict) if reading.verdict in ORDER else len(ORDER)
    return (urgency, -(reading.market_value or 0.0))


def _table(readings, total: float) -> list[str]:
    rows = [[
        r.holding.ticker,
        _num(r.holding.shares),
        _money(r.price),
        _money(r.market_value),
        _weight(r.market_value, total),
        _signed(r.pnl),
        _pct(r.pnl_pct),
        roster_text(r),
        where_text(r),
        trend_text(r),
        r.verdict,
    ] for r in readings]

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
    shows only the count invites acting on the second as if it were the first."""
    lean = reading.roster
    if lean.people == 0:
        return "silent"
    parts = []
    if lean.bulls:
        parts.append(f"{lean.bulls} bull")
    if lean.bears:
        parts.append(f"{lean.bears} bear")
    if not parts:
        # People spoke, but nobody picked a side. Distinct from silence and it has to read
        # that way, or an asset the roster is openly undecided on looks like one it ignores.
        return f"{lean.people} undecided"
    age = "" if lean.age_days is None else f" {lean.age_days}d"
    return "/".join(parts) + age


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


def _note(reading: Reading) -> str:
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
    return (f"  {reading.verdict:<5} {reading.holding.ticker} — roster {side} "
            f"({who}{age}){thin}{chart}; price {where_text(reading)}"
            f"{'' if reading.weekly_trend is None else f', weekly {reading.weekly_trend}'}")


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

    Deliberately independent of the verdict grid above it. On the live account 30 holdings sat
    on a weekly level and 14 of those had a silent roster — the grid correctly refuses to
    advise there, and refusing to *report* it as well threw away the most concrete thing the
    pipeline knew about them.
    """
    counted = ", ".join(_kind_phrase(k) for k in kinds) or "nothing"
    head = (f"LEVELS — {len(standing)} standing on one · {len(closing)} closing in "
            f"· counting {counted}")
    if not standing and not closing:
        return f"{head}\n\n  nothing near a level"

    # Widths come from the data rows alone. A group label in a cell would pad every side below
    # it to the width of a phrase that is not a side.
    rows = [_level_row(spot) for spot in (*standing, *closing)]
    widths = [max(len(cell) for cell in column)
              for column in zip(LEVEL_HEADERS, *rows, strict=True)]

    def line(cells):
        return "  " + "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()

    out = [head, "", line(LEVEL_HEADERS)]
    cursor = 0
    for label, group in (("standing on it", standing), ("closing in", closing)):
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


def _level_row(spot) -> list[str]:
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
        f"+{spot.others} more" if spot.others else "",
    ]


def render_altsignal(chains, macro) -> str:
    """Phase 5 alt-signal: DefiLlama confirms a holding, Kalshi/Polymarket confirm macro
    context. Independent of the verdict grid and of LEVELS above it, same reasoning as
    ``render_levels`` — this reports what an outside source says, not what to do about it."""
    if not chains and not macro:
        return "ALT-SIGNAL — nothing configured yet (see cfg/altsignal.yaml)"

    out = ["ALT-SIGNAL"]
    if chains:
        out.append("")
        for c in chains:
            out.append(f"  {c.reading.holding.ticker}")
            out += [f"    {line}" for line in c.lines]
    if macro:
        out.append("")
        out.append("  MACRO")
        for row in macro:
            # A Polymarket key is "<event slug>:<market slug>" — only the market half reads
            # as a line item here, the event half is already carried by `why`.
            top = ", ".join(f"{key.rsplit(':', 1)[-1]} {value:.0%}" for key, value in row.top)
            suffix = f" (+{row.others} more)" if row.others else ""
            out.append(f"    {row.why}: {top}{suffix}")
    return "\n".join(out)
