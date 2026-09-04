from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from core.altsignal import AltSignalReading


def _reading(**overrides):
    fields = {
        "source": "defillama",
        "kind": "chain_tvl",
        "key": "solana",
        "value": 5_927_196_263.0,
        "observed_at": datetime(2026, 9, 3, 6, 15, tzinfo=UTC),
    }
    fields.update(overrides)
    return AltSignalReading(**fields)


def test_holds_every_field_verbatim():
    r = _reading()
    assert r.source == "defillama"
    assert r.kind == "chain_tvl"
    assert r.key == "solana"
    assert r.value == 5_927_196_263.0
    assert r.observed_at == datetime(2026, 9, 3, 6, 15, tzinfo=UTC)


def test_frozen():
    r = _reading()
    with pytest.raises(FrozenInstanceError):
        r.value = 0.0


def test_value_can_be_a_dict_for_shapes_that_do_not_reduce_to_one_number():
    # A pump.fun graduation event carries a mint address, a pool, and a timestamp — no
    # single float represents it, unlike a chain TVL reading.
    r = _reading(source="pumpfun", kind="graduation", key="<mint>", value={"pool": "raydium"})
    assert r.value == {"pool": "raydium"}
