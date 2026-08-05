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

from datetime import UTC, datetime
from itertools import groupby

from oracle.intraday import H12, IntradayBar, IntradaySeries
from oracle.series import Bar, PriceSeries

# Where the day splits. UTC rather than any exchange's local midnight, so one instrument
# carves into the same twelve-hour periods regardless of which source served it.
H12_SPLIT_HOUR = 12

# What share of a day's activity the *thinner* half must carry for H12 to be a real extra rung
# rather than a thin artifact beside a real bar. Parity between the halves is 50%, so this is
# "at least half of parity" — the same shape as ``core.trigger.PARTICIPATION_FLOOR``.
#
# Measured 2026-08-05 over 13 continuous instruments and 66 routable equities: everything that
# trades around the clock lands between 31.4% (CL=F) and 50.6% (FX), and everything session-bound
# between 0.0% (^GSPC, ^DJI) and 22.3% (KORU, a 3x South Korea ETF whose pre-market tracks the
# Korean session). The floor sits high in that gap rather than in the middle of it *on purpose*:
# calling a session-bound instrument continuous draws a zone on a thin morning bucket, which is
# the §50 defect, while calling a continuous one session-bound merely costs a rung of resolution
# the spec already tolerates. The errors are not symmetric, so neither is the placement.
BALANCED_HALF_FLOOR = 0.25


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


def _h12_bucket(stamp: datetime) -> datetime:
    """The UTC twelve-hour period ``stamp`` falls in, identified by its start."""
    utc = stamp.astimezone(UTC)
    return utc.replace(
        hour=0 if utc.hour < H12_SPLIT_HOUR else H12_SPLIT_HOUR,
        minute=0, second=0, microsecond=0,
    )


def to_h12(series: IntradaySeries, *, include_partial: bool = False) -> IntradaySeries:
    """Aggregate hourly bars into twelve-hour bars — the setup timeframe, **for crypto only**.

    H12 exists to gain a rung of resolution on markets that never close. Equities do not have
    that problem and are actively harmed by this: an extended-hours feed runs 08:00-23:00 UTC,
    so a stock's day lands in two buckets and the morning one is four thin pre-market hours
    wearing the shape of a twelve-hour setup candle. Equities take the daily bar for that rung
    instead — see docs/IMPROVEMENTS.md §50. Nothing here rejects an equity series, because the
    resample's job is to report what it was handed; the choice belongs to the caller.

    **Why this is resampled and never fetched.** Coinbase's granularity ladder steps 3600 ->
    21600 -> 86400, and no venue we route to serves a native twelve-hour candle. There is no
    source to ask.

    **Why the bar is dated at the bucket's start, where ``to_weekly`` dates a week at its last
    daily bar.** The weekly rule exists to stop ``PriceSeries.close_on`` — newest bar at or
    before a date — from handing a mid-week query that week's later closes. ``IntradaySeries``
    has no such resolver, so that leak has no path here; and a start-dated bucket buys
    something the weekly rule cannot. A bucket's *last* bar moves as data arrives, so the same
    twelve hours would be stamped 09:00 on one run and 11:00 on the next, and ``merge_intraday``
    — which dedupes on the stamp — would keep both. One calendar bucket, two overlapping bars.
    A bucket start is computed from the clock alone, so it cannot drift.

    The look-ahead question that convention raises is answered by ``include_partial`` instead:
    a bar stamped 12:00 is not complete until 24:00, so the forming bucket is dropped unless
    asked for. Completeness is decided the way ``to_weekly`` decides it — "a later bucket
    exists" — rather than by comparing against the wall clock, because that keeps this a pure
    function of its bars. **Know the cost of that choice**: the newest complete bucket is
    withheld until some later bar shows up, so on a regular-session equity feed the session
    bucket is only confirmed when the *next* session opens, roughly seventeen hours later. A
    clock test would confirm it at 24:00 UTC, and if the trigger ever needs the zone sooner
    than that, injecting ``now`` here is the fix — not re-dating the bucket.

    **Empty buckets are absent, not flat.** Grouping the bars that exist means a period with no
    trading never produces a bar. Walking the clock and emitting one bucket per twelve hours
    would invent prices across every equity overnight and weekend, and ``core.imbalance``'s ATR
    would then average in ranges of zero — quietly lowering the displacement threshold until
    almost any candle cleared it.
    """
    groups = [
        tuple(bars) for _, bars in groupby(series.bars, key=lambda bar: _h12_bucket(bar.date))
    ]
    if not include_partial and groups:
        groups = groups[:-1]

    return IntradaySeries(
        symbol=series.symbol,
        source=series.source,
        interval=H12,
        bars=tuple(
            IntradayBar(
                date=_h12_bucket(bucket[0].date),
                open=bucket[0].open,
                high=max(bar.high for bar in bucket),
                low=min(bar.low for bar in bucket),
                close=bucket[-1].close,
                volume=_bucket_volume(bucket),
            )
            for bucket in groups
        ),
    )


def _bucket_volume(bucket) -> float | None:
    """Sum of the volume that was reported, or None if none of it was.

    A partly-measured bucket sums what it has rather than refusing to answer. That understates
    the market, which makes the trigger's gate more cautious — the safe direction — whereas
    None would have the gate decline to judge at all.
    """
    measured = [bar.volume for bar in bucket if bar.volume is not None]
    return sum(measured) if measured else None


def _weights(bars) -> list[float]:
    """Dollar volume per bar, or a flat 1.0 each when the source reports no volume at all.

    Indices are the volume-less case — Yahoo serves ``^GSPC`` and ``^DJI`` with none — and
    counting bars answers the same question for them: a session-bound index has no morning bars
    to count, which is exactly the 0.0% both of them measure. Dollar volume rather than share
    volume so the two halves stay comparable across instruments, matching what
    ``execution.participation`` already medians for the equivalent daily question.
    """
    if any(bar.volume is not None for bar in bars):
        return [(bar.volume or 0.0) * bar.close for bar in bars]
    return [1.0] * len(bars)


def straddles_the_split(series: IntradaySeries, *,
                        floor: float = BALANCED_HALF_FLOOR) -> bool:
    """Does this instrument trade on both sides of 12:00 UTC enough for H12 to mean anything?

    **This is the setup-rung decision, and it is computed rather than assigned.** The spec's
    asset-class table was wrong three times — US equities, ``^VIX``'s stated reason, ``^GSPC``
    — and every correction replaced a class label with a measurable property. This is that
    property. ``^GSPC`` is an index that behaves like an equity; ``^VIX`` is an index that does
    not; no label spanning both is true.

    False for an empty or unmeasurable series: unknown degrades to the daily bar, which always
    exists, is already cached, and is the rung equities take anyway.
    """
    bars = series.bars
    if not bars:
        return False
    weights = _weights(bars)
    total = sum(weights)
    if not total:
        return False
    morning = sum(
        weight for bar, weight in zip(bars, weights, strict=True)
        if bar.date.astimezone(UTC).hour < H12_SPLIT_HOUR
    )
    share = morning / total
    # The *thinner* half is the one tested — nothing says the dead half must be the morning.
    return min(share, 1.0 - share) >= floor
