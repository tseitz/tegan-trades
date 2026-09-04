"""Adapter tests. Payload shapes are copied from real API replies captured 2026-09-03."""
from datetime import UTC, datetime

from oracle.altsignal import defillama

AT = datetime(2026, 9, 3, 6, 15, tzinfo=UTC)

CHAIN_TVL_PAYLOAD = [
    {"date": 1788307200, "tvl": 5647944308},
    {"date": 1788393600, "tvl": 5753178429},
    {"date": 1788480000, "tvl": 5927196263},
]

STABLECOIN_PAYLOAD = [
    {"date": "1788393600", "totalCirculatingUSD": {"peggedUSD": 16036784393}},
    {"date": "1788480000", "totalCirculatingUSD": {"peggedUSD": 16537787080.98}},
]

DEX_VOLUME_PAYLOAD = {
    "total24h": 2373588819.8,
    "total48hto24h": 2289285889.32,
    "chain": "Solana",
}


def test_chain_tvl_takes_the_latest_point():
    assert defillama.parse_chain_tvl(CHAIN_TVL_PAYLOAD) == 5927196263


def test_chain_tvl_empty_payload_is_none():
    assert defillama.parse_chain_tvl([]) is None


def test_stablecoin_supply_takes_the_latest_usd_total():
    assert defillama.parse_stablecoin_supply(STABLECOIN_PAYLOAD) == 16537787080.98


def test_dex_volume_reads_total24h():
    assert defillama.parse_dex_volume(DEX_VOLUME_PAYLOAD) == 2373588819.8


def test_fetch_builds_one_reading_per_metric_per_chain():
    def fake_get_json(url, *args, **kwargs):
        if "historicalChainTvl" in url:
            return CHAIN_TVL_PAYLOAD
        if "stablecoincharts" in url:
            return STABLECOIN_PAYLOAD
        if "overview/dexs" in url:
            return DEX_VOLUME_PAYLOAD
        raise AssertionError(f"unexpected URL {url}")

    readings = defillama.fetch(["solana"], get_json=fake_get_json, observed_at=AT)
    kinds = {r.kind for r in readings}
    assert kinds == {"chain_tvl", "stablecoin_supply", "dex_volume"}
    assert all(r.source == "defillama" and r.key == "solana" for r in readings)


def test_fetch_skips_a_chain_whose_metric_came_back_empty_rather_than_dropping_the_others():
    def fake_get_json(url, *args, **kwargs):
        if "historicalChainTvl" in url:
            return []
        if "stablecoincharts" in url:
            return STABLECOIN_PAYLOAD
        if "overview/dexs" in url:
            return DEX_VOLUME_PAYLOAD
        raise AssertionError(f"unexpected URL {url}")

    readings = defillama.fetch(["solana"], get_json=fake_get_json, observed_at=AT)
    assert {r.kind for r in readings} == {"stablecoin_supply", "dex_volume"}
