"""Adapter tests. Payload shape copied from a real API reply captured 2026-09-04
(``scripts/probe_pumpfun_migrations.py`` — Solana Tracker's free tier, key required)."""
from datetime import UTC, datetime

import pytest
from oracle.altsignal import pumpfun
from oracle.http import FetchError

AT = datetime(2026, 9, 4, 6, 15, tzinfo=UTC)

GRADUATED_PAYLOAD = [
    {
        "token": {
            "name": "hopium",
            "symbol": "hopium",
            "mint": "6UB6pKTiQT4E8pHwV5nyS1tKYJfQn9mXLswsSTg8uG9j",
            "creation": {"created_time": 1788491545},
        },
        "pools": [
            {
                "market": "meteora-dyn-v2",
                "liquidity": {"usd": 5342.176901220556},
                "marketCap": {"usd": 158350.32851463984},
                "createdAt": 1788491648727,
            }
        ],
    },
    {
        # A token with no pool yet — the endpoint returned it, but nothing graduated it.
        "token": {"name": "nothing", "symbol": "NTH", "mint": "<no-pool-mint>"},
        "pools": [],
    },
]


def test_parse_graduated_uses_the_pool_creation_time_as_observed_at():
    (reading,) = pumpfun.parse_graduated(GRADUATED_PAYLOAD[:1])
    assert reading.source == "pumpfun"
    assert reading.kind == "graduation"
    assert reading.key == "6UB6pKTiQT4E8pHwV5nyS1tKYJfQn9mXLswsSTg8uG9j"
    assert reading.observed_at == datetime.fromtimestamp(1788491648727 / 1000, UTC)


def test_parse_graduated_value_carries_the_useful_fields():
    (reading,) = pumpfun.parse_graduated(GRADUATED_PAYLOAD[:1])
    assert reading.value == {
        "symbol": "hopium",
        "name": "hopium",
        "market": "meteora-dyn-v2",
        "liquidity_usd": 5342.176901220556,
        "market_cap_usd": 158350.32851463984,
    }


def test_a_token_with_no_pool_is_skipped_not_a_reading_with_nulls():
    readings = pumpfun.parse_graduated(GRADUATED_PAYLOAD)
    assert [r.key for r in readings] == ["6UB6pKTiQT4E8pHwV5nyS1tKYJfQn9mXLswsSTg8uG9j"]


def test_empty_payload_is_empty():
    assert pumpfun.parse_graduated([]) == []


def test_fetch_sends_the_api_key_header():
    seen = {}

    def fake_get_json(url, params=None, *, headers=None, **kwargs):
        seen["headers"] = headers
        return GRADUATED_PAYLOAD[:1]

    readings = pumpfun.fetch(get_json=fake_get_json, api_key="secret-key")
    assert seen["headers"] == {"x-api-key": "secret-key"}
    assert len(readings) == 1


def test_fetch_without_a_key_refuses_rather_than_calling_the_network():
    with pytest.raises(FetchError, match="SOLANATRACKER_API_KEY"):
        pumpfun.fetch(get_json=lambda *a, **k: pytest.fail("should not be called"), api_key=None)
