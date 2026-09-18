from datetime import UTC, date, datetime

from core.altsignal import AltSignalReading
from core.venue_facts import pool_candidates, protocol_facts

READ_AT = datetime(2026, 9, 15, tzinfo=UTC)


class _Entry:
    """Duck-typed stand-in for `VenueEntry`/`WrapperEntry` — `pool_candidates` reads only
    `llama_protocol`/`llama_pools`, the reason #74 needed no second fetch path."""

    def __init__(self, llama_protocol: str, llama_pools: tuple[str, ...]):
        self.llama_protocol = llama_protocol
        self.llama_pools = llama_pools


def _store_read(rows):
    def read(*, source=None, kind=None, key=None, since=None):
        return [
            r for r in rows
            if (source is None or r.source == source)
            and (kind is None or r.kind == kind)
            and (key is None or r.key == key)
        ]
    return read


def test_protocol_facts_reads_the_latest_row_per_slug():
    rows = [
        AltSignalReading(
            source="defillama", kind="venue_safety_facts", key="lido",
            value={"audited": True, "forked_from": [], "incidents": 1}, observed_at=READ_AT,
        ),
        AltSignalReading(
            source="defillama", kind="venue_first_tvl", key="lido",
            value="2020-12-18", observed_at=READ_AT,
        ),
    ]
    facts, all_rows = protocol_facts(store_read=_store_read(rows))
    assert facts["lido"].audited is True
    assert facts["lido"].first_tvl_on == date(2020, 12, 18)
    assert facts["lido"].incidents == 1
    assert all_rows == rows


def test_protocol_facts_leaves_first_tvl_on_none_when_no_age_row_yet():
    rows = [
        AltSignalReading(
            source="defillama", kind="venue_safety_facts", key="meth-protocol",
            value={"audited": False, "forked_from": [], "incidents": 0}, observed_at=READ_AT,
        ),
    ]
    facts, _ = protocol_facts(store_read=_store_read(rows))
    assert facts["meth-protocol"].first_tvl_on is None


def test_pool_candidates_joins_pool_facts_against_their_protocols_facts():
    facts_by_slug, _ = protocol_facts(store_read=_store_read([
        AltSignalReading(
            source="defillama", kind="venue_safety_facts", key="lido",
            value={"audited": True, "forked_from": [], "incidents": 0}, observed_at=READ_AT,
        ),
        AltSignalReading(
            source="defillama", kind="venue_first_tvl", key="lido",
            value="2020-12-18", observed_at=READ_AT,
        ),
    ]))
    pool_rows = [
        AltSignalReading(
            source="defillama", kind="venue_pool", key="pool-1",
            value={"apy": 2.25, "apy_reward": None, "sigma": 0.05, "count": 1000,
                   "outlier": False, "stablecoin": False},
            observed_at=READ_AT,
        ),
    ]
    candidates, all_rows = pool_candidates(
        [_Entry("lido", ("pool-1",))], facts_by_slug, store_read=_store_read(pool_rows)
    )
    assert len(candidates) == 1
    facts = candidates[0]
    assert facts.slug == "lido"
    assert facts.pool_id == "pool-1"
    assert facts.audited is True  # inherited from the protocol-level facts
    assert facts.apy == 2.25
    assert all_rows == pool_rows


def test_pool_candidates_skips_a_pool_with_no_stored_row():
    candidates, all_rows = pool_candidates(
        [_Entry("lido", ("pool-1",))], {}, store_read=_store_read([])
    )
    assert candidates == ()
    assert all_rows == []


def test_pool_candidates_leaves_protocol_level_fields_none_when_the_protocol_was_never_fetched():
    pool_rows = [
        AltSignalReading(
            source="defillama", kind="venue_pool", key="pool-1",
            value={"apy": 2.25, "apy_reward": None, "sigma": None, "count": None,
                   "outlier": False, "stablecoin": False},
            observed_at=READ_AT,
        ),
    ]
    candidates, _ = pool_candidates(
        [_Entry("lido", ("pool-1",))], {}, store_read=_store_read(pool_rows)
    )
    assert candidates[0].audited is None
    assert candidates[0].first_tvl_on is None
