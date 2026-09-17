from datetime import date

from core.safety import (
    MIN_AGE_DAYS_STANDALONE,
    MIN_AGE_DAYS_VALIDATED_FORK,
    STANDALONE,
    UNRESOLVED_PARENT,
    VALIDATED_FORK,
    VenueFacts,
    gate,
    rank,
    score,
)

AS_OF = date(2026, 9, 17)


def _facts(
    slug="v", *, audited: bool | None = True, age_days=MIN_AGE_DAYS_STANDALONE,
    forked_from=(), **kw,
):
    first_tvl_on = AS_OF.fromordinal(AS_OF.toordinal() - age_days) if age_days is not None else None
    return VenueFacts(
        slug=slug, audited=audited, first_tvl_on=first_tvl_on, forked_from=forked_from, **kw
    )


# ── gate() — table-driven, one row per case ────────────────────────────────

def test_no_audit_fails_even_with_everything_else_pristine():
    """AC 1."""
    facts = _facts(audited=False, age_days=MIN_AGE_DAYS_STANDALONE)
    result = gate(facts, as_of=AS_OF, by_slug={})
    assert not result.passed
    assert "no audit on record" in result.reasons


def test_audited_none_fails_same_as_false():
    facts = _facts(audited=None, age_days=MIN_AGE_DAYS_STANDALONE)
    result = gate(facts, as_of=AS_OF, by_slug={})
    assert not result.passed
    assert "no audit on record" in result.reasons


def test_validated_fork_at_130_days_passes():
    parent = _facts("parent", age_days=MIN_AGE_DAYS_STANDALONE)
    child = _facts("child", age_days=130, forked_from=("parent",))
    by_slug = {"parent": parent}
    result = gate(child, as_of=AS_OF, by_slug=by_slug)
    assert result.passed
    assert result.lineage == VALIDATED_FORK
    assert result.required_age_days == MIN_AGE_DAYS_VALIDATED_FORK


def test_identical_facts_with_a_failing_parent_fail_at_the_standalone_floor():
    parent = _facts("parent", audited=False, age_days=MIN_AGE_DAYS_STANDALONE)
    child = _facts("child", age_days=130, forked_from=("parent",))
    by_slug = {"parent": parent}
    result = gate(child, as_of=AS_OF, by_slug=by_slug)
    assert not result.passed
    assert result.required_age_days == MIN_AGE_DAYS_STANDALONE


def test_parent_absent_from_by_slug_is_unresolved_parent_at_the_standalone_floor():
    child = _facts("child", age_days=MIN_AGE_DAYS_STANDALONE, forked_from=("nowhere",))
    result = gate(child, as_of=AS_OF, by_slug={})
    assert result.lineage == UNRESOLVED_PARENT
    assert result.required_age_days == MIN_AGE_DAYS_STANDALONE
    assert result.passed  # 274 days old, needs 274 — the standalone floor, met


def test_a_lineage_cycle_resolves_unresolved_without_a_recursion_error():
    """Neither side is old enough alone, so neither can bootstrap validation off the other —
    the cycle guard must stop the recursion rather than let A and B validate each other
    forever."""
    a = _facts("a", age_days=100, forked_from=("b",))
    b = _facts("b", age_days=100, forked_from=("a",))
    by_slug = {"a": a, "b": b}
    result = gate(a, as_of=AS_OF, by_slug=by_slug)
    assert result.lineage == UNRESOLVED_PARENT
    assert not result.passed  # 100 days short of the 274-day standalone floor


def test_missing_first_tvl_on_fails():
    facts = _facts(age_days=None)
    result = gate(facts, as_of=AS_OF, by_slug={})
    assert not result.passed
    assert "first TVL date not read" in result.reasons


def test_two_parents_one_passing_is_validated():
    failing_parent = _facts("bad", audited=False, age_days=MIN_AGE_DAYS_STANDALONE)
    passing_parent = _facts("good", age_days=MIN_AGE_DAYS_STANDALONE)
    child = _facts("child", age_days=130, forked_from=("bad", "good"))
    by_slug = {"bad": failing_parent, "good": passing_parent}
    result = gate(child, as_of=AS_OF, by_slug=by_slug)
    assert result.passed
    assert result.lineage == VALIDATED_FORK


def test_standalone_protocol_has_no_forked_from_and_needs_the_9_month_floor():
    facts = _facts(forked_from=(), age_days=MIN_AGE_DAYS_STANDALONE)
    result = gate(facts, as_of=AS_OF, by_slug={})
    assert result.passed
    assert result.lineage == STANDALONE


def test_exact_boundary_at_validated_fork_floor_passes():
    parent = _facts("parent", age_days=MIN_AGE_DAYS_STANDALONE)
    child = _facts("child", age_days=MIN_AGE_DAYS_VALIDATED_FORK, forked_from=("parent",))
    result = gate(child, as_of=AS_OF, by_slug={"parent": parent})
    assert result.passed


def test_one_day_short_of_validated_fork_floor_fails():
    parent = _facts("parent", age_days=MIN_AGE_DAYS_STANDALONE)
    child = _facts("child", age_days=MIN_AGE_DAYS_VALIDATED_FORK - 1, forked_from=("parent",))
    result = gate(child, as_of=AS_OF, by_slug={"parent": parent})
    assert not result.passed


def test_exact_boundary_at_standalone_floor_passes():
    facts = _facts(age_days=MIN_AGE_DAYS_STANDALONE)
    assert gate(facts, as_of=AS_OF, by_slug={}).passed


def test_one_day_short_of_standalone_floor_fails():
    facts = _facts(age_days=MIN_AGE_DAYS_STANDALONE - 1)
    assert not gate(facts, as_of=AS_OF, by_slug={}).passed


# ── score() ─────────────────────────────────────────────────────────────

def test_apy_reward_none_leaves_incentive_share_none_never_zero():
    facts = _facts(apy=5.0, apy_reward=None)
    result = score(facts)
    assert result.incentive_share is None


def test_incentive_share_divides_reward_by_total_apy():
    facts = _facts(apy=5.0, apy_reward=1.0)
    assert score(facts).incentive_share == 0.2


def test_apy_volatility_and_observations_ride_together():
    facts = _facts(sigma=0.02, count=124)
    result = score(facts)
    assert result.apy_volatility == 0.02
    assert result.observations == 124


def test_incidents_come_from_facts_incidents():
    facts = _facts(incidents=2)
    assert score(facts).incidents == 2


# ── rank() ──────────────────────────────────────────────────────────────

def test_a_failing_venue_never_appears_whatever_its_apy():
    failing = _facts("high-apy-scam", audited=False, apy=99.0)
    passing = _facts("safe", apy=5.0)
    ranked = rank((failing, passing), as_of=AS_OF, by_slug={})
    assert [r.facts.slug for r in ranked] == ["safe"]


def test_an_outlier_pool_never_appears():
    outlier = _facts("too-good", apy=500.0, outlier=True)
    normal = _facts("normal", apy=5.0)
    ranked = rank((outlier, normal), as_of=AS_OF, by_slug={})
    assert [r.facts.slug for r in ranked] == ["normal"]


def test_rank_sorts_by_apy_descending_slug_as_tie_break():
    a = _facts("aave-v3", apy=4.0)
    b = _facts("compound-v3", apy=6.0)
    c = _facts("sky", apy=6.0)
    ranked = rank((a, b, c), as_of=AS_OF, by_slug={})
    assert [r.facts.slug for r in ranked] == ["compound-v3", "sky", "aave-v3"]
