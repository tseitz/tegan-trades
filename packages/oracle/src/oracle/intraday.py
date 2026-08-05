"""Intraday OHLCV — the timeframes below the daily, where entries are triggered.

The daily ``oracle.series.PriceSeries`` answers *what* to trade and roughly where. This
answers *when*: a weekly bias narrows to an H12 setup zone, and price entering that zone is
not the entry — the entry is an H1 confirmation inside it. That is the top-down method the
corpus describes, and it needs bars the daily series cannot represent.

**Why a separate type rather than a field on ``Bar``.** Two independent reasons, either of
which alone would be weak and which together are decisive:

1. *A clock, not a calendar.* These bars are stamped to the hour. Widening ``Bar.date`` to
   ``date | datetime`` would push that ambiguity through ``close_on``, ``extremes_between``,
   ``cache``, ``resample`` and every grading path — all of which do real calendar arithmetic
   (``MAX_CARRY_DAYS``, ISO weeks) that is meaningless on an hourly stamp.
2. *Volume.* The trigger's gate — refuse a candidate whose entry timeframe has no trade in
   it — has to read volume off the bars. ``execution.participation`` cannot answer it: it
   measures *daily* equity depth and needs an open broker. But volume is also meaningless for
   a derived ratio series (``oracle.derived`` — what is the volume of ETH/BTC?), so it cannot
   simply be added to the type those series are built from.

**Why the timestamp field is still called ``date``.** ``core.structure`` and
``core.imbalance`` are duck-typed on a bar exposing ``date``/``open``/``high``/``low``/
``close``, and they use that attribute purely as an orderable token — comparisons and sorts,
never calendar arithmetic. Keeping the name is what lets swings, breaks of structure, order
blocks and fair value gaps run on intraday bars with no change to ``core`` at all. The name
is a small lie about the type; renaming it would be a large one about the contract.

**Timestamps are bucket starts.** The 14:00 bar covers 14:00–15:00. It follows that a bar is
only *complete* once a later bar exists, which is the rule ``oracle.resample`` already uses to
decide when a week is done — stated here because it is what makes an incomplete final bar
detectable rather than merely wrong.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime

# Interval labels. These are identity, not decoration: they land in the cache path, because
# BTC-USD hourly and BTC-USD twelve-hourly are different data that would otherwise collide
# onto one file. H12 is always resampled — Coinbase's granularities jump 3600 -> 21600 ->
# 86400 and no venue we route to serves a native 12-hour candle.
M15 = "15m"
H1 = "1h"
H12 = "12h"


@dataclass(frozen=True, slots=True)
class IntradayBar:
    """One OHLCV candle, stamped at the start of the period it covers.

    ``volume`` defaults to None rather than 0.0 for the reason
    ``execution.participation.check_depth`` already encodes: a market that traded nothing is a
    refusal, a market nobody measured is merely unmeasured. Sources differ on whether they
    report volume at all, and collapsing the two would make every silent source look dead.
    """
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class IntradaySeries:
    """Immutable, ascending, unique-by-timestamp bars at one interval.

    Mirrors ``PriceSeries.__post_init__``'s normalization contract so that every consumer —
    including ``core``'s primitives, which document the requirement — can assume ascending
    order and unique stamps without re-checking.
    """
    symbol: str
    source: str
    interval: str
    bars: tuple[IntradayBar, ...] = ()
    _stamps: tuple[datetime, ...] = field(init=False, repr=False, compare=False, default=())

    def __post_init__(self) -> None:
        # Reject naive stamps here rather than letting them through. A naive/aware mix raises
        # from inside ``sorted`` with a message naming neither the symbol nor the source, and
        # a uniformly-naive series is worse still: it sorts and compares perfectly while
        # sitting some whole number of hours away from every other series in the repo.
        for bar in self.bars:
            if bar.date.tzinfo is None or bar.date.utcoffset() is None:
                raise ValueError(
                    f"{self.source}:{self.symbol} {self.interval} bar at {bar.date} is not "
                    "timezone-aware; intraday stamps must carry an offset"
                )
        deduped = {bar.date: bar for bar in self.bars}
        ordered = tuple(deduped[t] for t in sorted(deduped))
        object.__setattr__(self, "bars", ordered)
        object.__setattr__(self, "_stamps", tuple(b.date for b in ordered))

    @property
    def span(self) -> tuple[datetime, datetime] | None:
        """(first, last) bar stamps, or None when empty."""
        if not self.bars:
            return None
        return self._stamps[0], self._stamps[-1]

    @property
    def latest(self) -> IntradayBar | None:
        """The newest bar, which on a live series is the one still forming."""
        return self.bars[-1] if self.bars else None

    @property
    def traded_bars(self) -> int | None:
        """How many bars actually traded, or None if this source reports no volume at all.

        The gate's raw material: a trigger drawn on a timeframe whose bars are mostly empty is
        drawn on nothing. Returning None for an unmeasured source keeps that distinct from a
        measured zero, so the gate can decline to judge rather than refuse.
        """
        measured = [b.volume for b in self.bars if b.volume is not None]
        if not measured:
            return None
        return sum(1 for v in measured if v > 0)

    def bars_between(self, start: datetime, end: datetime) -> tuple[IntradayBar, ...]:
        """Bars in the inclusive window. Binary search, not a scan: an hourly series over the
        corpus span is ~18,000 bars and the trigger slices it per candidate."""
        lo = bisect_left(self._stamps, start)
        hi = bisect_right(self._stamps, end)
        return self.bars[lo:hi]
