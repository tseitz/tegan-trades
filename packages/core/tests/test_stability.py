import pytest
from core.stability import kendall_tau, rank_stability


def test_identical_orderings_are_perfectly_concordant():
    assert kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == pytest.approx(1.0)


def test_reversed_orderings_are_perfectly_discordant():
    assert kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == pytest.approx(-1.0)


def test_single_swap_is_partially_concordant():
    tau = kendall_tau(["a", "b", "c"], ["b", "a", "c"])
    assert 0.0 < tau < 1.0


def test_tau_uses_only_names_present_in_both():
    """Longer horizons push people below the min-sample floor, so the two orderings being
    compared genuinely differ in membership. Comparing over the intersection keeps the
    number meaningful instead of silently penalising dropouts."""
    assert kendall_tau(["a", "b", "c"], ["b", "a"]) == pytest.approx(-1.0)


def test_fewer_than_two_common_names_is_undefined():
    assert kendall_tau(["a"], ["a"]) is None
    assert kendall_tau(["a", "b"], ["c", "d"]) is None


def test_rank_stability_calls_churn_only_below_the_noise_floor():
    """The whole point: a ranking that reshuffles no more than resampling the same data
    would is not evidence that the swept parameter matters."""
    assert rank_stability(observed=0.75, noise_floor=(0.28, 0.89)) == "within-noise"
    assert rank_stability(observed=0.05, noise_floor=(0.28, 0.89)) == "beyond-noise"


def test_rank_stability_needs_a_noise_floor_to_judge():
    assert rank_stability(observed=0.5, noise_floor=None) == "unknown"


def test_a_hairs_breadth_below_the_floor_is_marginal_not_a_verdict():
    """Observed 0.43 vs a floor of 0.44 is a tie. Reporting that as 'beyond-noise' would
    turn a rounding difference into a conclusion."""
    assert rank_stability(observed=0.43, noise_floor=(0.44, 0.89)) == "marginal"


def test_clearly_below_the_floor_is_still_beyond_noise():
    assert rank_stability(observed=0.10, noise_floor=(0.44, 0.89)) == "beyond-noise"
