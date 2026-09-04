"""Adapter tests. Payload shape copied from a real API reply captured 2026-09-03."""
from datetime import UTC, datetime

from oracle.altsignal import kalshi

AT = datetime(2026, 9, 3, 6, 15, tzinfo=UTC)

MARKET_PAYLOAD = {
    "market": {
        "ticker": "KXFED-26DEC-T3.75",
        "event_ticker": "KXFED-26DEC",
        "last_price_dollars": "0.7200",
        "status": "active",
    }
}


def test_parse_market_reads_last_price_as_a_probability():
    assert kalshi.parse_market(MARKET_PAYLOAD) == 0.72


def test_parse_market_missing_price_is_none():
    assert kalshi.parse_market({"market": {"ticker": "X"}}) is None


def test_fetch_builds_one_reading_per_ticker():
    def fake_get_json(url, *args, **kwargs):
        assert url.endswith("KXFED-26DEC-T3.75")
        return MARKET_PAYLOAD

    (reading,) = kalshi.fetch(["KXFED-26DEC-T3.75"], get_json=fake_get_json, observed_at=AT)
    assert reading.source == "kalshi"
    assert reading.kind == "market"
    assert reading.key == "KXFED-26DEC-T3.75"
    assert reading.value == 0.72


def test_fetch_skips_a_ticker_with_no_price_rather_than_dropping_the_others():
    def fake_get_json(url, *args, **kwargs):
        if "GOOD" in url:
            return MARKET_PAYLOAD
        return {"market": {"ticker": "BAD"}}

    readings = kalshi.fetch(["BAD", "GOOD"], get_json=fake_get_json, observed_at=AT)
    assert [r.key for r in readings] == ["GOOD"]
