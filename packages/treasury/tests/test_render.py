from datetime import UTC, date, datetime

from core.safety import UNCONFIGURED, GateResult, RankedVenue, SafetyScore, VenueFacts
from oracle.benchmarks import Unresolved
from oracle.portfolios import Benchmark, Mandate
from oracle.treasury_file import ParkedRow
from treasury.book import NOT_FETCHED, IdleCash, ReadingsAsOf, TreasuryResult
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


# ── Safety (#73) ────────────────────────────────────────────────────────

def test_an_unconfigured_venue_prints_nothing_extra():
    out = render(_result(safety={"aave-v3": UNCONFIGURED}))
    assert "not fetched" not in out
    assert "Safety" not in out


def test_a_never_fetched_configured_venue_says_so_distinctly():
    out = render(_result(safety={"aave-v3": NOT_FETCHED}))
    assert "not fetched yet" in out


def test_a_failing_gate_prints_its_reasons():
    gate_result = GateResult(
        passed=False, reasons=("no audit on record",), required_age_days=274, lineage="standalone"
    )
    score = SafetyScore(incentive_share=None, apy_volatility=None, observations=None, incidents=0)
    out = render(_result(safety={"aave-v3": (gate_result, score)}))
    assert "Safety FAILS" in out
    assert "no audit on record" in out


def test_a_passing_gate_prints_ok_and_its_incident_count():
    gate_result = GateResult(
        passed=True, reasons=(), required_age_days=274, lineage="standalone"
    )
    score = SafetyScore(incentive_share=None, apy_volatility=None, observations=None, incidents=2)
    out = render(_result(safety={"aave-v3": (gate_result, score)}))
    assert "Safety OK" in out
    assert "2 incident(s)" in out


def test_readings_as_of_prints_a_staleness_line_when_present():
    out = render(_result(readings_as_of=ReadingsAsOf(
        freshest=datetime(2026, 9, 15, tzinfo=UTC), oldest=datetime(2026, 9, 10, tzinfo=UTC),
    )))
    assert "2026-09-10" in out and "2026-09-15" in out


def test_no_readings_prints_no_staleness_line():
    out = render(_result())
    assert "safety readings" not in out


def test_idle_block_prints_only_with_idle_cash_and_at_least_one_passing_venue():
    ranked = RankedVenue(
        facts=VenueFacts(slug="aave-v3", apy=5.0),
        gate=GateResult(passed=True, reasons=(), required_age_days=274, lineage="standalone"),
        score=SafetyScore(incentive_share=None, apy_volatility=None, observations=None, incidents=0),
    )
    out = render(_result(
        idle=(IdleCash(mandate="checking", account="checking", amount=2000.0),), advice=(ranked,),
    ))
    assert "IDLE CASH" in out
    assert "ADVICE" in out
    assert "aave-v3" in out


def test_no_idle_block_when_there_is_no_idle_cash():
    ranked = RankedVenue(
        facts=VenueFacts(slug="aave-v3", apy=5.0),
        gate=GateResult(passed=True, reasons=(), required_age_days=274, lineage="standalone"),
        score=SafetyScore(incentive_share=None, apy_volatility=None, observations=None, incidents=0),
    )
    out = render(_result(idle=(), advice=(ranked,)))
    assert "IDLE CASH" not in out


def test_no_idle_block_when_nothing_clears_the_gate():
    out = render(_result(
        idle=(IdleCash(mandate="checking", account="checking", amount=2000.0),), advice=(),
    ))
    assert "IDLE CASH" not in out
