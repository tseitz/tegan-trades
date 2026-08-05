"""Source adapters, exercised against recorded fixtures of the real APIs.

No test here touches the network — fixtures were captured live once (see
`tests/fixtures/`) so the parsers stay pinned to the shapes the APIs actually return.
"""
import json
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from oracle.sources import coinbase, kraken, yahoo

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


# ── Coinbase ────────────────────────────────────────────────────────────────

def test_coinbase_parses_candles_into_bars():
    # Coinbase rows are [time, low, high, open, close, volume] — a column order that
    # differs from every other source, so a mix-up here is silent and catastrophic.
    bars = coinbase.parse_candles(_fixture("coinbase_btc_daily"))
    by_date = {b.date: b for b in bars}
    aug5 = by_date[date(2024, 8, 5)]  # the yen-carry crash day
    assert aug5.low == pytest.approx(49050.01)
    assert aug5.high == pytest.approx(58282.83)
    assert aug5.open == pytest.approx(58131.31)
    assert aug5.close == pytest.approx(54029.12)
    assert aug5.low < aug5.open and aug5.high >= aug5.close


def test_coinbase_parse_is_order_agnostic():
    """The API returns newest-first; PriceSeries sorts, but the parser must not assume."""
    payload = _fixture("coinbase_btc_daily")
    assert coinbase.parse_candles(payload) and coinbase.parse_candles(list(reversed(payload)))


def test_coinbase_paginates_in_max_candle_chunks():
    """300-candle cap per request — a 2y backfill silently truncates without chunking."""
    seen = []

    def fake_get(url, params):
        seen.append((params["start"], params["end"]))
        return []

    coinbase.fetch_daily("BTC-USD", date(2024, 1, 1), date(2026, 7, 1), get_json=fake_get)
    assert len(seen) >= 3, f"expected >=3 chunks for ~900 days, got {len(seen)}"
    # windows must tile forward without gaps
    assert seen == sorted(seen)


def test_coinbase_symbol_for_builds_usd_pair():
    assert coinbase.symbol_for("BTC") == "BTC-USD"


def test_coinbase_intraday_parse_keeps_the_volume_the_daily_parse_throws_away():
    """Column 5 is real and has always been arriving — ``parse_candles`` drops it because
    ``oracle.series.Bar`` has nowhere to put it. The trigger's gate needs it, so the intraday
    parse keeps it. Asserted against the recorded daily fixture, since the payload shape is
    identical across granularities and this is the only fixture captured from the live API."""
    bars = coinbase.parse_intraday_candles(_fixture("coinbase_btc_daily"))
    by_stamp = {b.date: b for b in bars}
    aug5 = by_stamp[datetime(2024, 8, 5, tzinfo=UTC)]
    assert aug5.close == pytest.approx(54029.12)
    # The yen-carry crash day: 59,125 BTC against an 8-21k baseline either side of it. Reading
    # any neighbouring column would land inside that baseline and look entirely plausible.
    assert aug5.volume == pytest.approx(59125.78926672)
    assert aug5.volume > 2.5 * max(b.volume for b in bars if b.date != aug5.date)


def test_coinbase_intraday_stamps_are_utc_aware_datetimes_not_dates():
    """Two candles inside one day must stay distinct — truncating to a date, as the daily
    parse does, would collapse all 24 of them onto one bar."""
    payload = [[1754006400, 99.0, 101.0, 100.0, 100.5, 7.0],
               [1754010000, 100.0, 102.0, 100.5, 101.5, 8.0]]
    bars = coinbase.parse_intraday_candles(payload)
    assert len(bars) == 2
    assert bars[0].date == datetime(2025, 8, 1, 0, tzinfo=UTC)
    assert bars[1].date == datetime(2025, 8, 1, 1, tzinfo=UTC)
    assert all(b.date.tzinfo is not None for b in bars)


def test_coinbase_intraday_parse_tolerates_a_missing_volume_column():
    """Unmeasured is not zero — a short row must not be read as a market that never traded."""
    assert coinbase.parse_intraday_candles([[1754006400, 99.0, 101.0, 100.0, 100.5]])[0].volume is None


def test_coinbase_intraday_paginates_in_hours_not_days():
    """The 300-candle cap is counted in *candles*, so at hourly granularity one request covers
    12.5 days, not 300. Reusing the daily step would ask for 7,200 candles and get a silently
    truncated window back — the same failure ``fetch_daily`` chunks to avoid."""
    seen = []

    def fake_get(url, params):
        seen.append((params["start"], params["end"], params["granularity"]))
        return []

    start = datetime(2025, 8, 1, tzinfo=UTC)
    coinbase.fetch_intraday("BTC-USD", start, start + timedelta(days=50), get_json=fake_get)
    assert len(seen) >= 4, f"expected >=4 chunks for 50 days of hourly, got {len(seen)}"
    assert {g for _, _, g in seen} == {coinbase.GRANULARITY_HOURLY}
    assert seen == sorted(seen)


def test_coinbase_intraday_windows_tile_without_gaps_or_overlap():
    """A gap loses an hour outright; an overlap is harmless but means paying for candles
    twice against a rate limit that is the binding constraint on a backfill."""
    seen = []

    def fake_get(url, params):
        seen.append((datetime.fromisoformat(params["start"]), datetime.fromisoformat(params["end"])))
        return []

    start = datetime(2025, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=30)
    coinbase.fetch_intraday("BTC-USD", start, end, get_json=fake_get)
    assert seen[0][0] == start
    assert seen[-1][1] == end
    for (_, prev_end), (next_start, _) in pairwise(seen):
        assert next_start - prev_end == timedelta(hours=1)


def test_coinbase_intraday_granularity_is_a_parameter_for_the_15m_trigger():
    """M15 is the stated ideal trigger timeframe; H1 is where we start. The step must follow
    the granularity, or a 15-minute fetch would tile in hourly strides and lose 3 of every 4
    candles it asked for."""
    seen = []

    def fake_get(url, params):
        seen.append((datetime.fromisoformat(params["start"]), datetime.fromisoformat(params["end"])))
        return []

    start = datetime(2025, 8, 1, tzinfo=UTC)
    coinbase.fetch_intraday("BTC-USD", start, start + timedelta(days=30),
                            granularity=900, get_json=fake_get)
    assert seen[0][1] - seen[0][0] == timedelta(minutes=15) * (coinbase.MAX_CANDLES - 1)


def test_coinbase_intraday_returns_intraday_bars_from_a_real_payload():
    payload = [[1754006400, 99.0, 101.0, 100.0, 100.5, 7.0]]
    bars = coinbase.fetch_intraday(
        "BTC-USD", datetime(2025, 8, 1, tzinfo=UTC), datetime(2025, 8, 1, 1, tzinfo=UTC),
        get_json=lambda url, params: payload,
    )
    assert bars[0].volume == pytest.approx(7.0)


# ── Yahoo ───────────────────────────────────────────────────────────────────

def test_yahoo_parses_chart_into_bars():
    bars = yahoo.parse_chart(_fixture("yahoo_tsla_daily"))
    assert bars
    assert all(b.close is not None for b in bars)
    assert bars[0].date < bars[-1].date or len(bars) == 1


def test_yahoo_unknown_symbol_returns_empty_not_raise():
    """`result: null` is how Yahoo reports a bad ticker. The long tail of the corpus is
    bare tickers, so this path is hit routinely and must degrade, not explode."""
    assert yahoo.parse_chart(_fixture("yahoo_unknown_symbol")) == []


def test_yahoo_instrument_type_exposed_for_validation():
    """Routing validates bare tickers by asserting Yahoo agrees it's an EQUITY/ETF/etc."""
    assert yahoo.instrument_type(_fixture("yahoo_tsla_daily")) == "EQUITY"
    assert yahoo.instrument_type(_fixture("yahoo_unknown_symbol")) is None


def test_yahoo_skips_rows_with_null_prices():
    """Yahoo pads holidays with null OHLC entries rather than omitting the timestamp."""
    payload = _fixture("yahoo_tsla_daily")
    quote = payload["chart"]["result"][0]["indicators"]["quote"][0]
    quote["close"][0] = None
    bars = yahoo.parse_chart(payload)
    assert len(bars) == len(quote["close"]) - 1


# ── Kraken ──────────────────────────────────────────────────────────────────

def test_kraken_parses_ohlc_into_bars():
    # Rows are [time, open, high, low, close, vwap, volume, count] as *strings*.
    bars = kraken.parse_ohlc(_fixture("kraken_xmr_daily"))
    first = bars[0]
    assert first.date == date(2025, 7, 23)
    assert first.open == pytest.approx(325.39)
    assert first.high == pytest.approx(335.85)
    assert first.low == pytest.approx(308.00)
    assert first.close == pytest.approx(314.14)


def test_kraken_result_key_is_normalized_not_the_requested_pair():
    """Kraken answers XMRUSD under the key 'XXMRZUSD'. Looking up by the requested pair
    name returns nothing — the parser must take whichever non-'last' key is present."""
    payload = _fixture("kraken_xmr_daily")
    assert "XMRUSD" not in payload["result"]
    assert kraken.parse_ohlc(payload)


def test_kraken_error_payload_returns_empty():
    assert kraken.parse_ohlc(_fixture("kraken_error")) == []


def test_kraken_symbol_for_builds_usd_pair():
    assert kraken.symbol_for("XMR") == "XMRUSD"
