from datetime import date

import pytest
from oracle.benchmarks import Unresolved
from oracle.portfolios import Benchmark, Mandate
from oracle.treasury_file import ParkedRow, TreasuryBook
from treasury.book import treasury_for

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
