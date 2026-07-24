"""Daily OHLC as an immutable, leak-free lens over cached bars.

Every source (Coinbase, Yahoo, Kraken) normalizes into a ``PriceSeries``, so the grading
layer never learns which API a price came from. Two invariants matter more than anything
else here, because this phase is a backtest:

1. **Never look forward.** ``close_on`` resolves to the newest bar *at or before* the
   requested date. Reading a later bar would leak future information into an earlier
   grade and silently inflate every score in the corpus.
2. **Never invent a price.** A short carry across a weekend/holiday is legitimate; a long
   one means the data is genuinely absent (delisting, outage, pre-listing). Past
   ``max_carry_days`` we return ``None`` and let the caller degrade — mirroring
   ``core.rank.parse_date``, which returns ``None`` rather than raising on bad input.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta

# A 3-day weekend plus a holiday is the realistic worst case for equities (Thanksgiving,
# Easter, Christmas). Beyond that the series is missing data, not merely closed.
MAX_CARRY_DAYS = 5


@dataclass(frozen=True, slots=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class PriceSeries:
    symbol: str
    source: str
    bars: tuple[Bar, ...] = ()
    _dates: tuple[date, ...] = field(init=False, repr=False, compare=False, default=())

    def __post_init__(self) -> None:
        # Sources disagree on order (Coinbase returns newest-first) and paginated fetches
        # overlap at the seams. Normalize once, here, so every consumer downstream can
        # assume ascending and unique.
        deduped = {bar.date: bar for bar in self.bars}
        ordered = tuple(deduped[d] for d in sorted(deduped))
        object.__setattr__(self, "bars", ordered)
        object.__setattr__(self, "_dates", tuple(b.date for b in ordered))

    @property
    def span(self) -> tuple[date, date] | None:
        """(first, last) bar dates, or None when empty."""
        if not self.bars:
            return None
        return self._dates[0], self._dates[-1]

    def _bar_at_or_before(self, when: date) -> Bar | None:
        idx = bisect_right(self._dates, when)
        return self.bars[idx - 1] if idx else None

    def close_on(self, when: date, *, max_carry_days: int = MAX_CARRY_DAYS) -> float | None:
        """Close for ``when``, carrying the last known bar forward across market closures.

        Returns None when the date precedes the series or the carry would exceed
        ``max_carry_days`` — a missing price must never masquerade as a real one.
        """
        bar = self._bar_at_or_before(when)
        if bar is None:
            return None
        if when - bar.date > timedelta(days=max_carry_days):
            return None
        return bar.close

    def extremes_between(self, start: date, end: date) -> tuple[float, float] | None:
        """(low, high) across bars in the inclusive window, or None if it contains none.

        Tier-2 level grading is path-dependent — "did it reach the target before the
        stop" — so it needs intrabar extremes, not just closes.
        """
        window = [b for b in self.bars if start <= b.date <= end]
        if not window:
            return None
        return min(b.low for b in window), max(b.high for b in window)
