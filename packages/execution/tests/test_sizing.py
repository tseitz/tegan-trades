"""Risk-based sizing: the same dollar amount at risk on every trade, whatever the zone width.

The point of sizing off the engine's own stop rather than a fixed notional is that
``Candidate`` zones vary enormously in width — a weekly order block can be 10x a daily one.
A fixed notional would silently risk 10x more on the wider zone.
"""
from __future__ import annotations

import pytest

from execution.sizing import risk_of, size_for_risk


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


# ── the notional cap ────────────────────────────────────────────────────────────────────────

def test_notional_cap_limits_a_very_tight_stop():
    """A stop a hair from entry would otherwise demand enormous leverage.

    $100 of risk over a $1 stop distance is 100 units — at $3,200 that is $320,000 of
    notional against $10,000 of equity, i.e. 32x. The cap is what keeps a tight zone from
    turning a modest risk budget into a liquidation.
    """
    uncapped = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_199)
    capped = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_199,
                           max_notional_frac=1.0)
    assert uncapped > capped
    assert capped * 3_200 == pytest.approx(10_000)


def test_notional_cap_does_not_bind_on_a_normal_stop():
    """The cap must be inert in the ordinary case, or it is silently resizing every trade."""
    plain = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_050)
    capped = size_for_risk(equity=10_000, risk_pct=0.01, entry=3_200, stop=3_050,
                           max_notional_frac=1.0)
    assert plain == pytest.approx(capped)


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
