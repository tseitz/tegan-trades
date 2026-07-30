from dataclasses import dataclass

import pytest
from core.gaps import (
    SHRINK_SESSIONS,
    GapCost,
    adverse_excess,
    measure,
    measure_from_gaps,
    overnight_gaps,
    pooled,
    shrink,
)


@dataclass(frozen=True)
class _Bar:
    """Duck-typed like every other bar consumer in ``core`` — see ``core.structure``."""
    open: float
    high: float
    low: float
    close: float


def _pool(excess: float, rate: float = 0.02, assets: int = 5):
    from core.gaps import Pool
    return Pool(excess=excess, rate=rate, assets=assets)


def _bars(*pairs):
    """``(close, next_open)`` pairs flattened into a bar sequence.

    Each bar's own high/low are irrelevant here — a gap is close-to-open and nothing else.
    """
    return tuple(_Bar(open=o, high=max(o, c), low=min(o, c), close=c) for o, c in pairs)


# ── extracting the gap ───────────────────────────────────────────────────────

def test_gap_is_close_to_next_open_not_close_to_close():
    # 100 -> opens 95 is a -5% gap. The intervening close is irrelevant.
    bars = _bars((100.0, 100.0), (95.0, 110.0))
    assert overnight_gaps(bars) == pytest.approx((-0.05,))


def test_one_bar_has_no_gap_to_measure():
    assert overnight_gaps(_bars((100.0, 100.0))) == ()


def test_no_bars_is_empty_rather_than_an_error():
    assert overnight_gaps(()) == ()


def test_gaps_are_signed_so_direction_can_be_applied_later():
    bars = _bars((100.0, 100.0), (105.0, 105.0), (99.75, 99.75))
    up, down = overnight_gaps(bars)
    assert up == pytest.approx(0.05)
    assert down == pytest.approx(-0.05)


def test_a_zero_close_is_skipped_rather_than_dividing_by_zero():
    bars = _bars((100.0, 0.0), (50.0, 50.0))
    assert overnight_gaps(bars) == ()


# ── which side of a gap hurts ────────────────────────────────────────────────

def test_a_long_is_hurt_by_a_gap_down():
    n, excess = adverse_excess((-0.10,), stop_distance=0.04, side="long")
    assert n == 1
    assert excess == pytest.approx(0.06)


def test_a_short_is_hurt_by_a_gap_up():
    n, excess = adverse_excess((0.10,), stop_distance=0.04, side="short")
    assert n == 1
    assert excess == pytest.approx(0.06)


def test_a_gap_the_wrong_way_costs_a_long_nothing():
    # Gapping up is a windfall on a long, not a cost. It must not net against the losses.
    assert adverse_excess((0.10,), stop_distance=0.04, side="long") == (0, 0.0)


def test_a_gap_that_stays_inside_the_stop_costs_nothing_extra():
    # The stop absorbs it — that is what the stop is for. Only the excess is unbudgeted.
    assert adverse_excess((-0.03,), stop_distance=0.04, side="long") == (0, 0.0)


def test_excess_averages_over_every_session_not_only_the_bad_ones():
    # Three quiet sessions and one -10% gap against a 4% stop: 0.06 spread over 4 sessions.
    gaps = (0.0, 0.0, 0.0, -0.10)
    n, excess = adverse_excess(gaps, stop_distance=0.04, side="long")
    assert n == 1
    assert excess == pytest.approx(0.06 / 4)


def test_an_unknown_side_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        adverse_excess((-0.05,), stop_distance=0.04, side="sideways")


# ── shrinkage toward the pool ────────────────────────────────────────────────

def test_a_sample_the_size_of_the_constant_is_weighted_half_and_half():
    assert shrink(own=1.0, n=SHRINK_SESSIONS, pooled=0.0) == pytest.approx(0.5)


def test_a_thin_sample_leans_on_the_pool():
    # 98 sessions is BE/GLNG/WMB's real history. It must not be trusted on its own.
    thin = shrink(own=1.0, n=98, pooled=0.0)
    assert thin < 0.3


def test_a_long_sample_mostly_keeps_its_own_number():
    assert shrink(own=1.0, n=5000, pooled=0.0) > 0.94


def test_an_unmeasured_asset_takes_the_pooled_rate_and_never_zero():
    # The current bug is that Alpaca costs nothing. Unmeasured must not reproduce it.
    assert shrink(own=0.0, n=0, pooled=0.02) == pytest.approx(0.02)


# ── the pool has to be evaluated at the asset's own stop ─────────────────────

def test_the_pool_is_cheaper_at_a_wider_stop():
    # The defect this function exists to prevent: a single pooled scalar charged BE, whose stop
    # is 22.72% wide and which has never been gapped past it, the same cost as a 0.88%-stop
    # asset. Re-scoring the cohort at the caller's stop is what makes the two comparable.
    cohort = [(-0.05, 0.01, -0.02), (-0.10, 0.03, -0.01)]
    tight = pooled(cohort, 0.01, "long")
    wide = pooled(cohort, 0.20, "long")
    assert tight is not None and wide is not None
    assert tight.excess > wide.excess
    assert wide.excess == 0.0


def test_the_pool_ignores_assets_with_no_gaps_rather_than_counting_them_as_calm():
    # An unmeasured asset must not drag the pool toward zero — that is the original bug wearing
    # a different hat.
    with_empty = pooled([(-0.10,), ()], 0.04, "long")
    without = pooled([(-0.10,)], 0.04, "long")
    assert with_empty is not None and without is not None
    assert with_empty.excess == pytest.approx(without.excess)
    assert with_empty.assets == without.assets == 1


def test_an_empty_cohort_has_no_pool_rather_than_a_zero_one():
    assert pooled([], 0.04, "long") is None


def test_the_pool_respects_direction():
    cohort = [(-0.10, -0.10)]
    down = pooled(cohort, 0.04, "long")
    up = pooled(cohort, 0.04, "short")
    assert down is not None and up is not None
    assert down.excess > 0
    assert up.excess == 0.0


# ── assembling the cost ──────────────────────────────────────────────────────

def test_cost_over_a_hold_scales_with_the_days_held():
    cost = GapCost(asset="X", sessions=100, past_stop=1, excess_per_session=0.001,
                   rate=0.01, pooled_weight=0.0)
    assert cost.over(21) == pytest.approx(0.021)
    assert cost.over(42) == pytest.approx(0.042)


def test_at_least_one_gap_compounds_over_the_hold_rather_than_multiplying():
    # Reproduces §35's headline, and pins the two-sided/one-sided distinction that produces it.
    # §35's 3.5% counts gaps past the stop in EITHER direction; only the adverse half can hurt
    # a given position, and 1-(1-0.0175)^21 = 0.31 is where its 31% comes from. ``rate`` here is
    # already one-sided because ``adverse_excess`` filtered it, so 3.5% would compound to 53%.
    cost = GapCost(asset="X", sessions=2000, past_stop=35, excess_per_session=0.0,
                   rate=35 / 2000, pooled_weight=0.0)
    assert cost.rate == pytest.approx(0.0175, abs=1e-4)
    assert cost.at_least_one(21) == pytest.approx(0.31, abs=0.01)


def test_measure_reports_how_much_it_leaned_on_the_pool():
    bars = _bars(*[(100.0, 100.0)] * 99)
    cost = measure("BE", bars, stop_distance=0.045, side="long",
                   pool=_pool(0.002))
    assert cost is not None
    assert cost.sessions == 98
    assert cost.pooled_weight > 0.7


def test_measure_returns_none_when_there_is_nothing_to_measure_and_no_pool():
    # Mirrors AlpacaBroker.liquidity: None means "not measured", which the caller must handle
    # rather than silently reading as free.
    assert measure("NEW", (), stop_distance=0.045, side="long", pool=None) is None


def test_measure_falls_back_to_the_pool_for_an_asset_with_no_history():
    cost = measure("PLUME", (), stop_distance=0.045, side="long",
                   pool=_pool(0.003))
    assert cost is not None
    assert cost.sessions == 0
    assert cost.pooled_weight == 1.0
    assert cost.over(21) == pytest.approx(0.063)


# ── the review's findings, pinned ─────────────────────────────────────────────

def test_the_rate_is_shrunk_with_the_cost_so_they_cannot_contradict():
    """MEDIUM from review: ``over()`` was shrunk while ``rate`` was raw, so the module priced
    ``CRM`` at 0.274% of notional beside a 0.0% chance of the gap that causes it (``GLNG``
    0.841%/0.0%, ``VRT`` 0.187%/0.0%). A cost and its probability must move together.
    """
    own = tuple([-0.001] * 98)           # 98 sessions, never past a 4.5% stop
    cost = measure_from_gaps("CRM", own, stop_distance=0.045, side="long",
                             pool=_pool(0.0002, rate=0.03))
    assert cost is not None
    assert cost.over(21) > 0, "leans on the pool for cost"
    assert cost.at_least_one(21) > 0, "so it must lean on the pool for probability too"
    assert cost.observed_rate == 0.0, "while still reporting what this asset itself did"


def test_no_pool_is_distinct_from_a_pool_weight_of_zero():
    """MEDIUM from review: a single-asset cohort left ``pooled_weight`` at 0.0, which reads as
    "long enough history not to need the pool". 98 sessions of noise was reported as fully
    evidenced. ``None`` now means the pool did not exist.
    """
    lone = measure_from_gaps("INTL", tuple([-0.05] * 98), stop_distance=0.02, side="long",
                             pool=None)
    assert lone is not None
    assert lone.pooled_weight is None
    assert not lone.borrowed

    evidenced = measure_from_gaps("X", tuple([-0.05] * 100_000), stop_distance=0.02,
                                  side="long", pool=_pool(0.01))
    assert evidenced is not None
    assert evidenced.pooled_weight == pytest.approx(0.0, abs=0.01)
    assert not evidenced.borrowed


def test_a_mostly_pooled_estimate_says_so():
    thin = measure_from_gaps("BE", tuple([-0.001] * 98), stop_distance=0.045, side="long",
                             pool=_pool(0.002))
    assert thin is not None
    assert thin.pooled_weight is not None and thin.pooled_weight > 0.5
    assert thin.borrowed, "13 of 18 live assets are in this state; the router must be able to say"


def test_the_pool_carries_a_rate_as_well_as_an_excess():
    p = pooled([(-0.10, 0.0), (-0.20, 0.0)], 0.04, "long")
    assert p is not None
    assert p.excess > 0
    assert p.rate == pytest.approx(0.5), "one of two sessions past the stop, per asset"
    assert p.assets == 2
