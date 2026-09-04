from datetime import UTC, datetime

from core.altsignal import AltSignalReading
from oracle import altsignal_store


def _reading(source="defillama", kind="chain_tvl", key="solana", value=5_927_196_263.0, when=None):
    return AltSignalReading(
        source=source,
        kind=kind,
        key=key,
        value=value,
        observed_at=when or datetime(2026, 9, 3, 6, 15, tzinfo=UTC),
    )


def test_append_then_read_round_trips_every_field(tmp_path):
    original = _reading()
    altsignal_store.append([original], root=tmp_path)
    (got,) = altsignal_store.read(root=tmp_path)
    assert got == original


def test_dict_valued_readings_round_trip_too(tmp_path):
    original = _reading(source="pumpfun", kind="graduation", key="<mint>", value={"pool": "raydium"})
    altsignal_store.append([original], root=tmp_path)
    (got,) = altsignal_store.read(root=tmp_path)
    assert got.value == {"pool": "raydium"}


def test_appending_never_overwrites(tmp_path):
    altsignal_store.append([_reading(key="solana")], root=tmp_path)
    altsignal_store.append([_reading(key="ethereum")], root=tmp_path)
    assert {r.key for r in altsignal_store.read(root=tmp_path)} == {"solana", "ethereum"}


def test_rows_are_partitioned_by_month(tmp_path):
    altsignal_store.append(
        [
            _reading(when=datetime(2026, 6, 30, 23, 0, tzinfo=UTC)),
            _reading(when=datetime(2026, 7, 1, 1, 0, tzinfo=UTC)),
        ],
        root=tmp_path,
    )
    assert {p.name for p in tmp_path.glob("*.jsonl")} == {"2026-06.jsonl", "2026-07.jsonl"}


def test_reading_spans_partitions_in_time_order(tmp_path):
    altsignal_store.append(
        [
            _reading(when=datetime(2026, 7, 1, tzinfo=UTC)),
            _reading(when=datetime(2026, 6, 1, tzinfo=UTC)),
        ],
        root=tmp_path,
    )
    got = altsignal_store.read(root=tmp_path)
    assert [r.observed_at.month for r in got] == [6, 7]


def test_read_filters_by_source_and_kind(tmp_path):
    altsignal_store.append(
        [
            _reading(source="defillama", kind="chain_tvl", key="solana"),
            _reading(source="kalshi", kind="market", key="KXFED-26DEC"),
        ],
        root=tmp_path,
    )
    (got,) = altsignal_store.read(root=tmp_path, source="kalshi")
    assert got.key == "KXFED-26DEC"
    (got,) = altsignal_store.read(root=tmp_path, kind="chain_tvl")
    assert got.source == "defillama"


def test_duplicate_observation_is_deduped_on_reread(tmp_path):
    reading = _reading()
    altsignal_store.append([reading], root=tmp_path)
    altsignal_store.append([reading], root=tmp_path)
    assert len(altsignal_store.read(root=tmp_path)) == 1
