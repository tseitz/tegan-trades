"""Reducing a `TreasuryResult` to what a nightly can honestly carry. Pure.

The deployed half is standing state and has no diff to test — every field on `TreasuryDelta`
that describes it is copied straight off the `TreasuryResult`. What is worth testing is the
opportunity half: it is a diff, keyed on `pool_id`, gated on idle cash actually being non-zero.
"""
from __future__ import annotations

from core import safety
from digest import treasury
from oracle.portfolios import Mandate
from oracle.treasury_file import ParkedRow
from treasury.book import IdleCash, TreasuryResult

MANDATE = Mandate(name="treasury", benchmarks=(), horizon="macro", risk_posture="conservative")


def _ranked(slug: str, pool_id: str | None, apy: float = 4.0) -> safety.RankedVenue:
    facts = safety.VenueFacts(slug=slug, pool_id=pool_id, apy=apy, stablecoin=True)
    gate_result = safety.GateResult(passed=True, reasons=(), required_age_days=274,
                                    lineage=safety.STANDALONE)
    return safety.RankedVenue(facts=facts, gate=gate_result,
                              score=safety.SafetyScore(incentive_share=None, apy_volatility=None,
                                                       observations=None, incidents=None))


def _result(*, rows=(), advice=(), idle=()) -> TreasuryResult:
    apy_rows = [row for row in rows if row.apy is not None]
    apy_amount = sum(row.amount for row in apy_rows)
    weighted_apy = (sum(row.amount * row.apy for row in apy_rows) / apy_amount
                    if apy_amount else None)
    return TreasuryResult(
        mandate=MANDATE, rows=rows, total=sum(row.amount for row in rows),
        weighted_apy=weighted_apy, apy_rows=len(apy_rows), apy_amount=apy_amount,
        benchmark={}, updated=None, age_days=None, as_of=None,
        idle=idle, advice=advice,
    )


def test_no_advice_and_no_idle_has_a_quiet_opportunity_half():
    result = _result(rows=(ParkedRow(what="USDC", amount=1000.0, venue="aave-v3"),))
    d = treasury.delta(result, {}, has_idle=False)
    assert d.new == ()
    assert d.standing == 0
    assert d.is_quiet


def test_rows_with_no_advice_still_carry_the_deployed_facts():
    result = _result(rows=(ParkedRow(what="USDC", amount=1000.0, venue="aave-v3", apy=4.21),))
    d = treasury.delta(result, {}, has_idle=True)
    assert d.mandate_name == "treasury"
    assert d.rows == 1
    assert d.total == 1000.0
    assert d.weighted_apy == 4.21
    assert d.is_quiet


def test_advice_with_an_empty_memory_is_all_new():
    result = _result(advice=(_ranked("aave-v3", "pool-1"), _ranked("compound", "pool-2")),
                     idle=(IdleCash(mandate="retirement", account="cash", amount=500.0),))
    d = treasury.delta(result, {}, has_idle=True)
    assert {r.facts.pool_id for r in d.new} == {"pool-1", "pool-2"}
    assert d.standing == 0


def test_the_same_advice_against_last_nights_memory_is_all_standing():
    advice = (_ranked("aave-v3", "pool-1"), _ranked("compound", "pool-2"))
    result = _result(advice=advice,
                     idle=(IdleCash(mandate="retirement", account="cash", amount=500.0),))
    remembered = {"pool-1": "aave-v3", "pool-2": "compound"}
    d = treasury.delta(result, remembered, has_idle=True)
    assert d.new == ()
    assert d.standing == 2


def test_two_pools_under_one_slug_are_remembered_separately():
    """`slug` is not unique — `aave-v3` can supply more than one pool. Keying the memory on
    `slug` would make the second pool unreportable forever."""
    advice = (_ranked("aave-v3", "pool-1"), _ranked("aave-v3", "pool-2"))
    result = _result(advice=advice,
                     idle=(IdleCash(mandate="retirement", account="cash", amount=500.0),))
    d = treasury.delta(result, {"pool-1": "aave-v3"}, has_idle=True)
    assert [r.facts.pool_id for r in d.new] == ["pool-2"]
    assert d.standing == 1


def test_has_idle_false_suppresses_the_opportunity_half_even_with_fresh_advice():
    result = _result(advice=(_ranked("aave-v3", "pool-1"),),
                     idle=(IdleCash(mandate="retirement", account="cash", amount=500.0),))
    d = treasury.delta(result, {}, has_idle=False)
    assert d.new == ()
    assert d.standing == 0


def test_a_pool_with_no_pool_id_is_skipped_rather_than_counted():
    result = _result(advice=(_ranked("sofi", None),),
                     idle=(IdleCash(mandate="retirement", account="cash", amount=500.0),))
    d = treasury.delta(result, {}, has_idle=True)
    assert d.new == ()
    assert d.standing == 0


def test_bootstrap_is_the_first_time_this_memory_has_ever_been_written():
    result = _result(advice=(_ranked("aave-v3", "pool-1"),),
                     idle=(IdleCash(mandate="retirement", account="cash", amount=500.0),))
    assert treasury.delta(result, {}, has_idle=True).bootstrap is True
    assert treasury.delta(result, {"pool-1": "aave-v3"}, has_idle=True).bootstrap is False


# ── `has_idle` itself ──────────────────────────────────────────────────────────

def test_has_idle_is_false_for_a_zero_amount_account():
    """`treasury.book._idle_rows` emits one `IdleCash` per account regardless of amount, so a
    `0.0` account must not be able to turn the opportunity half on."""
    result = _result(idle=(IdleCash(mandate="retirement", account="cash", amount=0.0),))
    assert treasury.has_idle(result) is False


def test_has_idle_is_true_for_any_non_zero_account():
    result = _result(idle=(IdleCash(mandate="retirement", account="cash", amount=0.0),
                           IdleCash(mandate="retirement", account="roth", amount=25.0)))
    assert treasury.has_idle(result) is True


# ── `remember` ───────────────────────────────────────────────────────────────

def test_remember_stores_every_current_opportunity_not_only_the_new_ones():
    advice = (_ranked("aave-v3", "pool-1"), _ranked("compound", "pool-2"))
    result = _result(advice=advice)
    assert treasury.remember(result) == {"pool-1": "aave-v3", "pool-2": "compound"}


def test_remember_skips_a_pool_with_no_pool_id():
    result = _result(advice=(_ranked("sofi", None),))
    assert treasury.remember(result) == {}
