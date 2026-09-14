from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from core.interest import OpenInterest


def _oi(notional: float = 1_000_000.0, venue: str = "hyperliquid", symbol: str = "ETH"):
    return OpenInterest(
        venue=venue,
        symbol=symbol,
        notional=notional,
        volume_24h=500_000.0,
        observed_at=datetime(2026, 9, 13, 11, 0, tzinfo=UTC),
    )


def test_stores_venue_native_symbol_and_usd_notional():
    reading = _oi(notional=42.0, symbol="kPEPE")
    assert reading.symbol == "kPEPE"
    assert reading.notional == 42.0


def test_frozen_instance_rejects_mutation():
    reading = _oi()
    with pytest.raises(FrozenInstanceError):
        reading.notional = 0.0
