from datetime import UTC, date, datetime

import pytest
from core.altsignal import AltSignalReading
from core.safety import UNCONFIGURED
from oracle.altsignal_config import VenueEntry
from oracle.benchmarks import Unresolved
from oracle.portfolios import Benchmark, Mandate, Portfolio
from oracle.treasury_file import ParkedRow, TreasuryBook
from treasury.book import NOT_FETCHED, IdleCash, treasury_for

AS_OF = date(2026, 9, 16)
UPDATED = date(2026, 9, 1)

MANDATE = Mandate(
    name="treasury", benchmarks=(Benchmark(type="flat_rate", rate=3.1),),
    horizon="macro", risk_posture="conservative",
)


def _book(rows) -> TreasuryBook:
    return TreasuryBook(mandate=MANDATE, rows=rows, updated=UPDATED)


def test_total_sums_every_row_as_typed_principal(tmp_path):
    rows = (
        ParkedRow(what="USDC", amount=5000.0, venue="aave-v3", apy=4.21),
        ParkedRow(what="USDC", amount=1200.0, venue="sofi", apy=None),
    )
    result = treasury_for(_book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json")
    assert result.total == 6200.0


def test_weighted_apy_ignores_rows_with_no_apy_but_counts_their_money_separately(tmp_path):
    rows = (
        ParkedRow(what="USDC", amount=1000.0, venue="aave-v3", apy=4.0),
        ParkedRow(what="USDC", amount=3000.0, venue="aave-v3-2", apy=8.0),
        ParkedRow(what="USDC", amount=2000.0, venue="sofi", apy=None),
    )
    result = treasury_for(_book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json")
    assert result.weighted_apy == pytest.approx((1000 * 4.0 + 3000 * 8.0) / 4000)
    assert result.apy_rows == 2
    assert result.apy_amount == 4000.0
    assert result.total == 6000.0


def test_weighted_apy_is_none_when_no_row_states_one(tmp_path):
    rows = (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3", apy=None),)
    result = treasury_for(_book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json")
    assert result.weighted_apy is None
    assert result.apy_rows == 0
    assert result.apy_amount == 0.0


def test_a_flat_rate_mandate_produces_the_five_window_benchmark_dict(tmp_path):
    """AC 3: benchmarked against the cash rate alone."""
    rows = (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3", apy=4.0),)
    result = treasury_for(_book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json")
    assert set(result.benchmark) == {"7d", "30d", "90d", "1y", "since_inception"}
    assert result.benchmark["since_inception"] == 0.0  # first-ever anchor for this key


def test_an_unresolved_benchmark_passes_through_untouched(tmp_path):
    unresolvable = Mandate(
        name="treasury", benchmarks=(Benchmark(type="flat_rate", rate=None),),
        horizon="macro", risk_posture="conservative",
    )
    book = TreasuryBook(
        mandate=unresolvable,
        rows=(ParkedRow(what="USDC", amount=1000.0, venue="aave-v3"),),
        updated=UPDATED,
    )
    result = treasury_for(book, as_of=AS_OF, anchor_root=tmp_path / "anchors.json")
    assert isinstance(result.benchmark, Unresolved)


def test_age_days_is_computed_against_as_of(tmp_path):
    rows = (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3"),)
    result = treasury_for(_book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json")
    assert result.age_days == (AS_OF - UPDATED).days
    assert result.as_of == AS_OF


# ── Safety (#73) ────────────────────────────────────────────────────────

VENUE = VenueEntry(
    venue="aave-v3", llama_protocol="aave-v3", llama_pools=("pool-1",),
    asset="USDC", chain="Ethereum",
)

READ_AT = datetime(2026, 9, 15, tzinfo=UTC)


def _store_read(rows):
    def read(*, source=None, kind=None, key=None, since=None):
        return [
            r for r in rows
            if (source is None or r.source == source)
            and (kind is None or r.kind == kind)
            and (key is None or r.key == key)
        ]
    return read


def _passing_facts_rows():
    return [
        AltSignalReading(
            source="defillama", kind="venue_safety_facts", key="aave-v3",
            value={"audited": True, "forked_from": [], "incidents": 0}, observed_at=READ_AT,
        ),
        AltSignalReading(
            source="defillama", kind="venue_first_tvl", key="aave-v3",
            value="2022-01-01", observed_at=READ_AT,
        ),
        AltSignalReading(
            source="defillama", kind="venue_pool", key="pool-1",
            value={"apy": 5.0, "apy_reward": None, "sigma": 0.1, "count": 500,
                   "outlier": False, "stablecoin": True},
            observed_at=READ_AT,
        ),
    ]


def test_ac4_advice_exists_and_total_is_unchanged(tmp_path):
    """AC 4: idle cash produces advice without ever touching the parked total."""
    rows = (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3", apy=4.0),)
    portfolio = Portfolio(
        name="checking",
        mandate=Mandate(name="checking", benchmarks=(Benchmark(type="flat_rate", rate=0.0),),
                         horizon="macro", risk_posture="conservative"),
        positions=(), cash=2000.0,
    )
    result = treasury_for(
        _book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json",
        books=(portfolio,), venues=(VENUE,), store_read=_store_read(_passing_facts_rows()),
    )
    assert result.total == 1000.0
    assert len(result.advice) == 1
    assert result.advice[0].facts.slug == "aave-v3"
    assert result.idle == (IdleCash(mandate="checking", account="checking", amount=2000.0),)


def test_a_configured_venue_never_fetched_reads_not_fetched_not_no_audit(tmp_path):
    rows = (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3"),)
    result = treasury_for(
        _book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json",
        venues=(VENUE,), store_read=_store_read([]),
    )
    assert result.safety["aave-v3"] == NOT_FETCHED


def test_a_venue_with_no_config_row_is_unconfigured(tmp_path):
    rows = (ParkedRow(what="USDC", amount=1200.0, venue="sofi"),)
    result = treasury_for(
        _book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json",
        venues=(VENUE,), store_read=_store_read(_passing_facts_rows()),
    )
    assert result.safety["sofi"] == UNCONFIGURED


def test_a_configured_venue_that_clears_the_gate_carries_a_gate_result_and_score(tmp_path):
    rows = (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3"),)
    result = treasury_for(
        _book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json",
        venues=(VENUE,), store_read=_store_read(_passing_facts_rows()),
    )
    gate_result, score = result.safety["aave-v3"]
    assert gate_result.passed
    assert score.incidents == 0


def test_cash_none_produces_no_idle_row(tmp_path):
    rows = (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3"),)
    portfolio = Portfolio(
        name="hand-kept",
        mandate=Mandate(name="hand-kept", benchmarks=(Benchmark(type="flat_rate", rate=0.0),),
                         horizon="macro", risk_posture="conservative"),
        positions=(), cash=None,
    )
    result = treasury_for(
        _book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json", books=(portfolio,),
    )
    assert result.idle == ()


def test_no_store_read_leaves_safety_and_advice_empty(tmp_path):
    """The additive default: #73's whole feature stays off when nothing wires it in."""
    rows = (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3"),)
    result = treasury_for(_book(rows), as_of=AS_OF, anchor_root=tmp_path / "anchors.json")
    assert result.safety == {}
    assert result.advice == ()
    assert result.readings_as_of.freshest is None
