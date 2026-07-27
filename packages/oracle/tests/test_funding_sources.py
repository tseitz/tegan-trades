"""Adapter tests. Payload shapes are copied from real API replies captured 2026-07-27."""
from datetime import UTC, datetime

import pytest

from oracle.sources import aster, hyperliquid, lighter

AT = datetime(2026, 7, 27, 20, 36, tzinfo=UTC)


# ── Hyperliquid ──────────────────────────────────────────────────────────────

HL_CTXS = [
    {
        "universe": [
            {"name": "BTC", "maxLeverage": 40},
            {"name": "MATIC", "maxLeverage": 20, "isDelisted": True},
            {"name": "ETH", "maxLeverage": 25},
        ]
    },
    [
        {"funding": "0.000001191", "markPx": "64899.0"},
        {"funding": "0.0000125", "markPx": "0.2"},
        {"funding": "0.0000125", "markPx": "1943.1"},
    ],
]


def test_core_book_parses_and_is_marked_hourly():
    rates = hyperliquid.parse_asset_ctxs(HL_CTXS, observed_at=AT)
    assert [r.symbol for r in rates] == ["BTC", "ETH"]
    assert all(r.interval_hours == 1.0 for r in rates)
    assert all(r.venue == "hyperliquid" for r in rates)


def test_delisted_markets_are_dropped():
    assert "MATIC" not in {r.symbol for r in hyperliquid.parse_asset_ctxs(HL_CTXS, observed_at=AT)}


def test_hip3_namespace_is_split_but_the_dex_survives_in_the_venue():
    payload = [
        {"universe": [{"name": "xyz:NVDA"}]},
        [{"funding": "0.00002546"}],
    ]
    (rate,) = hyperliquid.parse_asset_ctxs(payload, dex="xyz", observed_at=AT)
    # The bare ticker is stored; which builder listed it is not thrown away, because two
    # deployers can list the same ticker against different oracles.
    assert rate.symbol == "NVDA"
    assert rate.venue == "hyperliquid:xyz"


def test_hourly_rate_annualizes_to_the_observed_nvda_reading():
    payload = [{"universe": [{"name": "xyz:NVDA"}]}, [{"funding": "0.00002546"}]]
    (rate,) = hyperliquid.parse_asset_ctxs(payload, dex="xyz", observed_at=AT)
    assert rate.annualized == pytest.approx(0.223, abs=1e-3)


def test_a_context_without_funding_is_skipped_not_zeroed():
    payload = [{"universe": [{"name": "BTC"}, {"name": "ETH"}]}, [{"markPx": "1"}, {"funding": "0.001"}]]
    rates = hyperliquid.parse_asset_ctxs(payload, observed_at=AT)
    assert [r.symbol for r in rates] == ["ETH"]


def test_unparseable_funding_is_skipped():
    payload = [{"universe": [{"name": "BTC"}]}, [{"funding": "n/a"}]]
    assert hyperliquid.parse_asset_ctxs(payload, observed_at=AT) == []


def test_malformed_top_level_payloads_yield_nothing():
    for bad in (None, [], [{"universe": []}]):
        assert hyperliquid.parse_asset_ctxs(bad, observed_at=AT) == []


def test_universe_longer_than_contexts_truncates_rather_than_raising():
    payload = [{"universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "SOL"}]}, [{"funding": "0.001"}]]
    assert len(hyperliquid.parse_asset_ctxs(payload, observed_at=AT)) == 1


def test_perp_dexs_skips_the_null_core_entry():
    assert hyperliquid.parse_dexs([None, {"name": "xyz"}, {"name": "flx"}]) == ["xyz", "flx"]


def test_funding_history_rows_become_timestamped_observations():
    payload = [
        {"coin": "xyz:NVDA", "fundingRate": "0.00000625", "time": 1782594000058},
        {"coin": "xyz:NVDA", "fundingRate": "-0.000002004", "time": 1782601200040},
    ]
    rates = hyperliquid.parse_funding_history(payload, venue="hyperliquid:xyz")
    assert [r.symbol for r in rates] == ["NVDA", "NVDA"]
    assert rates[1].rate < 0
    # Distinct settlement times -- the whole point of a backfill over a snapshot.
    assert rates[0].observed_at != rates[1].observed_at
    assert all(r.interval_hours == 1.0 for r in rates)


# ── Lighter ──────────────────────────────────────────────────────────────────

LIGHTER_PAYLOAD = {
    "code": 200,
    "funding_rates": [
        {"market_id": 1, "exchange": "lighter", "symbol": "BTC", "rate": 2.4e-05},
        {"market_id": 1, "exchange": "binance", "symbol": "BTC", "rate": 7.409e-05},
        {"market_id": 1, "exchange": "bybit", "symbol": "BTC", "rate": 4.705e-05},
        {"market_id": 1, "exchange": "hyperliquid", "symbol": "BTC", "rate": 9.812e-06},
        {"market_id": 164, "exchange": "lighter", "symbol": "NVDA", "rate": 3.2e-05},
    ],
}


def test_only_lighters_own_column_is_kept():
    rates = lighter.parse_funding_rates(LIGHTER_PAYLOAD, observed_at=AT)
    # The feed also carries Lighter's opinion of three competitors. Those are second-hand
    # and would corrupt a log whose purpose is measuring real carry.
    assert {r.symbol for r in rates} == {"BTC", "NVDA"}
    assert all(r.venue == "lighter" for r in rates)


def test_lighter_is_marked_eight_hourly_not_hourly():
    # Established empirically: Lighter's published Hyperliquid rate is exactly 8.000x
    # Hyperliquid's own hourly figure, so this feed's convention is 8-hourly.
    rates = lighter.parse_funding_rates(LIGHTER_PAYLOAD, observed_at=AT)
    assert all(r.interval_hours == 8.0 for r in rates)


def test_the_eight_hour_reading_of_nvda_is_a_single_digit_annual_rate():
    # Reading this 3.2e-05 as hourly gives 28%/yr; as 8-hourly it is 3.5%. An 8x error on
    # a carry term is the difference between a setup clearing R:R and failing it.
    (nvda,) = [r for r in lighter.parse_funding_rates(LIGHTER_PAYLOAD, observed_at=AT) if r.symbol == "NVDA"]
    assert nvda.annualized == pytest.approx(0.035, abs=1e-3)


def test_empty_lighter_payload_is_not_an_error():
    assert lighter.parse_funding_rates(None, observed_at=AT) == []
    assert lighter.parse_funding_rates({"funding_rates": []}, observed_at=AT) == []


# ── Aster ────────────────────────────────────────────────────────────────────

ASTER_INFO = [
    {"symbol": "BTCUSDT", "fundingIntervalHours": 8},
    {"symbol": "XAUUSDT", "fundingIntervalHours": 4},
    {"symbol": "SUSHIUSDT", "fundingIntervalHours": 1},
    {"symbol": "BROKENUSDT", "fundingIntervalHours": 0},
]

ASTER_PREMIUM = [
    {"symbol": "BTCUSDT", "lastFundingRate": "0.00004977"},
    {"symbol": "XAUUSDT", "lastFundingRate": "0.00000000"},
    {"symbol": "SUSHIUSDT", "lastFundingRate": "0.00010000"},
    {"symbol": "NEWUSDT", "lastFundingRate": "0.00002000"},
]


def test_intervals_are_read_per_symbol_because_the_venue_runs_four_schedules():
    intervals = aster.parse_funding_info(ASTER_INFO)
    assert intervals["BTCUSDT"] == 8.0
    assert intervals["XAUUSDT"] == 4.0   # gold settles twice as often as bitcoin here
    assert intervals["SUSHIUSDT"] == 1.0
    assert "BROKENUSDT" not in intervals  # a zero interval would divide by zero downstream


def test_each_rate_carries_its_own_symbols_interval():
    rates, _ = aster.parse_premium_index(ASTER_PREMIUM, aster.parse_funding_info(ASTER_INFO), observed_at=AT)
    by = {r.symbol: r for r in rates}
    assert by["BTCUSDT"].interval_hours == 8.0
    assert by["XAUUSDT"].interval_hours == 4.0
    assert by["SUSHIUSDT"].interval_hours == 1.0


def test_a_symbol_missing_from_funding_info_is_defaulted_and_counted_not_dropped():
    rates, defaulted = aster.parse_premium_index(
        ASTER_PREMIUM, aster.parse_funding_info(ASTER_INFO), observed_at=AT
    )
    assert defaulted == 1
    by = {r.symbol: r for r in rates}
    assert by["NEWUSDT"].interval_hours == aster.DEFAULT_INTERVAL_HOURS


def test_identical_rates_on_different_intervals_annualize_differently():
    rates, _ = aster.parse_premium_index(
        [{"symbol": "A", "lastFundingRate": "0.0001"}, {"symbol": "B", "lastFundingRate": "0.0001"}],
        {"A": 1.0, "B": 8.0},
        observed_at=AT,
    )
    a, b = rates
    assert a.annualized == pytest.approx(8 * b.annualized)


def test_aster_equity_perps_read_as_genuinely_zero():
    rates, _ = aster.parse_premium_index(
        [{"symbol": "NVDAUSDT", "lastFundingRate": "0.00000000"}], {"NVDAUSDT": 8.0}, observed_at=AT
    )
    assert rates[0].annualized == 0.0


def test_aster_history_applies_the_interval_it_is_given():
    payload = [
        {"symbol": "NVDAUSDT", "fundingTime": 1785052800005, "fundingRate": "0.00000000"},
        {"symbol": "NVDAUSDT", "fundingTime": 1785081600000, "fundingRate": "0.00001000"},
    ]
    rates = aster.parse_funding_history(payload, 8.0)
    assert len(rates) == 2
    assert all(r.interval_hours == 8.0 for r in rates)
    assert rates[0].observed_at < rates[1].observed_at


def test_empty_aster_payloads_are_not_errors():
    assert aster.parse_funding_info(None) == {}
    assert aster.parse_premium_index(None, {}, observed_at=AT) == ([], 0)
