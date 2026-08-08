"""How a trade ended — which leg took it, at what price, and what it made.

The dependent variable for every calibration question this repo wants to ask. These tests are
mostly about the two ways the arithmetic can lie: an R computed against the wrong denominator,
and a fill that never had to compete for liquidity being counted as evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime

from execution import outcome

# The INTL trade, which is the whole reason this module exists. Placed 2026-07-29, closed by
# hand on 2026-08-07 because 1,639 shares is 8.5x what the participation cap now allows.
ENTRY_ID = "f41cbf96"
TP_ID = "1fa9b483"
SL_ID = "94e89e37"


def _fill(order_id=TP_ID, qty=1639.0, price=31.21, side="sell", symbol="INTL",
          at="2026-08-07T13:39:09.192769Z"):
    return {"order_id": order_id, "qty": str(qty), "price": str(price), "side": side,
            "symbol": symbol, "transaction_time": at, "activity_type": "FILL"}


# ── parsing the venue's activity feed ───────────────────────────────────────────────────────

def test_a_fill_is_read_from_the_activity_feed():
    fills = outcome.parse_fills([_fill()])
    assert len(fills) == 1
    assert fills[0].order_id == TP_ID
    assert fills[0].qty == 1639.0
    assert fills[0].price == 31.21
    assert fills[0].side == "sell"
    assert fills[0].at == datetime(2026, 8, 7, 13, 39, 9, 192769, tzinfo=UTC)


def test_an_unreadable_fill_is_skipped_not_defaulted():
    """A price that will not parse must drop the row. Coerced to zero it would drag the
    weighted average toward nothing and report a loss that never happened."""
    fills = outcome.parse_fills([_fill(price="not-a-number"), _fill()])
    assert len(fills) == 1
    assert fills[0].price == 31.21


def test_a_partial_fill_is_still_a_fill():
    """Alpaca labels the prints of one order ``partial_fill`` until the last, which is
    ``fill``. Both are shares that changed hands."""
    rows = [dict(_fill(qty=699.0), type="partial_fill"), dict(_fill(qty=940.0), type="fill")]
    assert len(outcome.parse_fills(rows)) == 2


def test_a_non_fill_activity_is_ignored():
    fills = outcome.parse_fills([dict(_fill(), activity_type="DIV"), _fill()])
    assert len(fills) == 1


# ── aggregating prints into one close ───────────────────────────────────────────────────────

def test_prints_of_one_order_aggregate_at_a_qty_weighted_price():
    """The real INTL exit: 699 then 940, both at 31.21. A plain mean would be right here by
    coincidence and wrong the moment the prices differ, so weight it."""
    fills = outcome.parse_fills([_fill(qty=699.0, price=31.00), _fill(qty=940.0, price=31.50)])
    close = outcome.close_from_fills(fills, candidate_key="19ba232b91ce",
                                    order_ids=[ENTRY_ID, TP_ID, SL_ID])
    assert close.qty == 1639.0
    assert close.prints == 2
    # (699*31.00 + 940*31.50) / 1639
    assert round(close.price, 6) == round((699 * 31.00 + 940 * 31.50) / 1639, 6)


def test_the_close_time_is_the_last_print():
    fills = outcome.parse_fills([
        _fill(qty=699.0, at="2026-08-07T13:39:08.104410Z"),
        _fill(qty=940.0, at="2026-08-07T13:39:09.192769Z"),
    ])
    close = outcome.close_from_fills(fills, candidate_key="k", order_ids=[ENTRY_ID, TP_ID, SL_ID])
    assert close.at == datetime(2026, 8, 7, 13, 39, 9, 192769, tzinfo=UTC)


def test_no_fills_is_no_close():
    """Not a zero-quantity close — the position is simply still open."""
    assert outcome.close_from_fills((), candidate_key="k", order_ids=[]) is None


# ── which leg took it: exact, from the order id ─────────────────────────────────────────────

def test_a_fill_on_the_take_profit_leg_is_a_target():
    close = outcome.close_from_fills(outcome.parse_fills([_fill(order_id=TP_ID)]),
                                     candidate_key="k", order_ids=[ENTRY_ID, TP_ID, SL_ID])
    assert close.reason == outcome.TARGET


def test_a_fill_on_the_stop_leg_is_a_stop():
    close = outcome.close_from_fills(outcome.parse_fills([_fill(order_id=SL_ID)]),
                                     candidate_key="k", order_ids=[ENTRY_ID, TP_ID, SL_ID])
    assert close.reason == outcome.STOP


def test_a_fill_on_neither_leg_is_manual():
    """Today's close: the bracket was cancelled and the shares sold by hand, so the exit
    order is one this repo never recorded. That is a different outcome from the plan working
    and must never be pooled with one."""
    close = outcome.close_from_fills(outcome.parse_fills([_fill(order_id="c29d3a96")]),
                                     candidate_key="k", order_ids=[ENTRY_ID, TP_ID, SL_ID])
    assert close.reason == outcome.MANUAL


def test_a_log_row_with_no_leg_ids_cannot_name_the_reason():
    """A row predating three-id logging. ``unknown`` rather than ``manual``: not knowing which
    leg filled is a different fact from knowing it was neither."""
    close = outcome.close_from_fills(outcome.parse_fills([_fill()]),
                                     candidate_key="k", order_ids=[])
    assert close.reason == outcome.UNKNOWN


# ── the arithmetic, and the two denominators ────────────────────────────────────────────────

def test_a_long_that_worked_realises_the_move():
    r = outcome.realized(direction="long", entry=29.621233, exit_price=31.21, qty=1639.0,
                         stop=29.19, risk_planned=999.79)
    assert round(r.pnl, 2) == 2603.99


def test_a_short_realises_the_move_in_reverse():
    r = outcome.realized(direction="short", entry=100.0, exit_price=90.0, qty=10.0,
                         stop=105.0, risk_planned=50.0)
    assert r.pnl == 100.0


def test_both_r_denominators_are_reported_because_they_disagree():
    """INTL's entry filled at 29.6212 against a planned 29.80, so the risk actually taken was
    $706.79 and not the $999.79 budgeted. +2.60R and +3.68R are the same trade. Recording one
    silently would bake a choice nobody made into every future calibration.
    """
    r = outcome.realized(direction="long", entry=29.621233, exit_price=31.21, qty=1639.0,
                         stop=29.19, risk_planned=999.79)
    assert round(r.risk_at_fill, 2) == 706.79
    assert round(r.r_planned, 2) == 2.60
    assert round(r.r_at_fill, 2) == 3.68


def test_a_missing_planned_risk_leaves_that_r_absent_rather_than_zero():
    """Same asymmetry as ``store.risk_by_key``: "no risk recorded" and "risked nothing" must
    not read the same, and 0.0 would look like a scratch."""
    r = outcome.realized(direction="long", entry=100.0, exit_price=110.0, qty=1.0,
                         stop=95.0, risk_planned=None)
    assert r.r_planned is None
    assert r.r_at_fill == 2.0


def test_a_stop_at_the_entry_leaves_r_at_fill_absent():
    """A zero denominator. Nothing was at stake to the stop, so R is undefined rather than
    infinite — and dividing would raise inside a nightly step."""
    r = outcome.realized(direction="long", entry=100.0, exit_price=110.0, qty=1.0,
                         stop=100.0, risk_planned=10.0)
    assert r.risk_at_fill == 0.0
    assert r.r_at_fill is None
    assert r.r_planned == 1.0


def test_a_loss_is_negative_r():
    r = outcome.realized(direction="long", entry=100.0, exit_price=95.0, qty=2.0,
                         stop=95.0, risk_planned=10.0)
    assert r.pnl == -10.0
    assert r.r_at_fill == -1.0


# ── was the fill credible? ──────────────────────────────────────────────────────────────────

def test_an_exit_larger_than_the_cap_is_flagged_as_not_credible():
    """The INTL lesson. 1,639 shares against a 19,262-share median session is 8.51%, and the
    participation ceiling is 1% — so this fill did not have to compete for liquidity and says
    nothing about whether the strategy works.
    """
    q = outcome.fill_quality(qty=1639.0, median_volume=19_262.0, ceiling=0.01, paper=True)
    assert round(q.participation, 4) == 0.0851
    assert q.credible is False


def test_an_ordinary_exit_is_credible():
    q = outcome.fill_quality(qty=100.0, median_volume=5_358_410.0, ceiling=0.01, paper=False)
    assert q.credible is True


def test_a_paper_fill_is_never_credible_however_small():
    """Paper matches against the quote without consuming the book. A tiny paper fill is
    realistic *by luck*, and the flag records the venue rather than a guess about it."""
    q = outcome.fill_quality(qty=1.0, median_volume=5_000_000.0, ceiling=0.01, paper=True)
    assert q.credible is False


def test_an_unmeasured_market_leaves_credibility_unknown():
    """``participation.check_depth``'s asymmetry, carried through: not measured must never
    read as measured-and-fine."""
    q = outcome.fill_quality(qty=100.0, median_volume=None, ceiling=0.01, paper=False)
    assert q.participation is None
    assert q.credible is None


# ── picking the exit out of an account-wide feed ─────────────────────────────────────────────
#
# The feed is every print the account made, so the entry's own fills are in it too, alongside
# every other symbol's. Attribution has to be exact: crediting one trade's exit to another
# would corrupt both rows.

def test_the_entry_order_dates_the_position():
    """The reconciled row's timestamp is when reconcile RAN, not when the entry filled — on
    INTL those are seven hours apart. So the entry's own prints are the only trustworthy
    'position opened' marker, and a same-day round trip depends on getting this right."""
    fills = outcome.parse_fills([
        _fill(order_id=ENTRY_ID, side="buy", at="2026-07-29T13:34:57.147565Z"),
        _fill(order_id=TP_ID, at="2026-08-07T13:39:09.192769Z"),
    ])
    assert outcome.entry_end(fills, ENTRY_ID) == datetime(
        2026, 7, 29, 13, 34, 57, 147565, tzinfo=UTC)


def test_an_entry_absent_from_the_feed_cannot_be_dated():
    fills = outcome.parse_fills([_fill(order_id=TP_ID)])
    assert outcome.entry_end(fills, ENTRY_ID) is None


def test_the_entry_is_dated_by_its_last_print():
    """A large entry fills over several prints, and the position is only fully on at the last."""
    fills = outcome.parse_fills([
        _fill(order_id=ENTRY_ID, side="buy", qty=699.0, at="2026-07-29T13:34:50Z"),
        _fill(order_id=ENTRY_ID, side="buy", qty=940.0, at="2026-07-29T13:34:57Z"),
    ])
    assert outcome.entry_end(fills, ENTRY_ID) == datetime(2026, 7, 29, 13, 34, 57, tzinfo=UTC)


def test_only_the_closing_side_of_the_same_symbol_after_the_entry_counts():
    opened = datetime(2026, 7, 29, 13, 34, 57, tzinfo=UTC)
    fills = outcome.parse_fills([
        # The entry itself — same symbol, wrong side.
        _fill(order_id=ENTRY_ID, side="buy", at="2026-07-29T13:34:57Z"),
        # Another symbol entirely.
        _fill(order_id="other", symbol="SBSW", at="2026-08-01T14:00:00Z"),
        # A sell of this symbol from BEFORE this position existed — an earlier, separate trade.
        _fill(order_id="older", at="2026-07-01T14:00:00Z"),
        # The real exit.
        _fill(order_id=TP_ID, at="2026-08-07T13:39:09Z"),
    ])
    exits = outcome.exit_fills(fills, symbol="INTL", entry_order_id=ENTRY_ID,
                               exit_side="sell", after=opened)
    assert [f.order_id for f in exits] == [TP_ID]


def test_a_second_entry_print_landing_after_the_marker_is_not_an_exit():
    """Guards the boundary: ``after`` is exclusive of the entry order itself by id, not only by
    time, so a straggling entry print cannot be mistaken for a partial close."""
    opened = datetime(2026, 7, 29, 13, 34, 57, tzinfo=UTC)
    fills = outcome.parse_fills([
        _fill(order_id=ENTRY_ID, side="buy", at="2026-07-29T13:35:10Z"),
    ])
    assert outcome.exit_fills(fills, symbol="INTL", entry_order_id=ENTRY_ID,
                             exit_side="sell", after=opened) == ()


def test_exits_group_by_the_order_that_caused_them():
    """A position can come off in pieces — the target takes half and the rest is sold by hand.
    Those are two different outcomes and must not average into one."""
    fills = outcome.parse_fills([
        _fill(order_id=TP_ID, qty=800.0, at="2026-08-05T14:00:00Z"),
        _fill(order_id="manual1", qty=439.0, at="2026-08-07T13:39:08Z"),
        _fill(order_id="manual1", qty=400.0, at="2026-08-07T13:39:09Z"),
    ])
    groups = outcome.group_by_order(fills)
    assert list(groups) == [TP_ID, "manual1"]
    assert len(groups["manual1"]) == 2


# ── is the position actually off? ────────────────────────────────────────────────────────────

def test_a_full_exit_is_flat():
    assert outcome.is_flat(exit_qty=1639.0, entry_qty=1639.0) is True


def test_a_partial_exit_is_not_flat():
    """Half the position sold is not a closed trade. Writing one would take the candidate off
    the work list with shares still on the book, and the remaining exit would never be seen."""
    assert outcome.is_flat(exit_qty=800.0, entry_qty=1639.0) is False


def test_float_dust_still_counts_as_flat():
    """Perp sizes are fractional and the venue's own rounding can leave a remainder that no
    order will ever clear. An exact comparison would strand those trades open forever."""
    assert outcome.is_flat(exit_qty=0.3109999, entry_qty=0.311) is True
