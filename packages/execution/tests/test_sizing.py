"""Risk-based sizing: the same dollar amount at risk on every trade, whatever the zone width.

The point of sizing off the engine's own stop rather than a fixed notional is that
``Candidate`` zones vary enormously in width — a weekly order block can be 10x a daily one.
A fixed notional would silently risk 10x more on the wider zone.
"""
from __future__ import annotations

import pytest
from execution.sizing import (
    CAP_BUDGET,
    CAP_CONCENTRATION,
    CAP_LEVERAGE,
    apply_caps,
    breakeven_win_rate,
    kelly_risk_pct,
    notional_ceiling,
    risk_of,
    size_for_risk,
)


def test_sizes_to_the_risk_budget():
    """1% of $10,000 is $100; a $150-wide stop buys 0.667 units."""
    size = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_050)
    assert size == pytest.approx(100 / 150)


def test_is_direction_agnostic():
    """A short's stop sits above its entry; only the distance matters."""
    long_size = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_050)
    short_size = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_050, stop=3_200)
    assert long_size == pytest.approx(short_size)


def test_wider_stop_buys_less():
    """The property that makes this worth doing at all."""
    tight = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_150)
    wide = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=2_800)
    assert tight > wide
    # ...and both risk the same amount.
    assert risk_of(tight, entry=3_200, stop=3_150) == pytest.approx(100)
    assert risk_of(wide, entry=3_200, stop=2_800) == pytest.approx(100)


# ── the notional ceilings ───────────────────────────────────────────────────────────────────

def test_notional_cap_limits_a_very_tight_stop():
    """A stop a hair from entry would otherwise demand enormous leverage.

    $100 of risk over a $1 stop distance is 100 units — at $3,200 that is $320,000 of
    notional against $10,000 of equity, i.e. 32x. The cap is what keeps a tight zone from
    turning a modest risk budget into a liquidation.
    """
    uncapped = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_199)
    ceiling = notional_ceiling(equity=10_000, entry=3_200, frac=1.0)
    capped, reason = apply_caps(uncapped, [(CAP_LEVERAGE, ceiling)])
    assert uncapped > capped
    assert capped * 3_200 == pytest.approx(10_000)
    assert reason == CAP_LEVERAGE


def test_notional_cap_does_not_bind_on_a_normal_stop():
    """The cap must be inert in the ordinary case, or it is silently resizing every trade."""
    plain = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_050)
    capped, reason = apply_caps(
        plain, [(CAP_LEVERAGE, notional_ceiling(equity=10_000, entry=3_200, frac=1.0))]
    )
    assert plain == pytest.approx(capped)
    assert reason is None


def test_an_absent_ceiling_is_not_a_zero_one():
    """``None`` means the caller did not opt in. Treated as a number it would refuse every
    order, which is the failure mode every optional value in this package is written against."""
    assert notional_ceiling(equity=10_000, entry=3_200, frac=None) is None
    plain = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_050)
    assert apply_caps(plain, [(CAP_LEVERAGE, None)]) == (plain, None)


def test_a_non_positive_fraction_is_refused():
    with pytest.raises(ValueError, match="fraction"):
        notional_ceiling(equity=10_000, entry=3_200, frac=0)


# ── which ceiling bound ─────────────────────────────────────────────────────────────────────

def test_the_tightest_ceiling_wins_and_names_itself():
    """The reason is the whole point of routing four caps through one function: an order that
    came out a quarter of the requested size needs to say which of four unrelated facts did
    that, because each one calls for a different response."""
    size, reason = apply_caps(100.0, [
        (CAP_LEVERAGE, 90.0),
        (CAP_CONCENTRATION, 25.0),
        (CAP_BUDGET, 60.0),
    ])
    assert size == 25.0
    assert reason == CAP_CONCENTRATION


def test_a_ceiling_equal_to_the_request_is_not_a_cap():
    """It changed nothing, so naming it would explain a difference that is not there."""
    assert apply_caps(100.0, [(CAP_CONCENTRATION, 100.0)]) == (100.0, None)


# ── refusals ────────────────────────────────────────────────────────────────────────────────

def test_zero_stop_distance_is_refused():
    """Not clamped to a huge number: an entry equal to its stop is a broken candidate, and
    sizing it would divide by zero and produce an unbounded position."""
    with pytest.raises(ValueError, match="stop"):
        size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_200)


@pytest.mark.parametrize("equity", [0, -5_000])
def test_non_positive_equity_is_refused(equity):
    with pytest.raises(ValueError, match="equity"):
        size_for_risk(equity=equity, risk_pct=0.01, entry=3_200, stop=3_050)


@pytest.mark.parametrize("risk_pct", [0, -0.01, 1.5])
def test_risk_pct_outside_zero_to_one_is_refused(risk_pct):
    """1.5 would risk 150% of the account on a single trade — a typo, not an intention."""
    with pytest.raises(ValueError, match="risk_pct"):
        size_for_risk(equity=10_000, risk_pct=risk_pct, entry=3_200, stop=3_050)


def test_risk_of_is_the_inverse_of_sizing():
    """``risk_of`` reports what a *rounded* size actually risks, which is what the preview
    shows — after rounding, the realised risk is no longer exactly the budget."""
    size = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_050)
    assert risk_of(size, entry=3_200, stop=3_050) == pytest.approx(100)


# ── Kelly ───────────────────────────────────────────────────────────────────
#
# These pin the ARITHMETIC, not the edge. Every `p` here is a fixture chosen to sit on a
# named side of break-even; none of them is a claim about what this engine wins. The measured
# figure lives in `cfg/execution.yaml` and is produced by `scripts/probe_evidence.py`.

def test_kelly_is_zero_when_the_edge_is_absent():
    """The case that matters most today. Below break-even, Kelly returns no bet — never a
    small one. A negative fraction means the bet is backwards, and quartering a negative
    number to make it "safe" would size a trade the formula just refused."""
    assert kelly_risk_pct(win_rate=0.115, reward_risk=3.08) == 0.0


def test_break_even_is_where_kelly_crosses_zero():
    """`p = 1/(1+b)` is the definition, so it must land exactly on zero rather than near it."""
    assert kelly_risk_pct(win_rate=1 / (1 + 3.0), reward_risk=3.0) == pytest.approx(0.0)


def test_a_real_edge_sizes_and_is_quartered():
    """p=0.40, b=3.0 -> f = 0.40 - 0.60/3.0 = 0.20; a quarter of that is 5%."""
    full = kelly_risk_pct(win_rate=0.40, reward_risk=3.0, fraction=1.0, cap=1.0)
    assert full == pytest.approx(0.20)
    assert kelly_risk_pct(win_rate=0.40, reward_risk=3.0, fraction=0.25, cap=1.0) == pytest.approx(0.05)


def test_the_cap_binds_before_a_large_payoff_runs_away():
    """Kelly is unbounded as `b` grows, and a 20R target is a forecast rather than a fill.
    Without the cap this asks for 9.4% of equity on one trade."""
    assert kelly_risk_pct(win_rate=0.40, reward_risk=20.0, fraction=0.25, cap=0.02) == 0.02


def test_a_bigger_payoff_earns_a_bigger_bet_at_the_same_win_rate():
    """The whole reason Kelly replaces a flat percentage: `b` is the input we actually know
    per candidate, so it is what must move the size."""
    lean = kelly_risk_pct(win_rate=0.40, reward_risk=2.0, cap=1.0)
    rich = kelly_risk_pct(win_rate=0.40, reward_risk=5.0, cap=1.0)
    assert rich > lean > 0


def test_a_higher_win_rate_earns_a_bigger_bet_at_the_same_payoff():
    assert (kelly_risk_pct(win_rate=0.50, reward_risk=3.0, cap=1.0)
            > kelly_risk_pct(win_rate=0.35, reward_risk=3.0, cap=1.0) > 0)


@pytest.mark.parametrize("win_rate", [-0.01, 1.01])
def test_a_win_rate_outside_zero_to_one_is_refused(win_rate):
    """A caller passing 40 for "40%" has a bug, and clamping it would size a real order."""
    with pytest.raises(ValueError):
        kelly_risk_pct(win_rate=win_rate, reward_risk=3.0)


@pytest.mark.parametrize("reward_risk", [0.0, -1.0])
def test_a_non_positive_payoff_is_refused(reward_risk):
    """`b <= 0` makes the formula divide by zero or invert; both are caller bugs."""
    with pytest.raises(ValueError):
        kelly_risk_pct(win_rate=0.4, reward_risk=reward_risk)


def test_certainty_is_capped_rather_than_betting_everything():
    """p=1 sends full Kelly to 1.0 — bet the account. Nothing is certain, so the cap is what
    stands between a bad `p` estimate and the whole balance."""
    assert kelly_risk_pct(win_rate=1.0, reward_risk=3.0, fraction=1.0, cap=0.02) == 0.02


def test_breakeven_win_rate_is_the_point_kelly_crosses_zero():
    """The two functions must agree, or the queue could show a break-even a trade clears
    while the sizer refuses it."""
    for b in (1.83, 2.23, 3.0, 6.62):
        assert kelly_risk_pct(win_rate=breakeven_win_rate(b), reward_risk=b) == 0.0
        assert kelly_risk_pct(win_rate=breakeven_win_rate(b) + 0.05, reward_risk=b) > 0


def test_a_bigger_payoff_needs_a_lower_win_rate():
    assert breakeven_win_rate(1.0) == pytest.approx(0.5)
    assert breakeven_win_rate(3.0) == pytest.approx(0.25)


@pytest.mark.parametrize("reward_risk", [0.0, -1.0])
def test_breakeven_refuses_a_non_positive_payoff(reward_risk):
    with pytest.raises(ValueError):
        breakeven_win_rate(reward_risk)
