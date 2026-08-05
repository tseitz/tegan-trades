"""Fair value gaps — the price voids displacement leaves behind.

Pure and duck-typed exactly like ``core.structure``: the caller supplies any *sequence* of
bars exposing ``date``/``open``/``high``/``low``/``close``, and nothing here does I/O or
mutates anything. ``core`` deliberately does not depend on ``oracle``, which is why these
functions never name a concrete bar type.

**Why ATR here is a simple mean, not Wilder's smoothing.** This value is only ever used as a
threshold for "is this candle unusually large" — a predictable, order-independent window beats
a recursive average whose value quietly depends on where the series happens to start. Wilder
smoothing would make the ATR at a given index depend on the entire history before the lookback
window, which is the opposite of what a fixed threshold needs.

**Why displacement is measured one bar before the candle it gates.** A displacement candle is
large by definition, so including it in its own ATR window inflates the very threshold it is
being tested against — the filter then partly cancels itself out. Measuring the window strictly
before the candle keeps the threshold independent of the thing it's judging.

**Why a gap is dated at candle 3, not candle 2.** The void isn't knowable until the third
candle closes without retracing into it — the same no-look-ahead discipline ``core.structure``
implements via ``Swing.confirmed_at``. Dating it at candle 2 would credit a caller with
knowledge they couldn't have had yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Direction vocabulary is owned by core.structure and re-exported here rather than redefined,
# so the two modules can never drift into disagreeing about what "bullish" is.
from core.structure import BEARISH, BULLISH

# Bars in the simple-mean ATR window. 14 is the stated default; tunable, like
# core.structure's SWING_WIDTH.
ATR_LOOKBACK = 14

# How many ATRs a candle's body must clear to count as displacement. Tunable.
DISPLACEMENT_MULTIPLE = 1.5

__all__ = [
    "ATR_LOOKBACK", "BEARISH", "BULLISH", "DISPLACEMENT_MULTIPLE",
    "Gap", "atr", "fair_value_gaps", "is_displacement", "true_range",
]


def true_range(bar, prev_close: float | None) -> float:
    """The widest of the three legs a bar can move on, given its predecessor's close.

    ``prev_close is None`` means there is no predecessor at all (the first bar in the whole
    series) — the only leg that exists is the bar's own high-low range.
    """
    if prev_close is None:
        return bar.high - bar.low
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(prev_close - bar.low),
    )


def atr(bars, index: int, *, lookback: int = ATR_LOOKBACK) -> float | None:
    """The simple mean of true range over the ``lookback`` bars ending at ``index``.

    Returns None rather than a partial-window average when history is short — a threshold
    computed from a couple of bars would silently pass almost anything, and a caller has no
    way to tell that apart from a threshold computed from a full window.
    """
    if index < lookback - 1:
        return None
    total = 0.0
    for i in range(index - lookback + 1, index + 1):
        prev_close = bars[i - 1].close if i > 0 else None
        total += true_range(bars[i], prev_close)
    return total / lookback


def is_displacement(bars, index: int, *, multiple: float = DISPLACEMENT_MULTIPLE,
                     lookback: int = ATR_LOOKBACK) -> bool:
    """Whether ``bars[index]``'s body is unusually large relative to recent true range."""
    # Measured at index - 1, not index: a displacement candle is large by definition, so
    # folding it into its own ATR window would inflate the threshold it's being tested
    # against, letting the filter partly cancel itself out.
    threshold = atr(bars, index - 1, lookback=lookback)
    if not threshold:  # None (insufficient history) or 0 — neither can judge "unusual"
        return False
    body = abs(bars[index].close - bars[index].open)
    return body >= multiple * threshold


@dataclass(frozen=True, slots=True)
class Gap:
    """A fair value gap confirmed by a displaced middle candle.

    ``date``/``index`` are candle 3's — the gap isn't knowable until that candle closes.
    ``middle_index`` is candle 2, the displacement candle, carried so a caller can inspect
    what drove the gap without re-deriving the triple.
    """
    kind: str          # BULLISH | BEARISH
    top: float
    bottom: float
    date: date | datetime
    index: int
    middle_index: int


def fair_value_gaps(bars, *, multiple: float = DISPLACEMENT_MULTIPLE,
                     lookback: int = ATR_LOOKBACK) -> tuple[Gap, ...]:
    """Every displacement-confirmed fair value gap in ``bars``, ascending by index.

    ``bars`` must be an indexable sequence in ascending date order with unique dates.

    Each consecutive triple ``(c1, c2, c3) = (bars[i-2], bars[i-1], bars[i])`` is checked for
    a price void between c1 and c3, but a bare gap doesn't qualify: the construct means
    "price moved so hard it left an untraded void," so c2 must also satisfy
    ``is_displacement`` — without that filter this degenerates into flagging ordinary gaps.
    """
    found: list[Gap] = []
    for i in range(2, len(bars)):
        # The middle candle is reached via is_displacement(bars, i - 1) below, so only the
        # outer two are needed to measure the void itself.
        c1, c3 = bars[i - 2], bars[i]

        if c1.high < c3.low:
            kind, top, bottom = BULLISH, c3.low, c1.high
        elif c1.low > c3.high:
            kind, top, bottom = BEARISH, c1.low, c3.high
        else:
            continue

        if not is_displacement(bars, i - 1, multiple=multiple, lookback=lookback):
            continue

        found.append(Gap(kind=kind, top=top, bottom=bottom, date=c3.date,
                          index=i, middle_index=i - 1))

    return tuple(found)
