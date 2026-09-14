from datetime import UTC, datetime

from core.interest import OpenInterest
from oracle import interest_store


def _oi(symbol="ETH", venue="hyperliquid", notional=1_000_000.0, volume=500_000.0, when=None):
    return OpenInterest(
        venue=venue,
        symbol=symbol,
        notional=notional,
        volume_24h=volume,
        observed_at=when or datetime(2026, 9, 13, 11, 0, tzinfo=UTC),
    )


def test_append_then_read_round_trips_every_field(tmp_path):
    original = _oi()
    interest_store.append([original], root=tmp_path)
    (got,) = interest_store.read(root=tmp_path)
    assert got == original


def test_appending_never_overwrites(tmp_path):
    interest_store.append([_oi(symbol="ETH")], root=tmp_path)
    interest_store.append([_oi(symbol="BTC")], root=tmp_path)
    assert {r.symbol for r in interest_store.read(root=tmp_path)} == {"ETH", "BTC"}


def test_rows_are_partitioned_by_month(tmp_path):
    interest_store.append(
        [
            _oi(when=datetime(2026, 6, 30, 23, 0, tzinfo=UTC)),
            _oi(when=datetime(2026, 7, 1, 1, 0, tzinfo=UTC)),
        ],
        root=tmp_path,
    )
    assert {p.name for p in tmp_path.glob("*.jsonl")} == {"2026-06.jsonl", "2026-07.jsonl"}


def test_reading_spans_partitions_in_time_order(tmp_path):
    interest_store.append(
        [
            _oi(when=datetime(2026, 7, 1, tzinfo=UTC)),
            _oi(when=datetime(2026, 6, 1, tzinfo=UTC)),
        ],
        root=tmp_path,
    )
    got = interest_store.read(root=tmp_path)
    assert [r.observed_at.month for r in got] == [6, 7]


def test_rerunning_a_sweep_is_idempotent_for_the_reader(tmp_path):
    rows = [_oi(when=datetime(2026, 7, 1, h, tzinfo=UTC)) for h in range(5)]
    interest_store.append(rows, root=tmp_path)
    interest_store.append(rows, root=tmp_path)
    # Written twice on purpose -- appending is cheaper than seeking, so dedupe is the
    # reader's job.
    assert len(interest_store.read(root=tmp_path)) == 5


def test_same_symbol_on_two_venues_is_not_deduped(tmp_path):
    when = datetime(2026, 7, 1, tzinfo=UTC)
    interest_store.append(
        [_oi(venue="hyperliquid", when=when), _oi(venue="lighter", when=when)],
        root=tmp_path,
    )
    assert len(interest_store.read(root=tmp_path)) == 2


def test_filters_narrow_by_venue_symbol_and_time(tmp_path):
    interest_store.append(
        [
            _oi(symbol="ETH", venue="lighter", when=datetime(2026, 7, 1, tzinfo=UTC)),
            _oi(symbol="BTC", venue="lighter", when=datetime(2026, 7, 2, tzinfo=UTC)),
            _oi(symbol="ETH", venue="aster", when=datetime(2026, 7, 3, tzinfo=UTC)),
        ],
        root=tmp_path,
    )
    assert len(interest_store.read(root=tmp_path, venue="lighter")) == 2
    assert len(interest_store.read(root=tmp_path, symbol="ETH")) == 2
    assert len(interest_store.read(root=tmp_path, since=datetime(2026, 7, 2, tzinfo=UTC))) == 2


def test_a_truncated_line_does_not_poison_the_whole_log(tmp_path):
    interest_store.append([_oi()], root=tmp_path)
    path = next(tmp_path.glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"v":"aster","s":"BTC"\n')  # machine slept mid-append
    interest_store.append([_oi(symbol="BTC")], root=tmp_path)
    assert {r.symbol for r in interest_store.read(root=tmp_path)} == {"ETH", "BTC"}


def test_reading_a_missing_root_is_empty_not_an_error(tmp_path):
    assert interest_store.read(root=tmp_path / "nope") == []


def test_appending_nothing_creates_no_files(tmp_path):
    assert interest_store.append([], root=tmp_path) == {}
    assert list(tmp_path.glob("*.jsonl")) == []
