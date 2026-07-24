"""Source adapters, exercised against recorded fixtures of the real APIs.

No test here touches the network — fixtures were captured live once (see
`tests/fixtures/`) so the parsers stay pinned to the shapes the APIs actually return.
"""
import json
from datetime import date
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
