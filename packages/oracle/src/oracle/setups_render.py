"""Terminal rendering for the setups queue — pure string building, no I/O.

Split out of ``setups_cli`` because the two answer different questions. The CLI decides *what
to ask*; this decides *what a candidate looks like when you have four seconds to judge it*.
Keeping it here means the layout can be exercised character-by-character in tests without
standing up a queue, a price cache, or a prompt.

The layout is a **price ladder**: every level the trade depends on, stacked in price order,
with the market's current price sitting in its true position among them. That ordering is the
whole point — a trader reads a chart vertically, and a list of labelled numbers in arbitrary
order forces a mental re-sort before the trade's shape is visible at all. Rendering by price
also means a short reads correctly with no special-casing: its target simply sorts to the
bottom, because that is where it is.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date

from core.setups import ARRIVAL, Candidate

# Below this many observations, a carry number is shown with its sample count so it cannot be
# read as a settled rate. A display marker and nothing more — it must not become a gate, and
# deliberately lives here rather than in `core` so it cannot drift into scoring.
#
# The honest measure would be the *span* the observations cover, which `FundingOutlook` does
# not carry. This stands in for it: the nightly writes one sweep per market per run, so a
# market first seen days ago cannot reach a count that a month-old one clears easily. Live
# 2026-07-29 the split was stark enough not to need precision — HOOD 477 against ZM's 5.
THIN_OBSERVATIONS = 20

# ── colour ────────────────────────────────────────────────────────────────────────────────────

_RESET = "\033[0m"
_STYLES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "bold_green": "\033[1;32m",
    "bold_red": "\033[1;31m",
    "bold_cyan": "\033[1;36m",
}


def supports_color(stream=None) -> bool:
    """Whether to emit escape codes at all.

    Honours ``NO_COLOR`` (the informal cross-tool standard) and ``TERM=dumb``, and requires a
    real terminal — so piping the queue into a file or a pager yields clean text rather than
    a spray of escape sequences, and the test suite gets the plain rendering for free without
    having to strip anything.
    """
    stream = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, style: str, *, color: bool) -> str:
    """Wrap ``text`` in ``style`` when colour is on, else return it untouched.

    Colour is passed down as an argument rather than read from the environment inside the
    formatter on purpose: the rendering stays a pure function of its inputs, so a test can
    assert on both the painted and the plain form without monkeypatching anything global.
    """
    if not color or not text or style not in _STYLES:
        return text
    return f"{_STYLES[style]}{text}{_RESET}"


def _visible_len(text: str) -> int:
    """Length as the terminal sees it — escape codes occupy no columns.

    Column alignment is computed against painted strings, so measuring their raw length would
    push every coloured row out of line by exactly the width of its escape codes.
    """
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            i = text.find("m", i) + 1 or len(text)
            continue
        out += 1
        i += 1
    return out


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _visible_len(text))


def _rpad(text: str, width: int) -> str:
    return " " * max(0, width - _visible_len(text)) + text


def _align_decimals(values: list[str]) -> list[str]:
    """Pad a column of numbers so their decimal points line up.

    Plain right-alignment is not enough when precision varies within one ladder — CL's levels
    render as ``109.47``, ``97``, ``88.45``, ``50``, and right-aligning those puts the tens
    column of one number under the hundredths of another. Digits are never added, so a level
    that is exactly 97 still reads ``97`` rather than claiming a precision it does not have.
    """
    parts = [v.split(".") for v in values]
    whole_w = max(len(p[0]) for p in parts)
    frac_w = max((len(p[1]) for p in parts if len(p) > 1), default=0)
    out = []
    for p in parts:
        frac = f".{p[1]}" if len(p) > 1 else ""
        out.append(" " * (whole_w - len(p[0])) + p[0] + frac + " " * (frac_w + 1 - len(frac)))
    return [v.rstrip() if frac_w == 0 else v for v in out]


# ── the ladder ────────────────────────────────────────────────────────────────────────────────

# Vertical rail glyphs. The zone is drawn heavy and everything outside it light, so the band
# price has to reach is visible as a thickness before any number is read.
_RAIL_TOP, _RAIL_BOTTOM, _RAIL_LIGHT, _RAIL_HEAVY = "╷", "╵", "│", "┃"
_RAIL_INTO_ZONE, _RAIL_OUT_OF_ZONE = "╽", "╿"   # light↕heavy transitions, at the zone edges
# Terminators for a ladder that begins or ends *inside* the band — which happens whenever the
# zone's far edge is also the last level, as it is every time stop and invalidation coincide.
# The half-height transition glyphs would draw a light stub against nothing there.
_RAIL_TOP_HEAVY, _RAIL_BOTTOM_HEAVY = "╻", "╹"

# Freshness at exactly one half-life. Not a new constant: ``core.setups.freshness_signal`` is
# ``1/(1+age/half_life)``, which is 0.5 at the half-life by construction, so this is that curve's
# own midpoint rather than a threshold invented for the display. A view at or past it has lost
# half the weight it was born with. Purely cosmetic — nothing is filtered on it. TUNE: it flags
# later than the eye does, and real rejects have already been written for staleness above it.
STALE_FRESHNESS = 0.50


@dataclass(frozen=True)
class _Level:
    """One rung: a price, the labels sitting at it, and how far it is from entry."""
    price: float
    labels: tuple[str, ...]
    pct: float | None
    note: str = ""
    is_price: bool = False


def _levels(c: Candidate) -> list[_Level]:
    """The trade's levels, highest price first, with equal prices merged into one rung.

    Merging matters: ``stop`` and ``invalidation`` frequently coincide (a zone whose far edge
    *is* its origin swing), and printing the same number on two rungs implies two distinct
    places the trade could be wrong when there is only one.
    """
    raw = [
        (c.target, "target", c.target_source, False),
        (c.entry, "entry", "", False),
        (c.price, "price", "", True),
        (c.stop, "stop", "", False),
        (c.invalidation, "invalidation", "", False),
    ]
    merged: dict[str, _Level] = {}
    for price, label, note, is_price in raw:
        key = f"{price:g}"
        if key in merged:
            prior = merged[key]
            merged[key] = _Level(
                price=prior.price, labels=prior.labels + (label,),
                pct=prior.pct, note=prior.note or note,
                is_price=prior.is_price or is_price,
            )
            continue
        # Percentages are quoted from entry because entry is where the position is opened —
        # a move measured from anywhere else is not a move this trade experiences.
        pct = None if label == "entry" or c.entry == 0 else (price / c.entry - 1) * 100
        merged[key] = _Level(price=price, labels=(label,), pct=pct, note=note,
                             is_price=is_price)
    return sorted(merged.values(), key=lambda lv: lv.price, reverse=True)


def _segment_heavy(rows: list[_Level], i: int, *, top: float, bottom: float) -> bool:
    """Whether the rail *between* rung ``i`` and rung ``i+1`` runs through the zone.

    Weight belongs to the gaps, not the rungs, and the difference is visible: asking whether a
    rung is "inside" puts the light↔heavy transition one row early, drawing the band as though
    it started at the target rather than at the zone's own edge. Overlap must be positive, so
    a rung sitting exactly on an edge bounds the band instead of extending it.
    """
    if i < 0 or i + 1 >= len(rows):
        return False
    return min(rows[i].price, top) > max(rows[i + 1].price, bottom)


def _rail(rows: list[_Level], i: int, *, top: float, bottom: float) -> str:
    """The rail glyph for one rung — determined by the weight of the segments it joins."""
    above = _segment_heavy(rows, i - 1, top=top, bottom=bottom)
    below = _segment_heavy(rows, i, top=top, bottom=bottom)
    if i == 0:
        return _RAIL_TOP_HEAVY if below else _RAIL_TOP
    if i == len(rows) - 1:
        return _RAIL_BOTTOM_HEAVY if above else _RAIL_BOTTOM
    if above and below:
        return _RAIL_HEAVY
    if above:
        return _RAIL_OUT_OF_ZONE
    if below:
        return _RAIL_INTO_ZONE
    return _RAIL_LIGHT


def _age_days(newest_at: str, as_of: date | None) -> int | None:
    """Days since the freshest supporting view, or None when it can't be known."""
    if as_of is None or not newest_at:
        return None
    try:
        return (as_of - date.fromisoformat(newest_at)).days
    except ValueError:
        return None


def format_views(candidate: Candidate) -> str:
    """``Person (date)`` per supporter, newest first — so a stale voice is visible as one."""
    return ", ".join(f"{v.person} ({v.published_at})" for v in candidate.views)


def format_candidate(candidate: Candidate, *, rank: int | None = None,
                     total: int | None = None, as_of: date | None = None,
                     color: bool = False, venue_symbol: str | None = None,
                     venue: str | None = None,
                     zones_in_thesis: int = 1, zone_index: int = 1) -> str:
    """Render one candidate as a price ladder plus four summary lines.

    Stop and invalidation stay two distinct labelled values (they answer "where is this trade
    wrong" vs "where does the zone itself die"), and ``target_source`` is always shown so an
    inferred target never reads as a clean, stated one. Both were true of the flat layout this
    replaced and neither survives being merged into a prettier line.

    Every candidate leads with **when it was last called**, now as an age in days rather than a
    bare date — the date alone made the reader do subtraction to notice a zone was months old,
    and staleness is the thing rejections actually get written about.
    """
    c = candidate
    rows = _levels(c)
    top, bottom = max(c.entry_top, c.entry_bottom), min(c.entry_top, c.entry_bottom)

    prices = _align_decimals([f"{lv.price:g}" for lv in rows])
    price_w = max(len(p) for p in prices)
    label_w = max(len(" = ".join(lv.labels)) for lv in rows)

    ladder = []
    for i, lv in enumerate(rows):
        rail = _rail(rows, i, top=top, bottom=bottom)
        label = " = ".join(lv.labels)
        # Painted first, padded second, always. Padding inside an escape sequence puts the
        # trailing spaces where ``rstrip`` cannot see them, so the coloured and plain
        # renderings of the same candidate stop being the same layout.
        if lv.is_price:
            price_txt = _rpad(paint(prices[i], "bold_cyan", color=color), price_w)
            label = _pad(paint("◀ price now", "bold_cyan", color=color), label_w)
            extra = ""
        else:
            style = {"target": "green", "stop": "red", "invalidation": "red"}.get(
                lv.labels[0], "bold")
            price_txt = _rpad(paint(prices[i], style, color=color), price_w)
            label = _pad(label, label_w)
            pct = "" if lv.pct is None else f"{lv.pct:+.1f}%"
            extra = f"{_rpad(pct, 7)}  {paint(lv.note, 'dim', color=color)}".rstrip()
        ladder.append(f"    {price_txt}  {rail}  {label}  {extra}".rstrip())

    # The rail is drawn continuous by filling the gaps between rungs, so the zone reads as one
    # band rather than as detached ticks floating beside the numbers.
    body = []
    for i, line in enumerate(ladder):
        body.append(line)
        if i < len(ladder) - 1:
            gap_rail = (_RAIL_HEAVY if _segment_heavy(rows, i, top=top, bottom=bottom)
                        else _RAIL_LIGHT)
            body.append(f"    {' ' * price_w}  {gap_rail}")

    return "\n".join(["", _headline(c, rank=rank, total=total, color=color,
                                    venue_symbol=venue_symbol, venue=venue,
                                    zones_in_thesis=zones_in_thesis, zone_index=zone_index), "",
                      *body, "", *_summary(c, as_of=as_of, color=color)])


def _headline(c: Candidate, *, rank: int | None, total: int | None, color: bool,
              venue_symbol: str | None = None, venue: str | None = None,
              zones_in_thesis: int = 1, zone_index: int = 1) -> str:
    """Asset, direction and the two numbers the whole judgement hangs on.

    The zone timeframe stays in the heading because the same asset can appear twice — once per
    timeframe — and the two differ in exactly the numbers a glance skips over. Unlabelled, a
    weekly and a daily GOOGL long read as a duplicate rather than as two setups of different
    risk. The rank carries ``/total`` so the queue's remaining depth is visible mid-session.

    **The row names the instrument, not just the concept.** ``CHINA`` was excluded during
    triage as "I don't see this ticker anywhere" — it routes to ``FXI`` and was the most
    liquid name in that queue. An exclusion is durable and nothing re-offers the asset, so one
    missing arrow permanently dropped a tradeable setup for a reason that was not true. Shown
    only when the symbol differs, because ``SBSW -> SBSW`` on every other row is the noise
    that would hide the case that matters.
    """
    counter = "" if rank is None else f"[{rank}{'' if total is None else f'/{total}'}] "
    direction = c.direction.upper()
    dir_style = "bold_green" if direction == "LONG" else "bold_red"

    # An unmapped asset reaches the prompt and fails at placement with "no listing on this
    # venue" — after the judgement has been spent, which is the scarce input. Saying so here
    # costs a word and moves the refusal to before the decision instead of after it.
    if venue_symbol is None and venue is not None:
        routing = f" {paint(f'(unmapped on {venue})', 'dim', color=color)}"
    elif venue_symbol and venue_symbol != c.asset:
        routing = f" {paint(f'→ {venue_symbol}', 'yellow', color=color)}"
    else:
        routing = ""

    head = (f"{counter}{paint(c.asset, 'bold', color=color)}{routing} "
            f"{paint(direction, dir_style, color=color)}")
    # One thesis can yield a weekly zone and a daily one, and they are offered as two separate
    # decisions because they are two different trades. Adjacency alone reads as the duplicate
    # §27's sitting mistook them for, so the pair states itself: judge the second knowing the
    # first exists, rather than against a memory of it three rows back.
    pair = ("" if zones_in_thesis < 2 else
            f" · {zones_in_thesis} zones for this thesis · {zone_index} of {zones_in_thesis}")
    meta = paint(f"{c.zone_timeframe} zone · {c.tier}{pair}", "dim", color=color)
    stats = (f"{paint('score', 'dim', color=color)} {paint(f'{c.score:.2f}', 'bold', color=color)}"
             f"  {paint('R:R', 'dim', color=color)} "
             f"{paint(f'{c.reward_risk:.2f}', 'bold', color=color)}")
    # The ratio the *score* actually uses, beside the one the trade actually pays. Shown for
    # the reason a soft signal always is here: since `SCORE_VERSION` 6 the queue is ordered on
    # this number and not on `R:R`, so leaving it off would explain the order of the queue with
    # a figure the order does not use. The gap between the two is the reachability penalty —
    # a big drop means price has run far from the zone (§19d).
    if abs(c.reward_risk_from_price - c.reward_risk) >= 0.005:
        drift = "green" if c.reward_risk_from_price >= c.reward_risk else "red"
        stats += (f"  {paint('scored', 'dim', color=color)} "
                  f"{paint(f'{c.reward_risk_from_price:.2f}', drift, color=color)}")
    # Carry-adjusted R:R sits beside the nominal one rather than replacing it. Both are shown
    # because the *gap* between them is the new information — and because the ranking is still
    # done on the nominal number, so displaying only the adjusted one would explain the order
    # of the queue with a figure the order does not use.
    if c.carry_reward_risk is not None:
        drift = "green" if c.carry_reward_risk >= c.reward_risk else "red"
        stats += (f"  {paint('adj', 'dim', color=color)} "
                  f"{paint(f'{c.carry_reward_risk:.2f}', drift, color=color)}")
        # A thin outlook is shown with its sample count, a well-observed one without. `n` was
        # put on FundingOutlook so a reader could "tell a measured rate from one observation"
        # and then never reached the queue — which stopped mattering only while the funding log
        # covered nothing but long-mapped markets. Widening cfg/venue_map.yaml put medians over
        # three nights beside medians over a month, printed identically.
        if c.funding_n is not None and c.funding_n < THIN_OBSERVATIONS:
            stats += f" {paint(f'n={c.funding_n}', 'dim', color=color)}"
    return f"{head}  {meta}   {stats}"


def approach_phrase(c: Candidate) -> str:
    """``approach`` in words as well as digits.

    The number alone is ambiguous in the way that matters: 0.40 and 0.80 are both "partway",
    but one is price still travelling toward the zone and the other is price sitting inside it,
    and that is exactly the distinction being judged. Reporting the raw ramp only would repeat
    the mistake the two-term form made — a figure that is precise and doesn't say where price
    is.
    """
    if c.approach < ARRIVAL:
        return f"price approaching · approach {c.approach:.2f}"
    depth = (c.approach - ARRIVAL) / (1.0 - ARRIVAL)
    return f"price in zone, {depth:.0%} deep · approach {c.approach:.2f}"


def _summary(c: Candidate, *, as_of: date | None, color: bool) -> list[str]:
    """The four lines under the ladder: age, structure, trend, and who is behind it."""
    age = _age_days(c.newest_at, as_of)
    age_txt = "" if age is None else f" ({age}d ago)"
    stale = c.freshness <= STALE_FRESHNESS
    called = f"{c.newest_at}{age_txt}"
    called = paint(called, "yellow" if stale else "dim", color=color)
    if stale:
        called = f"{paint('STALE', 'yellow', color=color)}  {called}"
    span = "" if c.newest_at == c.oldest_at else f" · oldest {c.oldest_at}"

    # Age and macro alignment stopped being gates, so the queue has to show them. A soft gate
    # that isn't displayed is strictly worse than a hard one: the candidate arrives looking
    # like every other, and the judgement it was softened to enable can't actually be made.
    unaligned = "" if c.trend_alignment else (
        " · " + paint("no macro alignment", "yellow", color=color))

    label = lambda text: paint(_pad(text, 7), "dim", color=color)  # noqa: E731
    return [
        f"  {label('called')}{called}{paint(span, 'dim', color=color)}"
        f"{paint(f' · freshness {c.freshness:.2f}', 'dim', color=color)}",
        f"  {label('zone')}{c.zone} · {approach_phrase(c)}",
        f"  {label('trend')}weekly {c.weekly_trend} · daily {c.daily_trend}{unaligned}",
        f"  {label('who')}{format_views(c)} · agreement {c.agreement}",
    ]


def thesis_pairing(candidates) -> list[tuple[int, int]]:
    """Per row, how many zones of its thesis are **on this screen** and which one it is.

    Counted over the sitting's own rows rather than the whole population, deliberately. If
    sampling drew only one of a pair, "1 of 2" would point at a row that is not there and
    cannot be compared against — so a lone sibling gets no marker and reads as the single
    decision it actually is. ``collapse`` guarantees the pair is adjacent when both are drawn.

    Pure, and keyed the same way ``collapse`` groups: one thesis is one (asset, direction).

    **Currently always returns (1, 1)**, because ``queue.one_per_asset`` now keeps a single row
    per ticker, so no sitting can contain a pair. Kept rather than deleted so the dedupe stays
    one function to reverse: drop that call and the "1 of 2" label works again. If one-per-ticker
    outlives this note, delete both.
    """
    counts: dict[tuple[str, str], int] = {}
    for c in candidates:
        key = (c.asset, c.direction)
        counts[key] = counts.get(key, 0) + 1

    seen: dict[tuple[str, str], int] = {}
    out = []
    for c in candidates:
        key = (c.asset, c.direction)
        seen[key] = seen.get(key, 0) + 1
        out.append((counts[key], seen[key]))
    return out
