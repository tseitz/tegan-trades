"""Adapter tests. Payload shapes are copied from real API replies captured 2026-09-03/16."""
from datetime import UTC, datetime, timedelta

from oracle.altsignal import defillama
from oracle.altsignal_config import ProtocolEntry, VenueEntry
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


# ------------------------------------------------------------------------- venue Safety (#73)

AAVE_V3 = VenueEntry(
    venue="aave-v3", llama_protocol="aave-v3",
    llama_pools=("aa70268e-4b52-42bf-a116-608b370f9501",), asset="USDC", chain="Ethereum",
)

SPARKLEND = VenueEntry(
    venue="sparklend", llama_protocol="sparklend",
    llama_pools=("65ce8276-b4d9-41ba-9f6f-21fc374cf9bc",), asset="USDC", chain="Ethereum",
)

PROTOCOLS_PAYLOAD = [
    {"id": "111", "slug": "aave-v3", "audits": "2", "forkedFromIds": None, "hallmarks": None},
    {
        "id": "222", "slug": "sparklend", "audits": "2", "forkedFromIds": ["111"],
        "hallmarks": [[1700000000, "Exploit disclosed"]],
    },
    {"id": "333", "slug": "no-audit-protocol", "audits": "0", "forkedFromIds": None},
    # Deliberately no `audits` key at all — the 0.2% DefiLlama does not report on.
    {"id": "444", "slug": "unread-audit-protocol", "forkedFromIds": None},
]

# A wrong slug -> HTTP 200, EMPTY BODY -> `""`, which parses as no data, never a wrong name.
EMPTY_BODY_PAYLOAD = ""

POOLS_PAYLOAD = {
    "data": [
        {
            "pool": "aa70268e-4b52-42bf-a116-608b370f9501", "project": "aave-v3",
            "apy": 3.59532, "apyBase": 3.59532, "apyReward": None, "sigma": 0.15467,
            "count": 1316, "outlier": False, "stablecoin": True,
        },
        {
            "pool": "65ce8276-b4d9-41ba-9f6f-21fc374cf9bc", "project": "sparklend",
            "apy": 3.54196, "apyBase": 3.54196, "apyReward": None, "sigma": 0.14989,
            "count": 1051, "outlier": False, "stablecoin": True,
        },
        {
            "pool": "unrequested-pool", "project": "aave-v3", "apy": 99.0,
            "apyBase": None, "apyReward": 99.0, "sigma": 0.0, "count": 1, "outlier": True,
            "stablecoin": True,
        },
    ]
}


def test_parse_venue_metadata_reads_the_decision_fields_for_a_requested_slug():
    out = defillama.parse_venue_metadata(PROTOCOLS_PAYLOAD, slugs=["aave-v3"])
    assert out["aave-v3"] == {"audited": True, "forked_from": [], "incidents": 0}


def test_parse_venue_metadata_resolves_forked_from_ids_to_parent_slugs():
    out = defillama.parse_venue_metadata(PROTOCOLS_PAYLOAD, slugs=["sparklend"])
    assert out["sparklend"]["forked_from"] == ["aave-v3"]
    assert out["sparklend"]["incidents"] == 1


def test_parse_venue_metadata_returns_a_record_for_the_resolved_parent_too():
    """AC 2: the parent needs its own age/audit facts, not just the fork's."""
    out = defillama.parse_venue_metadata(PROTOCOLS_PAYLOAD, slugs=["sparklend"])
    assert "aave-v3" in out
    assert out["aave-v3"]["audited"] is True


def test_parse_venue_metadata_no_audit_field_is_none_not_false():
    out = defillama.parse_venue_metadata(PROTOCOLS_PAYLOAD, slugs=["unread-audit-protocol"])
    assert out["unread-audit-protocol"]["audited"] is None


def test_parse_venue_metadata_audits_zero_is_false_not_none():
    out = defillama.parse_venue_metadata(PROTOCOLS_PAYLOAD, slugs=["no-audit-protocol"])
    assert out["no-audit-protocol"]["audited"] is False


def test_parse_venue_metadata_a_protocol_with_no_forked_from_ids_yields_empty_list():
    out = defillama.parse_venue_metadata(PROTOCOLS_PAYLOAD, slugs=["aave-v3"])
    assert out["aave-v3"]["forked_from"] == []


def test_parse_venue_pools_reads_only_requested_pools():
    out = defillama.parse_venue_pools(
        POOLS_PAYLOAD, pool_ids=["aa70268e-4b52-42bf-a116-608b370f9501"]
    )
    assert set(out) == {"aa70268e-4b52-42bf-a116-608b370f9501"}
    assert out["aa70268e-4b52-42bf-a116-608b370f9501"]["apy"] == 3.59532
    assert out["aa70268e-4b52-42bf-a116-608b370f9501"]["count"] == 1316


def test_parse_first_tvl_date_takes_the_first_non_zero_point():
    payload = {
        "tvl": [
            {"date": 1600000000, "totalLiquidityUSD": 0},
            {"date": 1600100000, "totalLiquidityUSD": 0},
            {"date": 1600200000, "totalLiquidityUSD": 51234.5},
            {"date": 1600300000, "totalLiquidityUSD": 90000.0},
        ]
    }
    assert defillama.parse_first_tvl_date(payload) == "2020-09-15"


def test_parse_first_tvl_date_all_zero_points_is_none():
    payload = {"tvl": [{"date": 1600000000, "totalLiquidityUSD": 0}]}
    assert defillama.parse_first_tvl_date(payload) is None


def test_parse_first_tvl_date_empty_body_payload_is_none():
    assert defillama.parse_first_tvl_date(EMPTY_BODY_PAYLOAD) is None


def _fetch_venues_router(*, history_payloads: dict):
    def fake_get_json(url, params=None, **kwargs):
        if url == defillama.PROTOCOLS_BASE:
            return PROTOCOLS_PAYLOAD
        if url == defillama.POOLS_BASE:
            return POOLS_PAYLOAD
        for slug, payload in history_payloads.items():
            if url == f"{defillama.PROTOCOL_HISTORY_BASE}/{slug}":
                return payload
        raise AssertionError(f"unexpected URL {url}")

    return fake_get_json


def test_fetch_venues_writes_safety_facts_age_and_pool_readings():
    history = {
        "aave-v3": {"tvl": [{"date": 1600000000, "totalLiquidityUSD": 90000.0}]},
        "sparklend": {"tvl": [{"date": 1650000000, "totalLiquidityUSD": 12345.0}]},
    }
    readings = defillama.fetch_venues(
        [SPARKLEND], get_json=_fetch_venues_router(history_payloads=history), observed_at=AT
    )
    by_kind = {(r.kind, r.key): r for r in readings}
    assert ("venue_safety_facts", "sparklend") in by_kind
    assert ("venue_safety_facts", "aave-v3") in by_kind  # the resolved parent
    assert ("venue_first_tvl", "sparklend") in by_kind
    assert by_kind[("venue_first_tvl", "sparklend")].value == "2022-04-15"
    assert ("venue_pool", "65ce8276-b4d9-41ba-9f6f-21fc374cf9bc") in by_kind


def test_fetch_venues_skips_the_history_fetch_for_a_slug_already_in_known_ages():
    def fake_get_json(url, params=None, **kwargs):
        if url == defillama.PROTOCOLS_BASE:
            return PROTOCOLS_PAYLOAD
        if url == defillama.POOLS_BASE:
            return POOLS_PAYLOAD
        raise AssertionError(f"history fetch must have been skipped: {url}")

    readings = defillama.fetch_venues(
        [AAVE_V3], get_json=fake_get_json, observed_at=AT, known_ages=frozenset({"aave-v3"})
    )
    kinds = {r.kind for r in readings}
    assert "venue_first_tvl" not in kinds
    assert "venue_safety_facts" in kinds


def test_fetch_venues_a_failed_history_fetch_writes_no_venue_first_tvl_row():
    def fake_get_json(url, params=None, **kwargs):
        if url == defillama.PROTOCOLS_BASE:
            return PROTOCOLS_PAYLOAD
        if url == defillama.POOLS_BASE:
            return POOLS_PAYLOAD
        if url.startswith(defillama.PROTOCOL_HISTORY_BASE):
            raise FetchError("500 for protocol history")
        raise AssertionError(f"unexpected URL {url}")

    readings = defillama.fetch_venues([AAVE_V3], get_json=fake_get_json, observed_at=AT)
    assert not any(r.kind == "venue_first_tvl" for r in readings)
    assert any(r.kind == "venue_safety_facts" for r in readings)


def test_fetch_venues_a_down_pools_endpoint_still_yields_safety_facts():
    def fake_get_json(url, params=None, **kwargs):
        if url == defillama.PROTOCOLS_BASE:
            return PROTOCOLS_PAYLOAD
        if url == defillama.POOLS_BASE:
            raise FetchError("500 for pools")
        if url.startswith(defillama.PROTOCOL_HISTORY_BASE):
            return {"tvl": [{"date": 1600000000, "totalLiquidityUSD": 90000.0}]}
        raise AssertionError(f"unexpected URL {url}")

    readings = defillama.fetch_venues([AAVE_V3], get_json=fake_get_json, observed_at=AT)
    assert any(r.kind == "venue_safety_facts" for r in readings)
    assert not any(r.kind == "venue_pool" for r in readings)
