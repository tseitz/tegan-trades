from datetime import date

from oracle.benchmarks import Unresolved
from oracle.portfolios import Benchmark, Mandate
from oracle.treasury_file import ParkedRow
from treasury.book import TreasuryResult
from treasury.render import render

MANDATE = Mandate(
    name="treasury", benchmarks=(Benchmark(type="flat_rate", rate=3.1),),
    horizon="macro", risk_posture="conservative",
)

FULL_BENCHMARK = {"7d": 0.001, "30d": None, "90d": 0.008, "1y": 0.031, "since_inception": 0.0}


def _result(**overrides) -> TreasuryResult:
    base = {
        "mandate": MANDATE,
        "rows": (ParkedRow(what="USDC", amount=1000.0, venue="aave-v3", apy=4.21,
                           since=date(2026, 8, 1)),),
        "total": 1000.0, "weighted_apy": 4.21, "apy_rows": 1, "apy_amount": 1000.0,
        "benchmark": FULL_BENCHMARK, "updated": date(2026, 9, 1), "age_days": 15,
        "as_of": date(2026, 9, 16),
    }
    base.update(overrides)
    return TreasuryResult(**base)


def test_head_line_names_mandate_row_count_total_as_of_and_age():
    out = render(_result())
    assert "treasury" in out
    assert "1 row(s)" in out
    assert "$1,000.00" in out
    assert "as of 2026-09-16" in out
    assert "written 15 days ago" in out


def test_a_none_window_prints_as_a_dash_not_a_zero():
    """A missing return must never masquerade as a zero return."""
    out = render(_result())
    assert "30d —" in out
    assert "30d 0.0%" not in out


def test_a_partial_weighted_apy_says_so_on_its_face():
    out = render(_result(
        rows=(
            ParkedRow(what="USDC", amount=1000.0, venue="aave-v3", apy=4.0),
            ParkedRow(what="USDC", amount=2000.0, venue="sofi", apy=None),
        ),
        total=3000.0, weighted_apy=4.0, apy_rows=1, apy_amount=1000.0,
    ))
    assert "partial" in out
    assert "1/2 rows" in out


def test_a_full_weighted_apy_does_not_say_partial():
    out = render(_result())
    assert "partial" not in out


def test_no_row_states_an_apy():
    out = render(_result(weighted_apy=None, apy_rows=0, apy_amount=0.0))
    assert "no row states one" in out


def test_an_unresolved_benchmark_prints_its_reason_not_the_block():
    out = render(_result(benchmark=Unresolved(MANDATE.benchmarks[0], "flat_rate benchmark has no rate")))
    assert "could not be priced" in out
    assert "flat_rate benchmark has no rate" in out
