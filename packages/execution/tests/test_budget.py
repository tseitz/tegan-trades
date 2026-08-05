"""The account-level total that nothing computed. See ``execution.budget``.

The behaviour under test is deliberately two-sided: an order that *nearly* fits is shrunk to
what is left, and one that barely fits at all is refused rather than sent as a token position.
"""
from __future__ import annotations

import pytest
from execution.budget import (
    MIN_BUDGET_FILL,
    REFUSAL_NO_HEADROOM,
    affordable,
    check_fill,
)


def test_affordable_is_the_shares_the_room_pays_for():
    assert affordable(10_000.0, entry=250.0) == pytest.approx(40.0)


def test_no_room_affords_nothing():
    assert affordable(0.0, entry=250.0) == 0.0


# ── the floor ───────────────────────────────────────────────────────────────────────────────

def test_an_order_that_mostly_fits_is_shrunk_not_refused():
    """80% of the intended size still carries 80% of the intended risk, which is a trade."""
    assert check_fill(fitted=80.0, wanted=100.0, headroom=8_000.0, needed=10_000.0) is None


def test_an_order_that_barely_fits_is_refused():
    """At 10% of the intended size the risk taken is decided by where the candidate happened
    to fall in tonight's queue rather than by anything about the trade."""
    refusal = check_fill(fitted=10.0, wanted=100.0, headroom=1_000.0, needed=10_000.0)
    assert refusal is not None
    assert refusal.code == REFUSAL_NO_HEADROOM


def test_the_refusal_names_the_room_the_order_and_the_shortfall():
    """A refusal a person cannot act on is a refusal that gets ignored. All three numbers are
    needed to decide what to cancel."""
    refusal = check_fill(fitted=10.0, wanted=100.0, headroom=1_000.0, needed=10_000.0)
    assert "1,000" in refusal.detail
    assert "10,000" in refusal.detail
    assert "10%" in refusal.detail


def test_the_floor_is_inclusive():
    """Exactly half the intended size carries exactly half the risk, which the chosen floor
    admits. Written down because the boundary is where a default gets silently re-tuned."""
    assert MIN_BUDGET_FILL == 0.5
    assert check_fill(fitted=50.0, wanted=100.0, headroom=5_000.0, needed=10_000.0) is None
    assert check_fill(fitted=49.0, wanted=100.0, headroom=4_900.0, needed=10_000.0) is not None


def test_a_full_account_refuses_rather_than_sending_nothing():
    """Zero room must be a named refusal, not a dust refusal — the two call for opposite
    responses. Dust means the risk budget is too small for this market; this means the
    account is full and something has to be cancelled first."""
    refusal = check_fill(fitted=0.0, wanted=100.0, headroom=0.0, needed=10_000.0)
    assert refusal is not None
    assert refusal.code == REFUSAL_NO_HEADROOM


def test_the_floor_is_configurable_and_can_be_switched_off():
    """A floor of zero means "shrink to whatever fits", which is a legitimate policy — it is
    just not the default, because the size it produces is an artefact of queue order."""
    assert check_fill(fitted=1.0, wanted=100.0, headroom=100.0, needed=10_000.0,
                      min_fill=0.0) is None


def test_a_wanted_size_of_zero_is_not_this_gates_problem():
    """A risk budget that rounds to nothing is ``check_size``'s dust refusal. Answering it
    here too would report the account as full when it is untouched."""
    assert check_fill(fitted=0.0, wanted=0.0, headroom=50_000.0, needed=0.0) is None
