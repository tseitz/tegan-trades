"""Adapter tests. Payload shapes are copied from real API replies captured 2026-09-03/16."""
from datetime import UTC, datetime, timedelta

from oracle.altsignal import defillama
from oracle.altsignal_config import ProtocolEntry
from oracle.http import FetchError

AT = datetime(2026, 9, 3, 6, 15, tzinfo=UTC)
NOW = datetime(2026, 9, 16, tzinfo=UTC)

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


# ------------------------------------------------------------------------- protocol-level

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


def test_llama_total_reads_total30d():
    assert defillama._llama_total("hyperliquid", "dailyFees", get_json=lambda *a, **kw: {"total30d": 63385097}) == 63385097


def test_llama_total_is_none_when_the_protocol_has_no_adapter_for_it():
    def raises(*args, **kwargs):
        raise FetchError("400 for dailyRevenue")

    assert defillama._llama_total("aster-perps", "dailyRevenue", get_json=raises) is None


def test_parse_protocol_tvl_is_none_for_an_empty_body():
    assert defillama.parse_protocol_tvl("") is None
    assert defillama.parse_protocol_tvl(6668380330.53) == 6668380330.53


def test_parse_open_interest_reads_total24h_and_category():
    payload = {"total24h": 6803065137, "category": "Derivatives"}
    assert defillama.parse_open_interest(payload) == (6803065137, "Derivatives")


def test_parse_open_interest_is_none_for_a_non_dict_payload():
    assert defillama.parse_open_interest(None) == (None, None)


def _unlock_payload(*, now: datetime, tbd_amount: float) -> dict:
    """One allocation section whose series extends past ``now`` -- the Lighter trap: the
    series' last point sits in the future, and every computed figure must ignore points after
    the reference time except ``documented_end``, which reports the series' true last point."""
    points = [
        (now - timedelta(days=60), 1000),
        (now - timedelta(days=40), 1000),
        (now - timedelta(days=10), 1500),
        (now + timedelta(days=5), 2000),
        (now + timedelta(days=120), 5000),
    ]
    return {
        "documentedData": {
            "data": [
                {
                    "label": "Team",
                    "data": [{"timestamp": int(ts.timestamp()), "unlocked": u} for ts, u in points],
                }
            ]
        },
        "supplyMetrics": {"tbdAmount": tbd_amount},
    }


def test_parse_unlock_schedule_takes_the_last_point_at_or_before_now_never_the_series_end():
    schedule = defillama.parse_unlock_schedule(_unlock_payload(now=NOW, tbd_amount=250_000_000), now=NOW)
    assert schedule["unlocked_today"] == 1500
    assert schedule["unlocked_30d"] == 500       # 1500 - 1000 (the point 40d ago)
    assert schedule["scheduled_90d"] == 500       # 2000 (the +5d point) - 1500
    assert schedule["tbd_amount"] == 250_000_000
    # documented_end is the series' TRUE last point (+120d), not clamped to `now` -- this is
    # the field that is allowed to look into the future; everything else above must not.
    assert schedule["documented_end"] == (NOW + timedelta(days=120)).isoformat()


def test_parse_unlock_schedule_is_none_with_no_documented_data():
    assert defillama.parse_unlock_schedule({}, now=NOW) is None
    assert defillama.parse_unlock_schedule(None, now=NOW) is None


def test_parse_unlock_schedule_scores_a_tranche_that_has_not_started_as_zero_scheduled():
    # Lighter's Team/Investors allocations sit at zero until a start date months out. If that
    # start date falls beyond the 90-day lookahead window, every point in the section is AFTER
    # the window -- the section must contribute 0 to `scheduled_90d`, never its eventual total.
    future_only = {
        "documentedData": {
            "data": [{
                "label": "Investors",
                "data": [
                    {"timestamp": int((NOW + timedelta(days=105)).timestamp()), "unlocked": 0},
                    {"timestamp": int((NOW + timedelta(days=140)).timestamp()), "unlocked": 1_661_000},
                ],
            }]
        },
        "supplyMetrics": {"tbdAmount": 0},
    }
    schedule = defillama.parse_unlock_schedule(future_only, now=NOW)
    assert schedule["unlocked_today"] == 0
    assert schedule["scheduled_90d"] == 0


def _fetch_protocols_router(*, revenue_fails: bool):
    def fake_get_json(url, params=None, **kwargs):
        params = params or {}
        if "summary/fees/hyperliquid" in url:
            if params.get("dataType") == "dailyRevenue" and revenue_fails:
                raise FetchError("400 for dailyRevenue")
            return {"total30d": 63385097}
        if "summary/fees/lighter" in url:
            return {"total30d": 4_168_000}
        if "tvl/hyperliquid" in url:
            return 6668380330.53
        if "tvl/lighter" in url:
            return 620_000_000.0
        if "summary/open-interest/hyperliquid-perps" in url:
            return {"total24h": 6_803_065_137, "category": "Derivatives"}
        if "summary/open-interest/lighter-perps" in url:
            return {"total24h": 543_240_949, "category": "Derivatives"}
        if "summary/open-interest/lighter-robinhood-perps" in url:
            return {"total24h": 199_512_921, "category": "Derivatives"}
        if "emissions/hyperliquid" in url:
            return _unlock_payload(now=NOW, tbd_amount=611_827_353.99)
        if "emissions/lighter" in url:
            return _unlock_payload(now=NOW, tbd_amount=250_000_000)
        raise AssertionError(f"unexpected URL {url} params {params}")

    return fake_get_json


def test_fetch_protocols_reads_every_metric_and_keys_by_defillama_slug():
    readings = defillama.fetch_protocols(
        [HYPE], get_json=_fetch_protocols_router(revenue_fails=False), observed_at=AT
    )
    by_kind = {r.kind: r for r in readings}
    assert by_kind["protocol_fees_30d"].key == "hyperliquid"
    assert by_kind["protocol_fees_30d"].value == 63385097
    assert by_kind["protocol_revenue_30d"].value == 63385097  # same fake payload, still a real value
    assert by_kind["protocol_tvl"].key == "hyperliquid"
    assert by_kind["protocol_open_interest"].key == "hyperliquid-perps"
    assert by_kind["protocol_category"].key == "hyperliquid"  # keyed like protocol_tvl, not the OI slug
    assert by_kind["protocol_category"].value == "Derivatives"
    assert by_kind["unlock_schedule"].key == "hyperliquid"
    assert all(r.source == "defillama" for r in readings)


def test_fetch_protocols_emits_no_revenue_reading_when_defillama_has_no_adapter_for_it():
    readings = defillama.fetch_protocols(
        [HYPE], get_json=_fetch_protocols_router(revenue_fails=True), observed_at=AT
    )
    kinds = {r.kind for r in readings}
    assert "protocol_fees_30d" in kinds
    assert "protocol_revenue_30d" not in kinds


def test_fetch_protocols_emits_one_open_interest_reading_per_llama_oi_slug_never_presummed():
    readings = defillama.fetch_protocols(
        [LIT], get_json=_fetch_protocols_router(revenue_fails=False), observed_at=AT
    )
    oi_readings = [r for r in readings if r.kind == "protocol_open_interest"]
    assert {r.key for r in oi_readings} == {"lighter-perps", "lighter-robinhood-perps"}
    assert {r.value for r in oi_readings} == {543_240_949, 199_512_921}
    # Category is reported once per protocol, from the first llama_oi slug only.
    assert len([r for r in readings if r.kind == "protocol_category"]) == 1
