"""Adapter tests. Payload shape copied from a real API reply captured 2026-09-03."""
from datetime import UTC, datetime

from oracle.altsignal import polymarket

AT = datetime(2026, 9, 3, 6, 15, tzinfo=UTC)

# outcomePrices arrives as a JSON-encoded string, not a real array — a real quirk of this API.
EVENT_PAYLOAD = [
    {
        "slug": "us-recession-by-end-of-2026",
        "markets": [
            {
                "slug": "us-recession-by-end-of-2026",
                "question": "US recession by end of 2026?",
                "outcomePrices": '["0.07", "0.93"]',
            }
        ],
    }
]

MULTI_MARKET_EVENT_PAYLOAD = [
    {
        "slug": "what-price-will-bitcoin-hit-before-2027",
        "markets": [
            {"slug": "btc-hit-150k", "question": "Will BTC hit $150k?", "outcomePrices": '["0.31", "0.69"]'},
            {"slug": "btc-hit-200k", "question": "Will BTC hit $200k?", "outcomePrices": '["0.08", "0.92"]'},
        ],
    }
]


def test_parse_event_yields_one_reading_per_market():
    readings = polymarket.parse_event(
        EVENT_PAYLOAD, event_slug="us-recession-by-end-of-2026", observed_at=AT
    )
    (r,) = readings
    # Namespaced <event slug>:<market slug>, mirroring hyperliquid's dex-prefixed venue — a
    # stored reading otherwise carries no way back to which configured event produced it.
    assert r.key == "us-recession-by-end-of-2026:us-recession-by-end-of-2026"
    assert r.value == 0.07


def test_parse_event_handles_multiple_markets_in_one_event():
    readings = polymarket.parse_event(
        MULTI_MARKET_EVENT_PAYLOAD, event_slug="what-price-will-bitcoin-hit-before-2027", observed_at=AT
    )
    assert {r.key: r.value for r in readings} == {
        "what-price-will-bitcoin-hit-before-2027:btc-hit-150k": 0.31,
        "what-price-will-bitcoin-hit-before-2027:btc-hit-200k": 0.08,
    }


def test_parse_event_not_found_is_empty():
    assert polymarket.parse_event([], event_slug="anything", observed_at=AT) == []


def test_fetch_builds_readings_for_each_slug():
    def fake_get_json(url, params=None, **kwargs):
        assert params == {"slug": "us-recession-by-end-of-2026"}
        return EVENT_PAYLOAD

    readings = polymarket.fetch(["us-recession-by-end-of-2026"], get_json=fake_get_json, observed_at=AT)
    assert readings[0].source == "polymarket"
    assert readings[0].kind == "market"
