from datetime import UTC, datetime

from core.funding import FundingRate
from oracle import funding_store


def _rate(symbol="NVDA", venue="hyperliquid:xyz", rate=2.546e-05, hours=1.0, when=None):
    return FundingRate(
        venue=venue,
        symbol=symbol,
        rate=rate,
        interval_hours=hours,
        observed_at=when or datetime(2026, 7, 27, 20, 36, tzinfo=UTC),
    )


def test_append_then_read_round_trips_every_field(tmp_path):
    original = _rate()
    funding_store.append([original], root=tmp_path)
    (got,) = funding_store.read(root=tmp_path)
    assert got == original


def test_the_interval_survives_storage(tmp_path):
    # The rate alone is meaningless; losing the interval on the way to disk would make the
    # whole log unreadable in exactly the way that is hard to notice.
    funding_store.append([_rate(venue="lighter", hours=8.0)], root=tmp_path)
    (got,) = funding_store.read(root=tmp_path)
    assert got.interval_hours == 8.0


def test_appending_never_overwrites(tmp_path):
    funding_store.append([_rate(symbol="NVDA")], root=tmp_path)
    funding_store.append([_rate(symbol="TSLA")], root=tmp_path)
    assert {r.symbol for r in funding_store.read(root=tmp_path)} == {"NVDA", "TSLA"}


def test_rows_are_partitioned_by_month(tmp_path):
    funding_store.append(
        [
            _rate(when=datetime(2026, 6, 30, 23, 0, tzinfo=UTC)),
            _rate(when=datetime(2026, 7, 1, 1, 0, tzinfo=UTC)),
        ],
        root=tmp_path,
    )
    assert {p.name for p in tmp_path.glob("*.jsonl")} == {"2026-06.jsonl", "2026-07.jsonl"}


def test_reading_spans_partitions_in_time_order(tmp_path):
    funding_store.append(
        [
            _rate(when=datetime(2026, 7, 1, tzinfo=UTC)),
            _rate(when=datetime(2026, 6, 1, tzinfo=UTC)),
        ],
        root=tmp_path,
    )
    got = funding_store.read(root=tmp_path)
    assert [r.observed_at.month for r in got] == [6, 7]


def test_rerunning_a_backfill_is_idempotent_for_the_reader(tmp_path):
    rows = [_rate(when=datetime(2026, 7, 1, h, tzinfo=UTC)) for h in range(5)]
    funding_store.append(rows, root=tmp_path)
    funding_store.append(rows, root=tmp_path)
    # Written twice on purpose -- appending is cheaper than seeking, so dedupe is the
    # reader's job.
    assert len(funding_store.read(root=tmp_path)) == 5


def test_same_symbol_on_two_venues_is_not_deduped(tmp_path):
    when = datetime(2026, 7, 1, tzinfo=UTC)
    funding_store.append(
        [_rate(venue="hyperliquid:xyz", when=when), _rate(venue="lighter", hours=8.0, when=when)],
        root=tmp_path,
    )
    assert len(funding_store.read(root=tmp_path)) == 2


def test_filters_narrow_by_venue_symbol_and_time(tmp_path):
    funding_store.append(
        [
            _rate(symbol="NVDA", venue="lighter", when=datetime(2026, 7, 1, tzinfo=UTC)),
            _rate(symbol="TSLA", venue="lighter", when=datetime(2026, 7, 2, tzinfo=UTC)),
            _rate(symbol="NVDA", venue="aster", when=datetime(2026, 7, 3, tzinfo=UTC)),
        ],
        root=tmp_path,
    )
    assert len(funding_store.read(root=tmp_path, venue="lighter")) == 2
    assert len(funding_store.read(root=tmp_path, symbol="NVDA")) == 2
    assert len(funding_store.read(root=tmp_path, since=datetime(2026, 7, 2, tzinfo=UTC))) == 2


def test_a_truncated_line_does_not_poison_the_whole_log(tmp_path):
    funding_store.append([_rate()], root=tmp_path)
    path = next(tmp_path.glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"v":"aster","s":"BTC"\n')  # machine slept mid-append
    funding_store.append([_rate(symbol="TSLA")], root=tmp_path)
    assert {r.symbol for r in funding_store.read(root=tmp_path)} == {"NVDA", "TSLA"}


def test_reading_a_missing_root_is_empty_not_an_error(tmp_path):
    assert funding_store.read(root=tmp_path / "nope") == []


def test_appending_nothing_creates_no_files(tmp_path):
    assert funding_store.append([], root=tmp_path) == {}
    assert list(tmp_path.glob("*.jsonl")) == []
