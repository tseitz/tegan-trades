"""Adapter tests for open interest. Payload shapes mirror test_funding_sources.py."""
from datetime import UTC, datetime

from oracle.http import FetchError
from oracle.sources import aster, hyperliquid, lighter

AT = datetime(2026, 9, 13, 11, 0, tzinfo=UTC)


# ── Hyperliquid ──────────────────────────────────────────────────────────────

HL_CTXS = [
    {
        "universe": [
            {"name": "BTC"},
            {"name": "MATIC", "isDelisted": True},
            {"name": "ETH"},
        ]
    },
    [
        {"funding": "0.000001191", "markPx": "64899.0", "openInterest": "10.0", "dayNtlVlm": "500000.0"},
        {"funding": "0.0000125", "markPx": "0.2", "openInterest": "1000.0", "dayNtlVlm": "1000.0"},
        {"funding": "0.0000125", "markPx": "1943.1", "openInterest": "20.0", "dayNtlVlm": "300000.0"},
    ],
]


def test_notional_is_base_units_times_mark_not_the_raw_figure():
    (btc,) = [r for r in hyperliquid.parse_open_interest(HL_CTXS, observed_at=AT) if r.symbol == "BTC"]
    assert btc.notional == 10.0 * 64899.0
    assert btc.volume_24h == 500000.0


def test_delisted_markets_are_dropped_from_open_interest_too():
    symbols = {r.symbol for r in hyperliquid.parse_open_interest(HL_CTXS, observed_at=AT)}
    assert "MATIC" not in symbols


def test_hip3_dex_is_recorded_the_same_way_as_funding():
    payload = [
        {"universe": [{"name": "xyz:NVDA"}]},
        [{"openInterest": "5.0", "markPx": "100.0", "dayNtlVlm": "1000.0"}],
    ]
    (reading,) = hyperliquid.parse_open_interest(payload, dex="xyz", observed_at=AT)
    assert reading.symbol == "NVDA"
    assert reading.venue == "hyperliquid:xyz"


def test_a_context_missing_any_field_is_skipped():
    payload = [
        {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
        [{"markPx": "1"}, {"openInterest": "1.0", "markPx": "1.0", "dayNtlVlm": "1.0"}],
    ]
    readings = hyperliquid.parse_open_interest(payload, observed_at=AT)
    assert [r.symbol for r in readings] == ["ETH"]


def test_fetch_snapshot_reuses_the_same_payload_for_both_parsers():
    calls: list[dict] = []

    def fake_post(_url, body):
        calls.append(body)
        if body.get("type") == "perpDexs":
            return [None, {"name": "xyz"}]
        return HL_CTXS

    rates, interest = hyperliquid.fetch_snapshot(post_json=fake_post, observed_at=AT)
    assert len(rates) == len(interest) == 4  # 2 core-book markets + 2 on the xyz dex
    # One metaAndAssetCtxs call per dex (core + xyz) plus one perpDexs call -- not doubled
    # for fetching funding and open interest separately.
    assert len(calls) == 3


# ── Lighter ──────────────────────────────────────────────────────────────────

LIGHTER_OI_PAYLOAD = {
    "order_book_details": [
        {
            "symbol": "BTC",
            "market_type": "perp",
            "open_interest": 10.0,
            "mark_price": 64899.0,
            "daily_quote_token_volume": 500000.0,
        },
        {
            "symbol": "BTC-SPOT",
            "market_type": "spot",
            "open_interest": 999.0,
            "mark_price": 1.0,
            "daily_quote_token_volume": 1.0,
        },
    ]
}


def test_spot_rows_are_dropped_only_perp_markets_carry_open_interest():
    readings = lighter.parse_open_interest(LIGHTER_OI_PAYLOAD, observed_at=AT)
    assert {r.symbol for r in readings} == {"BTC"}


def test_lighter_notional_is_base_units_times_mark():
    (btc,) = lighter.parse_open_interest(LIGHTER_OI_PAYLOAD, observed_at=AT)
    assert btc.notional == 10.0 * 64899.0
    assert btc.volume_24h == 500000.0  # already USD, unlike open_interest


def test_empty_lighter_open_interest_payload_is_not_an_error():
    assert lighter.parse_open_interest(None, observed_at=AT) == []
    assert lighter.parse_open_interest({"order_book_details": []}, observed_at=AT) == []


# ── Aster ────────────────────────────────────────────────────────────────────

ASTER_TICKERS = [
    {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
    {"symbol": "龙虾USDT", "quoteVolume": "500"},
    {"symbol": "BROKENUSDT", "quoteVolume": "2000"},
]

ASTER_MARKS = [
    {"symbol": "BTCUSDT", "markPrice": "65000.0"},
    {"symbol": "龙虾USDT", "markPrice": "0.001"},
    {"symbol": "BROKENUSDT", "markPrice": "1.0"},
]


def _fake_get(url, params=None):
    if url.endswith("/ticker/24hr"):
        return ASTER_TICKERS
    if url.endswith("/premiumIndex"):
        return ASTER_MARKS
    if url.endswith("/openInterest"):
        assert params is not None
        symbol = params["symbol"]
        if symbol == "BROKENUSDT":
            raise FetchError("400 for BROKENUSDT")
        # Raw, un-encoded symbol -- `requests` percent-encodes `params` values itself, so a
        # caller that pre-quotes `龙虾USDT` before handing it to `get_json` would double-encode
        # it and this lookup would miss, silently sending the request nowhere useful.
        oi_by_symbol = {"BTCUSDT": "10.0", "龙虾USDT": "50000.0"}
        return {"openInterest": oi_by_symbol[symbol]}
    raise AssertionError(f"unexpected url {url}")


def test_non_ascii_symbol_survives_as_the_raw_string_not_pre_encoded():
    readings, _ = aster.fetch_open_interest(get_json=_fake_get, observed_at=AT)
    assert "龙虾USDT" in {r.symbol for r in readings}


def test_a_refusing_symbol_lowers_coverage_rather_than_shrinking_the_total_silently():
    readings, coverage = aster.fetch_open_interest(get_json=_fake_get, observed_at=AT)
    # BROKENUSDT's 2000 volume never enters the numerator, and the denominator is the full
    # 1,002,500 across all three tickers -- so a caller can tell resolution was partial.
    assert {r.symbol for r in readings} == {"BTCUSDT", "龙虾USDT"}
    assert coverage < 1.0


def test_notional_multiplies_base_units_by_the_matched_mark():
    readings, _ = aster.fetch_open_interest(get_json=_fake_get, observed_at=AT)
    (btc,) = [r for r in readings if r.symbol == "BTCUSDT"]
    assert btc.notional == 10.0 * 65000.0


def test_empty_ticker_list_yields_no_readings_and_zero_coverage():
    readings, coverage = aster.fetch_open_interest(
        get_json=lambda _url, params=None: [] if params is None else None, observed_at=AT
    )
    assert readings == []
    assert coverage == 0.0
