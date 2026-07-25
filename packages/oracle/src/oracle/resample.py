"""Daily bars rolled up into weekly bars, without leaking the week's own future into itself.

The one decision that matters here is what date a weekly bar carries. Dating it at the
*start* of the week would make ``PriceSeries.close_on`` — which resolves to the newest bar
at or before the requested date — hand back a Wednesday query the week's Thursday and Friday
closes, days that haven't happened yet from that Wednesday's point of view. That's the same
look-ahead leak ``oracle.series`` treats as the top invariant, just introduced one layer up.
So the weekly bar is dated at the *last* daily bar it aggregates: a mid-week query then
resolves to the *prior* week's close, which is the only honest answer.

The same reasoning drives completeness. A week is only "done" once the data shows a bar from
some later week — that's the sole reliable signal that no more days are coming, since crypto
trades 7-day weeks and equities trade 5-day weeks and neither has a fixed last weekday to
check against. It follows that the final week in any series is always open, which is why
``include_partial`` defaults to False: an incomplete week's close is really just "the price
so far," and treating it as a closed bar would silently misrepresent the week that's still
in progress.
"""
from __future__ import annotations

from itertools import groupby

from oracle.series import Bar, PriceSeries


def to_weekly(series: PriceSeries, *, include_partial: bool = False) -> PriceSeries:
    """Aggregate ``series.bars`` into ISO-week (Mon-Sun) bars.

    ``series.bars`` is already ascending and unique-by-date (``PriceSeries.__post_init__``
    guarantees it), and ascending dates never produce a decreasing (year, week) key, so a
    plain ``groupby`` is enough — no sorting or bucket dict required.
    """
    groups = [
        tuple(bars)
        for _, bars in groupby(series.bars, key=lambda bar: bar.date.isocalendar()[:2])
    ]
    if not include_partial and groups:
        groups = groups[:-1]

    weekly_bars = tuple(
        Bar(
            date=week[-1].date,
            open=week[0].open,
            high=max(bar.high for bar in week),
            low=min(bar.low for bar in week),
            close=week[-1].close,
        )
        for week in groups
    )
    return PriceSeries(symbol=series.symbol, source=series.source, bars=weekly_bars)
