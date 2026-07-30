"""The pooled risk ceiling: what the whole book has at stake, across venues."""
from __future__ import annotations

import pytest

from execution import portfolio
from execution.portfolio import MAX_PORTFOLIO_RISK, combine, remaining, size_ceiling


# ── combining equity across venues ───────────────────────────────────────────────────────────

def test_equity_pools_across_venues():
    pool = combine({"alpaca": 100_000.0, "hyperliquid": 1_000.0})
    assert pool.equity == 101_000.0
    assert pool.answered == ("alpaca", "hyperliquid")
    assert pool.complete


def test_a_venue_that_did_not_answer_is_named_and_not_counted():
    """It cannot be counted — there is no number — but it must not vanish either. A silent
    venue moves the ceiling in *both* directions at once: its equity is missing from the
    denominator (tighter) and its open risk is missing from the total (looser), so which way
    the answer is wrong is not knowable from here. Naming it is the only honest move.
    """
    pool = combine({"alpaca": 100_000.0, "hyperliquid": None})
    assert pool.equity == 100_000.0
    assert pool.silent == ("hyperliquid",)
    assert not pool.complete
    assert pool.known


def test_a_pool_no_venue_answered_is_unknown_rather_than_zero():
    # Zero equity means a ceiling of zero, which refuses every order in the account. Same
    # asymmetry ``account.parse_account`` is built on: None disables the gate, 0.0 blocks it.
    pool = combine({"alpaca": None, "hyperliquid": None})
    assert not pool.known
    assert pool.silent == ("alpaca", "hyperliquid")


def test_a_negative_equity_does_not_add_room():
    # A debit balance is not a budget — the same flooring ``parse_account`` applies.
    assert combine({"alpaca": -500.0, "hyperliquid": 1_000.0}).equity == 1_000.0


def test_an_empty_desk_is_unknown():
    assert not combine({}).known


# ── what is left to risk ─────────────────────────────────────────────────────────────────────

def test_the_ceiling_is_a_fraction_of_combined_equity():
    pool = combine({"alpaca": 100_000.0})
    assert remaining(pool, spent=0.0, max_risk=0.05) == pytest.approx(5_000.0)


def test_risk_already_at_stake_comes_off_the_top():
    pool = combine({"alpaca": 100_000.0})
    assert remaining(pool, spent=3_000.0, max_risk=0.05) == pytest.approx(2_000.0)


def test_an_over_risked_book_has_no_room_rather_than_negative_room():
    pool = combine({"alpaca": 100_000.0})
    assert remaining(pool, spent=9_000.0, max_risk=0.05) == 0.0


def test_an_unknown_pool_leaves_the_ceiling_off():
    assert remaining(combine({"alpaca": None}), spent=0.0, max_risk=0.05) is None


def test_no_configured_ceiling_leaves_it_off():
    assert remaining(combine({"alpaca": 100_000.0}), spent=0.0, max_risk=None) is None


def test_the_pooled_ceiling_spans_venues_rather_than_being_applied_twice():
    """The reason this is one number and not two. ``risk_pct`` applied per venue risks it once
    per venue, so running both books at 1% risks 2% and nobody typed 2%. cfg/execution.yaml's
    own comment on ``venue`` names this as the thing to revisit before treating them as one
    account, and this is that.
    """
    both = combine({"alpaca": 100_000.0, "hyperliquid": 100_000.0})
    # Risk already taken on Alpaca reduces what Hyperliquid may take, because there is one book.
    assert remaining(both, spent=10_000.0, max_risk=0.05) == pytest.approx(0.0)


# ── from dollars of risk to a size ───────────────────────────────────────────────────────────

def test_the_room_left_converts_to_a_size_at_this_trade_s_stop():
    assert size_ceiling(remaining=1_000.0, entry=100.0, stop=90.0) == pytest.approx(100.0)


def test_a_tighter_stop_buys_a_bigger_position_out_of_the_same_room():
    wide = size_ceiling(remaining=1_000.0, entry=100.0, stop=90.0)
    tight = size_ceiling(remaining=1_000.0, entry=100.0, stop=99.0)
    assert tight is not None and wide is not None and tight > wide


def test_no_ceiling_passes_through_as_no_ceiling():
    assert size_ceiling(remaining=None, entry=100.0, stop=90.0) is None


def test_a_full_book_allows_no_size():
    assert size_ceiling(remaining=0.0, entry=100.0, stop=90.0) == 0.0


def test_a_zone_with_no_stop_distance_raises_rather_than_dividing_by_zero():
    # Matching ``sizing.size_for_risk``: a caller passing this has a bug, and substituting a
    # reasonable number would place a real order on it.
    with pytest.raises(ValueError, match="stop distance"):
        size_ceiling(remaining=1_000.0, entry=100.0, stop=100.0)


# ── is what fits still worth sending ─────────────────────────────────────────────────────────

def _refusal(fitted, wanted=100.0, *, pool=None, spent=4_800.0, min_fill=0.5):
    return portfolio.check_fill(
        fitted=fitted, wanted=wanted,
        pool=pool or combine({"alpaca": 100_000.0}), spent=spent,
        remaining=200.0, needed=1_000.0, min_fill=min_fill,
    )


def test_an_untouched_order_is_not_refused():
    assert _refusal(100.0) is None


def test_an_order_shrunk_below_the_floor_is_refused():
    refusal = _refusal(3.0)
    assert refusal is not None
    assert refusal.code == portfolio.REFUSAL_PORTFOLIO_FULL


def test_an_order_shrunk_but_still_meaningful_is_sent():
    assert _refusal(60.0) is None


def test_the_refusal_says_the_book_is_full_and_not_the_account():
    """A different cause and a different remedy from ``budget``'s. That one means this venue has
    no buying power left and calls for cancelling a resting order there; this one means the
    portfolio is carrying all the risk it is allowed to and calls for closing or resolving a
    position — possibly on the other venue entirely. Same rule as ``REFUSAL_NO_HEADROOM``
    against ``dust``.
    """
    refusal = _refusal(3.0)
    assert refusal is not None
    assert "buying power" not in refusal.detail
    assert "risk" in refusal.detail
    assert "max_portfolio_risk" in refusal.detail


def test_the_refusal_names_a_venue_that_could_not_be_counted():
    # Otherwise the reader is told the book is full without being told the total is partial.
    refusal = _refusal(3.0, pool=combine({"alpaca": 100_000.0, "hyperliquid": None}))
    assert refusal is not None
    assert "hyperliquid" in refusal.detail


def test_a_complete_pool_says_nothing_about_missing_venues():
    refusal = _refusal(3.0)
    assert refusal is not None
    assert "not counted" not in refusal.detail


def test_a_wanted_of_zero_is_not_answered_here():
    # That is ``guards.check_size``'s dust refusal, and reporting it as a full book would name
    # the wrong cause on an untouched account.
    assert _refusal(0.0, wanted=0.0) is None


# ── the sitting this exists for ──────────────────────────────────────────────────────────────

def test_the_2026_07_29_sitting_stops_at_five_orders():
    """The measurement in the module docstring, reproduced. Eight brackets went out that night,
    each sized to risk ~1% of the same $100,000, and the account carried 7.94% in aggregate —
    a number that was on nobody's screen until the venue rejected three at the open.

    At a 5% ceiling the first five go in full and the sixth is offered 3.2% of what it asked
    for, which is far under the fill floor. Five sent with a reason, rather than eight sent and
    three killed hours later.
    """
    risks = (999.79, 994.77, 994.68, 984.50, 994.98, 988.26, 987.97, 999.60)
    pool = combine({"alpaca": 100_000.0})
    spent, sent = 0.0, 0
    for risk in risks:
        left = remaining(pool, spent=spent, max_risk=MAX_PORTFOLIO_RISK)
        assert left is not None
        if left >= risk:
            spent += risk
            sent += 1
            continue
        # The sixth. Sized by the room that is left, then judged against the fill floor.
        fill = left / risk
        assert sent == 5
        assert fill < 0.5, f"a {fill:.1%} fill is not the trade that was approved"
        break
    assert spent / 100_000.0 == pytest.approx(0.04968, abs=1e-5)


def test_five_full_size_positions_is_the_same_statement_as_a_twenty_percent_concentration_cap():
    """Why 5% rather than a round guess. ``max_position_frac: 0.20`` already says five positions
    fit a 1x account; at the 1% risk budget five positions risk 5%. The two settings are one
    choice with two names, and a pooled ceiling that disagreed with the concentration cap would
    make one of them dead.
    """
    concentration, risk_pct = 0.20, 0.01
    positions_at_1x = 1 / concentration
    assert MAX_PORTFOLIO_RISK == pytest.approx(positions_at_1x * risk_pct)


# ── Book: the state as one object ────────────────────────────────────────────────────────────

def _book(spent=0.0, *, equity=100_000.0, max_risk=MAX_PORTFOLIO_RISK, silent=False):
    reported = {"alpaca": equity}
    if silent:
        reported["hyperliquid"] = None
    return portfolio.Book(pool=combine(reported), spent=spent, max_risk=max_risk)


def test_a_book_reports_what_it_has_left():
    assert _book(3_000.0).remaining == pytest.approx(2_000.0)


def test_a_book_reports_the_share_already_at_stake():
    assert _book(3_000.0).at_stake == pytest.approx(0.03)


def test_an_empty_pool_has_no_share_at_stake_rather_than_dividing_by_zero():
    assert portfolio.Book(pool=combine({"alpaca": None}), spent=100.0).at_stake == 0.0


def test_a_book_converts_its_room_to_a_size():
    assert _book(4_000.0).size_ceiling(entry=100.0, stop=90.0) == pytest.approx(100.0)


def test_a_book_with_the_ceiling_off_caps_nothing():
    assert _book(max_risk=None).size_ceiling(entry=100.0, stop=90.0) is None


def test_a_book_with_the_ceiling_off_refuses_nothing():
    # And this is the case that must not be a refusal: an unreadable pool switching the gate to
    # "no room" would refuse every order in the account.
    assert _book(max_risk=None).check_fill(
        fitted=1.0, wanted=100.0, needed=1_000.0, min_fill=0.5) is None


def test_a_book_refuses_a_fill_under_the_floor():
    refusal = _book(4_900.0).check_fill(fitted=3.0, wanted=100.0, needed=1_000.0, min_fill=0.5)
    assert refusal is not None and refusal.code == portfolio.REFUSAL_PORTFOLIO_FULL
