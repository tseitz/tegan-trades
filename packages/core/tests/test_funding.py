from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from core.funding import (
    FundingRate,
    annualized,
    carry_adjusted_rr,
    carry_cost,
    summarize,
)


def _rate(rate: float, hours: float = 1.0, venue: str = "hyperliquid", symbol: str = "NVDA"):
    return FundingRate(
        venue=venue,
        symbol=symbol,
        rate=rate,
        interval_hours=hours,
        observed_at=datetime(2026, 7, 27, 20, 36, tzinfo=UTC),
    )


# ── annualization ────────────────────────────────────────────────────────────

def test_hourly_rate_annualizes_over_8760_hours():
    # 2.546e-05/hr is roughly 22.3%/yr — the live NVDA reading on Hyperliquid.
    assert annualized(2.546e-05, 1.0) == pytest.approx(0.2230, abs=1e-3)


def test_eight_hour_rate_is_charged_three_times_a_day_not_twenty_four():
    # The whole reason interval_hours is stored. Same rate, 8x apart.
    assert annualized(1e-04, 8.0) == pytest.approx(annualized(1e-04, 1.0) / 8)


def test_negative_rate_annualizes_negative():
    assert annualized(-1e-04, 1.0) < 0


def test_zero_interval_is_refused_rather_than_dividing_by_zero():
    with pytest.raises(ValueError):
        annualized(1e-04, 0.0)


def test_funding_rate_exposes_its_own_annualized_view():
    assert _rate(2.546e-05).annualized == pytest.approx(0.2230, abs=1e-3)


# ── sign convention: the load-bearing fact ───────────────────────────────────

def test_positive_rate_costs_a_long_and_pays_a_short():
    long_cost = carry_cost(0.223, days=21, side="long")
    short_cost = carry_cost(0.223, days=21, side="short")
    assert long_cost > 0        # the long pays
    assert short_cost < 0       # the short is paid
    assert long_cost == pytest.approx(-short_cost)


def test_negative_rate_pays_a_long_and_costs_a_short():
    # MU printed -19.1%/yr live: shorts were paying longs.
    assert carry_cost(-0.191, days=21, side="long") < 0
    assert carry_cost(-0.191, days=21, side="short") > 0


def test_carry_scales_linearly_with_holding_period():
    assert carry_cost(0.223, days=42, side="long") == pytest.approx(
        2 * carry_cost(0.223, days=21, side="long")
    )


def test_zero_holding_period_costs_nothing():
    assert carry_cost(0.223, days=0, side="long") == 0.0


def test_unknown_side_is_refused():
    with pytest.raises(ValueError):
        carry_cost(0.223, days=21, side="sideways")


# ── the reason any of this matters: carry moves R:R ──────────────────────────

def test_carry_degrades_a_long_and_improves_a_short_at_identical_price_levels():
    # NVDA at +22.3%/yr, a 3-week hold, 8% target / 4% stop -> nominal R:R 2.0.
    long_rr = carry_adjusted_rr(0.08, 0.04, 0.223, days=21, side="long")
    short_rr = carry_adjusted_rr(0.08, 0.04, 0.223, days=21, side="short")

    assert long_rr.nominal == pytest.approx(2.0)
    assert short_rr.nominal == pytest.approx(2.0)
    assert long_rr.ratio == pytest.approx(1.271, abs=1e-3)
    assert short_rr.ratio == pytest.approx(3.416, abs=1e-3)
    # Same levels, same thesis, 2.7x difference in realised edge.
    assert short_rr.ratio / long_rr.ratio == pytest.approx(2.69, abs=1e-2)


def test_zero_funding_leaves_reward_risk_untouched():
    rr = carry_adjusted_rr(0.08, 0.04, 0.0, days=21, side="long")
    assert rr.ratio == pytest.approx(rr.nominal)
    assert rr.carry == 0.0


def test_high_carry_can_sink_a_two_to_one_below_one():
    # HOOD printed 38.6%/yr. A nominal 2.0 setup does not survive three weeks of it.
    rr = carry_adjusted_rr(0.08, 0.04, 0.386, days=21, side="long")
    assert rr.nominal == pytest.approx(2.0)
    assert rr.ratio < 1.0


def test_carry_exceeding_the_target_kills_the_trade_and_says_so():
    rr = carry_adjusted_rr(0.02, 0.04, 1.5, days=21, side="long")
    assert rr.reward < 0
    assert rr.ratio is not None and rr.ratio < 0
    assert rr.carry_dominates


def test_credit_wider_than_the_stop_yields_no_defined_ratio_rather_than_infinity():
    # A carry credit larger than the stop distance means the position pays for its own
    # stop. Real, but the ratio is undefined -- refuse it rather than return inf.
    rr = carry_adjusted_rr(0.08, 0.01, 0.50, days=21, side="short")
    assert rr.risk <= 0
    assert rr.ratio is None
    assert rr.carry_dominates


def test_negative_or_zero_risk_input_is_refused():
    with pytest.raises(ValueError):
        carry_adjusted_rr(0.08, 0.0, 0.1, days=21, side="long")


# ── distribution, which is how you bet without knowing the future rate ───────

def test_summarize_reports_the_spread_not_just_the_mean():
    rates = [_rate(r / 1e6) for r in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)]
    stats = summarize(rates)
    assert stats.n == 10
    assert stats.median == pytest.approx(annualized(55e-06, 1.0))
    assert stats.p10 < stats.median < stats.p90


def test_summarize_normalizes_across_venues_with_different_intervals():
    # Same economic rate quoted two ways must summarize identically.
    hourly = [_rate(1e-05, hours=1.0) for _ in range(5)]
    eight_hourly = [_rate(8e-05, hours=8.0, venue="aster") for _ in range(5)]
    assert summarize(hourly).median == pytest.approx(summarize(eight_hourly).median)


def test_summarize_of_nothing_is_empty_not_a_crash():
    stats = summarize([])
    assert stats.n == 0
    assert stats.median is None
    assert stats.p90 is None


def test_summarize_single_observation_has_no_spread():
    stats = summarize([_rate(1e-05)])
    assert stats.n == 1
    assert stats.median == stats.p10 == stats.p90


def test_funding_rate_is_immutable():
    with pytest.raises(FrozenInstanceError):
        _rate(1e-05).rate = 2e-05  # type: ignore[misc]
