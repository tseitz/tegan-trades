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

HEADERS = ("TICKER", "SHARES", "PRICE", "VALUE", "P&L", "ROSTER", "WEEKLY", "")


def render(readings, *, portfolio: str, as_of) -> str:
    """The whole report. ``as_of`` is passed in rather than read from a clock so a replay of
    a past date prints that date, not today's."""
    head = f"{portfolio} · {len(readings)} position(s) · as of {as_of.isoformat()}"
    if not readings:
        return f"{head}\n\n  no positions — nothing to review"

    ranked = sorted(readings, key=_rank)
    lines = [head, "", *_table(ranked)]

    total = sum(r.market_value for r in ranked if r.market_value is not None)
    unpriced = [r for r in ranked if r.price is None]
    tail = f"  total {_money(total)}"
    if unpriced:
        # Named rather than netted out. A total quietly missing three holdings reads as your
        # whole account, which is a worse error than a total that admits its own hole.
        tail += f" (excludes {len(unpriced)} with no price)"
    lines += ["", tail]

    notes = [_note(r) for r in ranked if r.verdict in LOUD]
    if notes:
        lines += ["", *notes]
    return "\n".join(lines)


def _rank(reading: Reading) -> tuple[int, float]:
    """Urgency first, then size. Sorting the loud rows by what they are worth puts the
    decision that moves the most money at the top of the group that wants a decision."""
    urgency = ORDER.index(reading.verdict) if reading.verdict in ORDER else len(ORDER)
    return (urgency, -(reading.market_value or 0.0))


def _table(readings) -> list[str]:
    rows = [[
        r.holding.ticker,
        _num(r.holding.shares),
        _money(r.price),
        _money(r.market_value),
        _signed(r.pnl),
        roster_text(r),
        where_text(r),
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


def _note(reading: Reading) -> str:
    lean = reading.roster
    who = ", ".join(lean.voices) if lean.voices else "nobody"
    age = "" if lean.age_days is None else f", newest {lean.age_days}d ago"
    side = {BULLISH_ROSTER: "bullish", BEARISH_ROSTER: "bearish",
            MIXED: "split", SILENT: "silent"}.get(lean.lean, lean.lean)
    # Says why a row that looks like an ADD or a TRIM came back as WATCH. Without it the
    # grid looks broken: the roster is bullish, price is at support, and the verdict is the
    # cautious one for a reason nothing on the line explains.
    thin = " [one voice — needs a second]" if lean.thin else ""
    return (f"  {reading.verdict:<5} {reading.holding.ticker} — roster {side} "
            f"({who}{age}){thin}; price {where_text(reading)}"
            f"{'' if reading.weekly_trend is None else f', weekly {reading.weekly_trend}'}")


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _signed(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+,.2f}"


def _num(value: float) -> str:
    """Trailing zeros trimmed. A share count is 0.35 or 42.5, and padding both to a fixed
    precision makes a crypto position and an ETF position hard to tell apart at a glance."""
    return f"{value:,.8f}".rstrip("0").rstrip(".")
