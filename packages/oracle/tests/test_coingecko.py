"""Adapter tests. Payload shapes are copied from real API replies captured 2026-09-16."""
from datetime import UTC, datetime

from oracle.altsignal import coingecko
from oracle.altsignal_config import ProtocolEntry

AT = datetime(2026, 9, 16, 6, 15, tzinfo=UTC)

HYPE = ProtocolEntry(
    asset="HYPE", llama_fees="hyperliquid", llama_tvl="hyperliquid",
    llama_oi=("hyperliquid-perps",), coingecko="hyperliquid",
    coingecko_derivatives=("hyperliquid",), venue="hyperliquid",
)

LIT = ProtocolEntry(
    asset="LIT", llama_fees="lighter", llama_tvl="lighter",
    llama_oi=("lighter-perps", "lighter-robinhood-perps"), coingecko="lighter",
    coingecko_derivatives=("lighter", "robinhood-chain-lighter-futures"), venue="lighter",
)

MARKETS_PAYLOAD = [
    {"id": "bitcoin", "current_price": 76126, "market_cap": 1_528_794_094_497,
     "fully_diluted_valuation": 1_528_795_997_368, "circulating_supply": 20_085_368.0,
     "total_supply": 20_085_400.0},
    {"id": "hyperliquid", "market_cap": 17_393_979_153, "fully_diluted_valuation": 74_699_535_090,
     "circulating_supply": 222_445_714.07, "total_supply": 955_307_079.43},
    {"id": "lighter", "market_cap": 1_101_355_994, "fully_diluted_valuation": 4_405_423_975,
     "circulating_supply": 250_000_000.0, "total_supply": 1_000_000_000.0},
]

DERIVATIVES_PAYLOAD = [
    {"id": "hyperliquid", "open_interest_btc": 187192.38, "trade_volume_24h_btc": "118435.7"},
    {"id": "lighter", "open_interest_btc": 14366.75, "trade_volume_24h_btc": 22550.58},
    {"id": "robinhood-chain-lighter-futures", "open_interest_btc": 5277.66, "trade_volume_24h_btc": 5792.83},
]


def test_parse_market_reads_the_four_economics_fields():
    row = next(r for r in MARKETS_PAYLOAD if r["id"] == "hyperliquid")
    assert coingecko.parse_market(row) == {
        "market_cap": 17_393_979_153,
        "fdv": 74_699_535_090,
        "circulating_supply": 222_445_714.07,
        "total_supply": 955_307_079.43,
    }


def test_fetch_markets_keys_rows_by_coingecko_id():
    def fake_get_json(url, params=None, **kwargs):
        assert params["ids"] == "bitcoin,hyperliquid"
        return MARKETS_PAYLOAD[:2]

    markets = coingecko.fetch_markets(["bitcoin", "hyperliquid"], get_json=fake_get_json)
    assert set(markets) == {"bitcoin", "hyperliquid"}


def test_fetch_markets_skips_the_call_for_an_empty_id_list():
    def raises(*args, **kwargs):
        raise AssertionError("should not be called")

    assert coingecko.fetch_markets([], get_json=raises) == {}


def test_parse_derivatives_row_converts_btc_to_usd_and_coerces_a_string_volume():
    row = {"open_interest_btc": 100.0, "trade_volume_24h_btc": "50.0"}
    oi, volume = coingecko.parse_derivatives_row(row, btc_usd=76126.0)
    assert oi == 100.0 * 76126.0
    assert volume == 50.0 * 76126.0


def test_parse_derivatives_row_is_none_for_missing_fields():
    assert coingecko.parse_derivatives_row({}, btc_usd=76126.0) == (None, None)


def test_fetch_emits_one_reading_per_market_field_per_protocol():
    def fake_get_json(url, params=None, **kwargs):
        return MARKETS_PAYLOAD if "coins/markets" in url else DERIVATIVES_PAYLOAD

    readings = coingecko.fetch([HYPE], get_json=fake_get_json, observed_at=AT)
    market_kinds = {r.kind for r in readings if r.key == "hyperliquid" and r.kind in
                    {"market_cap", "fdv", "circulating_supply", "total_supply"}}
    assert market_kinds == {"market_cap", "fdv", "circulating_supply", "total_supply"}
    assert all(r.source == "coingecko" for r in readings)


def test_fetch_converts_open_interest_and_volume_using_btcs_own_current_price():
    def fake_get_json(url, params=None, **kwargs):
        return MARKETS_PAYLOAD if "coins/markets" in url else DERIVATIVES_PAYLOAD

    readings = coingecko.fetch([HYPE], get_json=fake_get_json, observed_at=AT)
    oi = next(r for r in readings if r.kind == "open_interest" and r.key == "hyperliquid")
    assert oi.value == 187192.38 * 76126


def test_fetch_emits_one_reading_per_derivatives_id_never_presummed():
    def fake_get_json(url, params=None, **kwargs):
        return MARKETS_PAYLOAD if "coins/markets" in url else DERIVATIVES_PAYLOAD

    readings = coingecko.fetch([LIT], get_json=fake_get_json, observed_at=AT)
    oi_readings = [r for r in readings if r.kind == "open_interest"]
    assert {r.key for r in oi_readings} == {"lighter", "robinhood-chain-lighter-futures"}


def test_fetch_skips_derivatives_entirely_when_btc_price_is_unavailable():
    def fake_get_json(url, params=None, **kwargs):
        if "coins/markets" in url:
            return [row for row in MARKETS_PAYLOAD if row["id"] != "bitcoin"]
        raise AssertionError("derivatives should not be fetched without a BTC price")

    readings = coingecko.fetch([HYPE], get_json=fake_get_json, observed_at=AT)
    assert not any(r.kind in {"open_interest", "volume_24h"} for r in readings)
    assert any(r.kind == "market_cap" for r in readings)


def test_fetch_yields_no_reading_for_a_protocol_absent_from_the_response():
    def fake_get_json(url, params=None, **kwargs):
        return [row for row in MARKETS_PAYLOAD if row["id"] == "bitcoin"] if "coins/markets" in url else []

    readings = coingecko.fetch([HYPE], get_json=fake_get_json, observed_at=AT)
    assert readings == []
