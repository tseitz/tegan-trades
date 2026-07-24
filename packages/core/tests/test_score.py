import pytest

from core.grade import Grade, Pending, Ungradeable
from core.score import (
    MIN_SAMPLE,
    best_static_baseline,
    bootstrap_ci,
    fold_restatements,
    group_scores,
    score_person,
)


def _grade(*, signed, null=None, benchmark=None, correct=True, person="P", asset="BTC",
           direction="long", timeframe="swing", tid="t"):
    null = signed if null is None else null
    from datetime import date

    return Grade(
        thesis_id=tid, asset=asset, person=person, direction=direction, timeframe=timeframe,
        entry_date=date(2025, 1, 1), exit_date=date(2025, 1, 31), entry=100.0, exit=120.0,
        market_return=null, signed_return=signed, correct=correct,
        null_return=null, null_correct=null > 0,
        benchmark_return=benchmark,
        excess_return=None if benchmark is None else signed - benchmark,
    )


def _pending(person="P"):
    from datetime import date

    return Pending(thesis_id="p", asset="BTC", person=person, resolves_on=date(2027, 1, 1))


def _ungradeable(person="P", reason="unpriceable"):
    return Ungradeable(thesis_id="u", asset="ALTS", person=person, reason=reason)


# ── counting ────────────────────────────────────────────────────────────────

def test_pending_and_ungradeable_are_counted_but_excluded_from_n():
    """They must never be folded into the graded population — that would convert 'we
    don't know yet' into evidence about the person."""
    outcomes = [_grade(signed=0.1)] * 3 + [_pending()] * 5 + [_ungradeable()] * 2
    s = score_person("P", outcomes, min_sample=1)
    assert (s.n, s.n_pending, s.n_ungradeable) == (3, 5, 2)


def test_empty_input_is_insufficient_not_a_crash():
    s = score_person("P", [], min_sample=1)
    assert s.n == 0 and s.insufficient_sample is True


def test_below_min_sample_is_flagged():
    outcomes = [_grade(signed=0.1)] * (MIN_SAMPLE - 1)
    assert score_person("P", outcomes).insufficient_sample is True
    outcomes = [_grade(signed=0.1)] * MIN_SAMPLE
    assert score_person("P", outcomes).insufficient_sample is False


# ── hit rates ───────────────────────────────────────────────────────────────

def test_hit_rate_against_null_hit_rate():
    outcomes = [
        _grade(signed=0.1, null=0.1, correct=True),
        _grade(signed=-0.1, null=-0.1, correct=False),
        _grade(signed=0.2, null=-0.2, correct=True),   # a short that worked
    ]
    s = score_person("P", outcomes, min_sample=1)
    assert s.hit_rate == pytest.approx(2 / 3)
    assert s.null_hit_rate == pytest.approx(1 / 3)   # always-long wins only once


# ── the two edges ───────────────────────────────────────────────────────────

def test_direction_edge_is_zero_for_a_long_only_caller():
    """Structural, and the single most important caveat in the whole report: for a long
    call the signed return *is* the always-long return, so a long-only caller cannot score
    anything but zero here. It means 'no evidence of directional skill beyond buy-and-
    hold', not 'no skill' — which is why benchmark_edge exists alongside it."""
    outcomes = [_grade(signed=r, null=r, direction="long") for r in (0.3, -0.2, 0.5)]
    assert score_person("P", outcomes, min_sample=1).direction_edge == pytest.approx(0.0)


def test_direction_edge_rewards_shorts_that_worked():
    outcomes = [_grade(signed=0.2, null=-0.2, direction="short")]
    assert score_person("P", outcomes, min_sample=1).direction_edge == pytest.approx(0.4)


def test_benchmark_edge_is_mean_excess_over_the_benchmark():
    outcomes = [
        _grade(signed=0.30, benchmark=0.10),   # +20
        _grade(signed=0.05, benchmark=0.15),   # -10
    ]
    s = score_person("P", outcomes, min_sample=1)
    assert s.benchmark_edge == pytest.approx(0.05)
    assert s.n_with_benchmark == 2


def test_benchmark_edge_ignores_grades_lacking_a_benchmark():
    outcomes = [_grade(signed=0.30, benchmark=0.10), _grade(signed=99.0, benchmark=None)]
    s = score_person("P", outcomes, min_sample=1)
    assert s.n_with_benchmark == 1
    assert s.benchmark_edge == pytest.approx(0.20)


def test_benchmark_edge_is_none_when_nothing_has_a_benchmark():
    s = score_person("P", [_grade(signed=0.1)], min_sample=1)
    assert s.benchmark_edge is None and s.benchmark_edge_ci is None


def test_long_share_is_reported_for_interpreting_direction_edge():
    outcomes = [_grade(signed=0.1, direction="long")] * 3 + [
        _grade(signed=0.1, direction="short")
    ]
    assert score_person("P", outcomes, min_sample=1).long_share == pytest.approx(0.75)


# ── bootstrap CI ────────────────────────────────────────────────────────────

def test_bootstrap_ci_is_deterministic_for_a_given_seed():
    values = [0.1, -0.2, 0.3, 0.05, -0.01] * 10
    assert bootstrap_ci(values, seed=7) == bootstrap_ci(values, seed=7)


def test_bootstrap_ci_brackets_the_sample_mean():
    values = [0.1, -0.2, 0.3, 0.05, -0.01] * 10
    lo, hi = bootstrap_ci(values, seed=7)
    mean = sum(values) / len(values)
    assert lo < mean < hi


def test_bootstrap_ci_of_identical_values_is_degenerate():
    lo, hi = bootstrap_ci([0.2] * 40, seed=1)
    assert lo == pytest.approx(0.2) and hi == pytest.approx(0.2)


def test_bootstrap_ci_of_empty_is_none():
    assert bootstrap_ci([], seed=1) is None


def test_noisy_sample_yields_a_wider_interval_than_a_tight_one():
    """The CI is what stops a lucky 30-call run being read as skill."""
    tight = bootstrap_ci([0.10, 0.11, 0.09, 0.10] * 10, seed=3)
    noisy = bootstrap_ci([0.9, -0.8, 0.7, -0.6] * 10, seed=3)
    assert (noisy[1] - noisy[0]) > (tight[1] - tight[0])


# ── grouping ────────────────────────────────────────────────────────────────

def test_group_scores_splits_by_person():
    outcomes = [_grade(signed=0.1, person="A")] * 2 + [_grade(signed=0.2, person="B")]
    scores = group_scores(outcomes, min_sample=1)
    assert set(scores) == {"A", "B"}
    assert scores["A"].n == 2 and scores["B"].n == 1


def test_group_scores_routes_pending_to_the_right_person():
    outcomes = [_grade(signed=0.1, person="A"), _pending(person="B")]
    scores = group_scores(outcomes, min_sample=1)
    assert scores["B"].n_pending == 1 and scores["A"].n_pending == 0


def test_group_scores_accepts_a_custom_key_for_breakdowns():
    outcomes = [_grade(signed=0.1, asset="BTC"), _grade(signed=0.2, asset="ETH")]
    scores = group_scores(outcomes, key=lambda o: o.asset, min_sample=1)
    assert set(scores) == {"BTC", "ETH"}


# ── restatement folding ─────────────────────────────────────────────────────

def _row(person="P", asset="BTC", direction="long", timeframe="swing", published_at="2025-01-01",
         tid="t"):
    from types import SimpleNamespace

    return SimpleNamespace(id=tid, person=person, asset=asset, direction=direction,
                           timeframe=timeframe, published_at=published_at)


def test_fold_collapses_a_weekly_restatement_into_one_call():
    rows = [_row(published_at=d, tid=d) for d in
            ("2025-01-01", "2025-01-08", "2025-01-15", "2025-01-22")]
    assert len(fold_restatements(rows)) == 1


def test_fold_keeps_the_earliest_statement_not_the_latest():
    """Grading credits the call to when the information first arrived."""
    rows = [_row(published_at="2025-01-15", tid="late"), _row(published_at="2025-01-01", tid="early")]
    assert [r.id for r in fold_restatements(rows)] == ["early"]


def test_fold_starts_a_new_call_beyond_the_window():
    rows = [_row(published_at="2025-01-01", tid="a"), _row(published_at="2025-03-01", tid="b")]
    assert {r.id for r in fold_restatements(rows)} == {"a", "b"}


def test_fold_does_not_merge_across_direction_or_timeframe():
    """A swing long and a position long on the same asset are separate views these people
    routinely hold at once — folding them would erase a real distinction."""
    rows = [
        _row(direction="long", timeframe="swing", tid="a"),
        _row(direction="short", timeframe="swing", tid="b"),
        _row(direction="long", timeframe="position", tid="c"),
    ]
    assert len(fold_restatements(rows)) == 3


def test_fold_does_not_merge_across_people():
    rows = [_row(person="A", tid="a"), _row(person="B", tid="b")]
    assert len(fold_restatements(rows)) == 2


def test_fold_never_drops_undated_rows():
    rows = [_row(published_at="", tid="u"), _row(published_at="2025-01-01", tid="d")]
    assert {r.id for r in fold_restatements(rows)} == {"u", "d"}


def test_fold_anchors_rather_than_chaining_transitively():
    """A steady drip must not merge into one cluster spanning the whole corpus."""
    rows = [_row(published_at=d, tid=d) for d in
            ("2025-01-01", "2025-01-25", "2025-02-18", "2025-03-14")]
    kept = fold_restatements(rows, window_days=30)
    assert [r.id for r in kept] == ["2025-01-01", "2025-02-18"]


# ── static-bias baseline (does a person beat a fixed directional stance?) ───

def test_best_static_baseline_picks_the_strongest_fixed_stance():
    """On a slate where everything fell, always-short is the stance to beat."""
    grades = [_grade(signed=0.0, null=-0.2, benchmark=0.0, direction="neutral") for _ in range(5)]
    direction, value = best_static_baseline(grades)
    assert direction == "short"
    assert value == pytest.approx(0.20)


def test_static_baseline_is_measured_on_the_persons_own_slate():
    """Not a pooled average — each person is compared against a robot trading exactly
    their calls, so asset mix and timing can't flatter anyone."""
    grades = [_grade(signed=0.0, null=0.5, benchmark=0.0, direction="neutral")]
    assert best_static_baseline(grades)[0] == "long"


def test_skill_edge_subtracts_the_best_fixed_stance():
    """The finding that motivated this: a +7% benchmark edge is worthless if a permanently
    short robot earned +11% on the identical calls."""
    grades = [_grade(signed=0.07, null=-0.11, benchmark=0.0, direction="short") for _ in range(40)]
    s = score_person("P", grades, min_sample=1)
    assert s.benchmark_edge == pytest.approx(0.07)
    assert s.best_static_edge == pytest.approx(0.11)
    assert s.skill_edge == pytest.approx(-0.04)
    assert s.best_static_direction == "short"


def test_skill_edge_is_positive_only_when_beating_every_fixed_stance():
    # market flat, person still extracts return -> genuine selection
    grades = [_grade(signed=0.05, null=0.0, benchmark=0.0, direction="long") for _ in range(40)]
    s = score_person("P", grades, min_sample=1)
    assert s.best_static_edge == pytest.approx(0.0)
    assert s.skill_edge == pytest.approx(0.05)


def test_skill_edge_none_without_benchmarks():
    s = score_person("P", [_grade(signed=0.1)], min_sample=1)
    assert s.skill_edge is None and s.best_static_edge is None


def test_skill_edge_has_a_confidence_interval():
    grades = [_grade(signed=r, null=r, benchmark=0.0) for r in (0.1, -0.2, 0.3, 0.05)] * 10
    s = score_person("P", grades, min_sample=1)
    assert s.skill_edge_ci is not None and len(s.skill_edge_ci) == 2
