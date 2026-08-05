from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from core import structure
from oracle.intraday import H1, H12, IntradayBar, IntradaySeries
from oracle.resample import to_h12, to_weekly
from oracle.series import Bar, PriceSeries


def _bar(d: str, *, open: float, high: float, low: float, close: float) -> Bar:
    return Bar(date=date.fromisoformat(d), open=open, high=high, low=low, close=close)


def _flat_bar(d: str, price: float) -> Bar:
    """A bar where open/high/low/close all equal ``price`` — convenient filler for days
    whose OHLC don't matter to the assertion at hand."""
    return _bar(d, open=price, high=price, low=price, close=price)


# ── the look-ahead pin ───────────────────────────────────────────────────────

def test_weekly_close_on_never_leaks_the_current_week():
    """Exists to catch look-ahead leakage: a weekly bar dated at the week's start would
    make close_on(mid-week) return that same week's close — including days that, from the
    query date's point of view, haven't happened yet. Dating at the week's last bar is what
    prevents that."""
    week1 = [_flat_bar(d, 100.0) for d in
              ("2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09",
               "2025-01-10", "2025-01-11", "2025-01-12")]
    week2 = [_flat_bar(d, 200.0) for d in
              ("2025-01-13", "2025-01-14", "2025-01-15", "2025-01-16",
               "2025-01-17", "2025-01-18", "2025-01-19")]
    series = PriceSeries(symbol="BTC-USD", source="coinbase", bars=tuple(week1 + week2))

    weekly = to_weekly(series, include_partial=True)

    # 2025-01-15 is a Wednesday inside week 2. If we'd dated week 2's bar at its start
    # (2025-01-13), this would wrongly resolve to week 2's close.
    assert weekly.close_on(date(2025, 1, 15)) == pytest.approx(100.0)


# ── OHLC aggregation ─────────────────────────────────────────────────────────

def test_ohlc_aggregates_open_close_high_low_correctly():
    bars = (
        _bar("2025-01-06", open=10.0, high=12.0, low=9.0, close=11.0),
        _bar("2025-01-07", open=11.0, high=15.0, low=10.5, close=14.0),
        _bar("2025-01-08", open=14.0, high=14.5, low=8.0, close=9.0),
    )
    # Trailing bar in the next week so the first week is complete.
    series = PriceSeries(symbol="X-USD", source="coinbase",
                          bars=(*bars, _flat_bar("2025-01-13", 9.5)))

    weekly = to_weekly(series, include_partial=False)

    assert len(weekly.bars) == 1
    bar = weekly.bars[0]
    assert bar.date == date(2025, 1, 8)  # last daily bar in the week
    assert bar.open == pytest.approx(10.0)   # first bar's open
    assert bar.close == pytest.approx(9.0)   # last bar's close
    assert bar.high == pytest.approx(15.0)   # max across the week
    assert bar.low == pytest.approx(8.0)     # min across the week


# ── include_partial ───────────────────────────────────────────────────────────

def test_include_partial_false_drops_trailing_incomplete_week():
    week1 = [_flat_bar(d, 100.0) for d in ("2025-01-06", "2025-01-07")]
    week2 = [_flat_bar(d, 200.0) for d in ("2025-01-13", "2025-01-14")]
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=tuple(week1 + week2))

    weekly = to_weekly(series, include_partial=False)

    assert len(weekly.bars) == 1
    assert weekly.bars[0].date == date(2025, 1, 7)


def test_include_partial_true_keeps_trailing_incomplete_week():
    week1 = [_flat_bar(d, 100.0) for d in ("2025-01-06", "2025-01-07")]
    week2 = [_flat_bar(d, 200.0) for d in ("2025-01-13", "2025-01-14")]
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=tuple(week1 + week2))

    weekly = to_weekly(series, include_partial=True)

    assert len(weekly.bars) == 2
    assert weekly.bars[-1].date == date(2025, 1, 14)
    assert weekly.bars[-1].close == pytest.approx(200.0)


# ── 5-day equity-style week ───────────────────────────────────────────────────

def test_five_day_equity_week_aggregates_and_counts_as_complete():
    week1 = (
        _bar("2025-01-06", open=100.0, high=105.0, low=99.0, close=101.0),  # Mon
        _bar("2025-01-07", open=101.0, high=103.0, low=100.0, close=102.0),  # Tue
        _bar("2025-01-08", open=102.0, high=104.0, low=101.0, close=103.0),  # Wed
        _bar("2025-01-09", open=103.0, high=106.0, low=102.0, close=104.0),  # Thu
        _bar("2025-01-10", open=104.0, high=107.0, low=103.0, close=98.0),   # Fri
    )
    # A later week (also 5-day, equities skip the weekend) makes week 1 complete.
    week2 = (_bar("2025-01-13", open=98.0, high=99.0, low=97.0, close=98.5),)
    series = PriceSeries(symbol="SPY", source="yahoo", bars=week1 + week2)

    weekly = to_weekly(series, include_partial=False)

    assert len(weekly.bars) == 1
    bar = weekly.bars[0]
    assert bar.date == date(2025, 1, 10)  # Friday, the week's last bar
    assert bar.open == pytest.approx(100.0)
    assert bar.close == pytest.approx(98.0)
    assert bar.high == pytest.approx(107.0)
    assert bar.low == pytest.approx(99.0)


# ── single-bar week ───────────────────────────────────────────────────────────

def test_week_with_a_single_daily_bar():
    bars = (
        _bar("2025-01-08", open=50.0, high=55.0, low=48.0, close=52.0),  # lone Wed
        _flat_bar("2025-01-13", 60.0),  # next week, makes the lone bar's week complete
    )
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=bars)

    weekly = to_weekly(series, include_partial=False)

    assert len(weekly.bars) == 1
    bar = weekly.bars[0]
    assert bar.date == date(2025, 1, 8)
    assert bar.open == pytest.approx(50.0)
    assert bar.high == pytest.approx(55.0)
    assert bar.low == pytest.approx(48.0)
    assert bar.close == pytest.approx(52.0)


# ── empty input ───────────────────────────────────────────────────────────────

def test_empty_series_returns_empty_series():
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=())

    weekly = to_weekly(series, include_partial=True)

    assert weekly.bars == ()
    assert weekly.symbol == "X-USD"
    assert weekly.source == "coinbase"


# ── year boundary ─────────────────────────────────────────────────────────────

def test_bars_spanning_a_year_boundary_do_not_collapse_into_one_week():
    # 2024-12-27 is ISO week (2024, 52); 2024-12-30 through 2025-01-02 is ISO week
    # (2025, 1). Grouping by week number alone (ignoring year) would wrongly merge these.
    bars = (
        _flat_bar("2024-12-27", 100.0),  # (2024, 52)
        _flat_bar("2024-12-30", 110.0),  # (2025, 1)
        _flat_bar("2025-01-02", 111.0),  # (2025, 1)
        _flat_bar("2025-01-06", 120.0),  # (2025, 2) - makes (2025, 1) complete
    )
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=bars)

    weekly = to_weekly(series, include_partial=True)

    assert [b.date.isoformat() for b in weekly.bars] == [
        "2024-12-27", "2025-01-02", "2025-01-06",
    ]
    assert weekly.bars[1].open == pytest.approx(110.0)
    assert weekly.bars[1].close == pytest.approx(111.0)


# ── H12 ──────────────────────────────────────────────────────────────────────
# The setup timeframe. Higher resolution than the daily in crypto, and — because the buckets
# are UTC-anchored — very nearly the daily in equities, which is the whole reason for choosing
# it: nothing is lost on stocks and a bar is gained on everything that trades around the clock.

def _h1(stamp: str, price: float, *, volume: float | None = 1.0,
        high: float | None = None, low: float | None = None) -> IntradayBar:
    return IntradayBar(
        date=datetime.fromisoformat(stamp),
        open=price, close=price,
        high=price if high is None else high,
        low=price if low is None else low,
        volume=volume,
    )


def _hourly(*bars) -> IntradaySeries:
    return IntradaySeries(symbol="BTC-USD", source="coinbase", interval=H1, bars=bars)


def _day(day: int, hours, price=lambda h: 100.0, **kw):
    return [_h1(f"2026-08-{day:02d}T{h:02d}:00:00+00:00", price(h), **kw) for h in hours]


# ── the dating convention, which is not to_weekly's ──────────────────────────

def test_a_bucket_is_dated_at_its_start_not_its_last_bar():
    """``to_weekly`` dates a week at its *last* daily bar; this dates a bucket at its start,
    and the difference is deliberate — see the next test for why it has to be."""
    h12 = to_h12(_hourly(*_day(1, range(12))), include_partial=True)
    assert h12.bars[0].date == datetime(2026, 8, 1, 0, tzinfo=UTC)


def test_a_bucket_keeps_its_stamp_when_a_later_bar_arrives():
    """The correctness argument for dating at the start, and it is not a style preference.

    ``merge_intraday`` dedupes on the stamp. Dating a bucket at its last constituent would
    move the stamp every time a fresher bar landed, so the same calendar bucket would merge
    into the cache twice — once as 09:00 and again as 11:00 — and the series would carry two
    overlapping bars claiming the same twelve hours.
    """
    early = to_h12(_hourly(*_day(1, range(10))), include_partial=True)
    later = to_h12(_hourly(*_day(1, range(12))), include_partial=True)
    assert early.bars[0].date == later.bars[0].date


def test_the_two_daily_buckets_split_at_noon_utc():
    h12 = to_h12(_hourly(*_day(1, [11, 12])), include_partial=True)
    assert [b.date.hour for b in h12.bars] == [0, 12]


def test_a_day_boundary_starts_a_new_bucket():
    h12 = to_h12(_hourly(*_day(1, [23]), *_day(2, [0])), include_partial=True)
    assert [(b.date.day, b.date.hour) for b in h12.bars] == [(1, 12), (2, 0)]


# ── OHLCV aggregation ────────────────────────────────────────────────────────

def test_ohlc_comes_from_the_bucket_s_own_first_last_and_extremes():
    bars = [
        _h1("2026-08-01T00:00:00+00:00", 100.0, high=101.0, low=99.0),
        _h1("2026-08-01T01:00:00+00:00", 105.0, high=110.0, low=104.0),
        _h1("2026-08-01T02:00:00+00:00", 103.0, high=106.0, low=95.0),
    ]
    bucket = to_h12(_hourly(*bars), include_partial=True).bars[0]
    assert bucket.open == pytest.approx(100.0)   # first bar's open
    assert bucket.close == pytest.approx(103.0)  # last bar's close
    assert bucket.high == pytest.approx(110.0)
    assert bucket.low == pytest.approx(95.0)


def test_volume_sums_across_the_bucket():
    h12 = to_h12(_hourly(*_day(1, range(4), volume=2.5)), include_partial=True)
    assert h12.bars[0].volume == pytest.approx(10.0)


def test_volume_is_none_only_when_nothing_in_the_bucket_was_measured():
    h12 = to_h12(_hourly(*_day(1, range(4), volume=None)), include_partial=True)
    assert h12.bars[0].volume is None


def test_a_partly_measured_bucket_sums_what_it_has():
    """Understating volume makes a market look thinner, which makes the gate more cautious.
    Returning None instead would make it decline to judge, which is the less safe direction —
    so the partial sum is preferred to an honest refusal here."""
    bars = [*_day(1, [0, 1], volume=3.0), *_day(1, [2, 3], volume=None)]
    assert to_h12(_hourly(*bars), include_partial=True).bars[0].volume == pytest.approx(6.0)


# ── holes, which are the normal case ─────────────────────────────────────────

def test_empty_buckets_are_never_emitted():
    """Equities do not trade overnight and crypto sources drop candles during outages. A
    bucket with no bars must not appear at all: emitting a flat placeholder would invent a
    price, and ``core.imbalance``'s ATR would then average in ranges of zero and let almost
    anything through as displacement."""
    bars = [*_day(1, [0]), *_day(4, [0])]  # three full days missing in between
    h12 = to_h12(_hourly(*bars), include_partial=True)
    assert [(b.date.day, b.date.hour) for b in h12.bars] == [(1, 0), (4, 0)]


def test_a_bucket_holding_one_hour_still_counts():
    """Thin is not absent. The gate decides what is too thin to trade; the resample's job is
    only to report what was there."""
    h12 = to_h12(_hourly(*_day(1, [7])), include_partial=True)
    assert len(h12.bars) == 1
    assert h12.bars[0].volume == pytest.approx(1.0)


def test_an_empty_series_resamples_to_an_empty_series():
    empty = to_h12(_hourly())
    assert empty.bars == ()
    assert empty.interval == H12


# ── completeness ─────────────────────────────────────────────────────────────

def test_the_forming_bucket_is_dropped_by_default():
    """Same rule ``to_weekly`` uses, and for the same reason: a bucket is only known to be
    finished once a bar from a later one exists. A clock-based test would be wrong for
    equities, whose evening bucket never sees a 23:00 bar at all."""
    h12 = to_h12(_hourly(*_day(1, range(12)), *_day(1, [12, 13])))
    assert [b.date.hour for b in h12.bars] == [0]


def test_include_partial_keeps_the_bucket_still_forming():
    h12 = to_h12(_hourly(*_day(1, range(12)), *_day(1, [12, 13])), include_partial=True)
    assert [b.date.hour for b in h12.bars] == [0, 12]


def test_a_single_forming_bucket_resamples_to_nothing_by_default():
    assert to_h12(_hourly(*_day(1, [0, 1]))).bars == ()


# ── identity and timezone ────────────────────────────────────────────────────

def test_the_result_is_labelled_h12_and_keeps_its_provenance():
    h12 = to_h12(_hourly(*_day(1, range(12))), include_partial=True)
    assert h12.interval == H12
    assert (h12.symbol, h12.source) == ("BTC-USD", "coinbase")


def test_buckets_are_anchored_to_utc_whatever_the_source_offset():
    """A source stamping in New York time must not bucket on New York midnight, or the same
    instrument would carve into different twelve-hour periods depending on who served it."""
    ny = timezone(timedelta(hours=-4))
    bars = (
        IntradayBar(date=datetime(2026, 8, 1, 9, tzinfo=ny),   # 13:00 UTC
                    open=1, high=1, low=1, close=1, volume=1.0),
        IntradayBar(date=datetime(2026, 8, 1, 6, tzinfo=ny),   # 10:00 UTC
                    open=2, high=2, low=2, close=2, volume=1.0),
    )
    h12 = to_h12(IntradaySeries(symbol="X", source="s", interval=H1, bars=bars),
                 include_partial=True)
    assert [b.date for b in h12.bars] == [
        datetime(2026, 8, 1, 0, tzinfo=UTC), datetime(2026, 8, 1, 12, tzinfo=UTC),
    ]


def test_a_us_cash_session_lands_in_exactly_one_bucket():
    """Why H12 costs little on stocks. The regular session is 13:30-20:00 UTC in summer and
    14:30-21:00 in winter, so it sits inside [12:00, 24:00) year-round — one bucket, never
    split across the noon boundary. That holds for a *regular-session* feed such as Yahoo's.
    See the next test for what an extended-hours feed does to the same day."""
    summer = _hourly(*_day(1, range(13, 21)))
    assert [b.date.hour for b in to_h12(summer, include_partial=True).bars] == [12]
    winter = IntradaySeries(symbol="AAPL", source="yahoo", interval=H1, bars=tuple(
        _h1(f"2026-01-15T{h:02d}:00:00+00:00", 100.0) for h in range(14, 22)))
    assert [b.date.hour for b in to_h12(winter, include_partial=True).bars] == [12]


def test_an_extended_hours_feed_splits_an_equity_day_into_two_uneven_buckets():
    """Real AAPL hourly bars, Alpaca SIP, 2026-08-03. The feed runs 08:00-23:00 UTC, so the
    morning bucket is *not* empty — it holds four pre-market hours carrying 926k shares
    against the session bucket's 74.4M, 1.2% of the day on 80x less volume.

    This is why "an equity is basically a daily on H12" is a property of the *feed*, not of
    equities. The resample must not paper over it: a 4-hour pre-market bucket is a real bucket
    and gets reported as one. Deciding whether it should ever have been fetched belongs to
    whatever assembles the series — see docs/IMPROVEMENTS.md §50.
    """
    premarket = [(8, 171_657), (9, 48_144), (10, 229_886), (11, 476_046)]
    session = [(12, 401_378), (13, 8_968_458), (14, 9_171_572), (15, 9_202_381),
               (16, 7_906_059), (17, 5_685_701), (18, 5_009_339), (19, 13_162_761),
               (20, 13_598_841), (21, 1_034_011), (22, 102_239), (23, 144_725)]
    bars = tuple(
        _h1(f"2026-08-03T{h:02d}:00:00+00:00", 305.0, volume=float(v))
        for h, v in premarket + session
    )
    h12 = to_h12(IntradaySeries(symbol="AAPL", source="alpaca", interval=H1, bars=bars),
                 include_partial=True)
    assert [b.date.hour for b in h12.bars] == [0, 12]
    assert h12.bars[0].volume == pytest.approx(925_733)
    assert h12.bars[1].volume == pytest.approx(74_387_465)
    assert h12.bars[1].volume > 75 * h12.bars[0].volume


def test_resampled_bars_still_flow_through_core():
    """The point of the whole type. H12 is where the setup zone is drawn, so ``order_blocks``
    and ``swings`` have to run on these bars, not just on the hourly ones they came from."""
    closes = [100, 101, 105, 101, 100, 99, 95, 99, 100, 104, 108, 103]
    bars = [_h1(f"2026-08-{d:02d}T00:00:00+00:00", float(c)) for d, c in enumerate(closes, 1)]
    h12 = to_h12(_hourly(*bars), include_partial=True)
    found = structure.swings(h12.bars, width=2)
    assert found
    assert all(isinstance(s.date, datetime) for s in found)
