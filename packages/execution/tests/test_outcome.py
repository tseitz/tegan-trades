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
    q = outcome.fill_quality(qty=100.0, median_volume=5_358_410.0, ceiling=0.01, paper=False,
                             stop_survival=0.9)
    assert q.credible is True


def test_an_unmeasured_stop_survival_leaves_credibility_unknown():
    """Same asymmetry as an unmeasured market: a row this code cannot fully vet must not be
    presented as vetted, even when nothing is positively wrong with it."""
    q = outcome.fill_quality(qty=100.0, median_volume=5_358_410.0, ceiling=0.01, paper=False)
    assert q.credible is None


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


# ── did the fill leave the trade that was approved? ─────────────────────────────────────────
#
# The third, independent reason a row is not evidence, and the one that survives going live.
# A limit entry fills at the OPEN on a gapped session, not at the limit — so the entry walks
# toward a stop that does not move with it and the planned risk distance collapses. VRT, 
# 2026-07-29: planned 266.52 against a 241.18 stop (9.5% away), filled at 243.33 (0.9% away),
# round-tripped flat in 49 seconds. See ``plan.build``'s note on the gapped open.

def test_a_clean_fill_keeps_most_of_the_planned_stop_distance():
    """INTL: planned 29.80, stop 29.19, filled 29.6212. 0.4312 of 0.61 survives."""
    assert round(outcome.stop_survival(planned_entry=29.8, fill=29.621233, stop=29.19), 3) == 0.707


def test_a_gapped_fill_destroys_the_planned_stop_distance():
    """VRT, the real one. 2.15 of 25.34 left — 91% of the risk distance gone before it started."""
    assert round(outcome.stop_survival(planned_entry=266.52, fill=243.33, stop=241.18), 3) == 0.085


def test_a_better_entry_is_always_a_tighter_stop():
    """The structural fact behind the whole check, and it holds in BOTH directions.

    A limit only ever fills on the favourable side — a buy at or below its price, a sell at or
    above — and the stop sits on the far side of the entry. So any improvement on the entry
    closes distance to the stop that the size was never adjusted for. Survival is therefore
    bounded above by 1.0 for any fill a limit order can produce; there is no benign case.
    """
    # Long, stop below: filling lower is a better entry and a tighter stop.
    assert outcome.stop_survival(planned_entry=100.0, fill=99.0, stop=95.0) == 0.8
    # Short, stop above: filling higher is a better entry and, again, a tighter stop.
    assert outcome.stop_survival(planned_entry=100.0, fill=101.0, stop=105.0) == 0.8
    # Filling exactly at the limit is the only way to keep the whole planned distance.
    assert outcome.stop_survival(planned_entry=100.0, fill=100.0, stop=95.0) == 1.0


def test_a_planned_entry_on_its_stop_leaves_survival_unmeasurable():
    """A zero denominator. Undefined rather than infinite, and it must not raise inside the
    nightly step."""
    assert outcome.stop_survival(planned_entry=100.0, fill=99.0, stop=100.0) is None


def test_an_unreadable_input_leaves_survival_unmeasurable():
    assert outcome.stop_survival(planned_entry=None, fill=99.0, stop=95.0) is None


def test_a_gap_collapsed_fill_is_not_evidence_even_on_real_money():
    """The gap this closes. VRT is liquid enough to pass participation and, on a live account,
    would have recorded as a credible +0.05R — a number describing a trade nobody approved."""
    q = outcome.fill_quality(qty=39.0, median_volume=2_000_000.0, ceiling=0.01, paper=False,
                             stop_survival=0.085)
    assert q.credible is False
    assert any("stop" in r for r in q.reasons)


def test_a_clean_fill_on_real_money_is_evidence():
    q = outcome.fill_quality(qty=39.0, median_volume=2_000_000.0, ceiling=0.01, paper=False,
                             stop_survival=0.707)
    assert q.credible is True
    assert q.reasons == ()


def test_every_disqualifying_reason_is_named():
    """The flag is skimmed and the reasons are what make it actionable — "not evidence" alone
    invites dismissing the flag rather than the number."""
    q = outcome.fill_quality(qty=1639.0, median_volume=19_262.0, ceiling=0.01, paper=True,
                             stop_survival=0.085)
    assert q.credible is False
    assert len(q.reasons) == 3


def test_a_definite_disqualifier_beats_an_unmeasured_one():
    """Paper is knowable without measuring anything, so an unmeasurable market must not soften
    it to "unknown" — that would read as "might be fine"."""
    q = outcome.fill_quality(qty=1.0, median_volume=None, ceiling=0.01, paper=True,
                             stop_survival=None)
    assert q.credible is False


# ── the venue saying whether a fill opened or closed ────────────────────────────────────────
#
# Hyperliquid states it (``dir``: "Close Short"); Alpaca does not, so there the side and the
# entry timestamp are all there is. Preferring the statement is the same read-don't-infer rule
# ``reason_for`` follows, and it is stronger: side + time cannot tell a close from a re-entry.

def test_a_venue_stated_close_is_preferred_over_the_side():
    opened = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    fills = (
        outcome.ExitFill(order_id="x", symbol="SOL", side="buy", qty=3.81, price=75.887,
                         at=datetime(2026, 8, 8, 13, 0, tzinfo=UTC), closing=True),
    )
    got = outcome.exit_fills(fills, symbol="SOL", entry_order_id="e",
                             exit_side="buy", after=opened)
    assert len(got) == 1


def test_a_venue_stated_OPEN_is_excluded_even_when_the_side_matches():
    """The case side-inference gets wrong: re-entering SOL short after closing means a later
    SELL of SOL that is an OPEN, not this position's exit. Alpaca cannot distinguish it; where
    the venue says so, this must."""
    opened = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    fills = (
        outcome.ExitFill(order_id="x", symbol="SOL", side="buy", qty=1.0, price=75.0,
                         at=datetime(2026, 8, 8, 13, 0, tzinfo=UTC), closing=False),
    )
    assert outcome.exit_fills(fills, symbol="SOL", entry_order_id="e",
                              exit_side="buy", after=opened) == ()


def test_without_a_statement_the_side_still_decides():
    """Alpaca's path must not regress — it has no ``dir`` and never will."""
    opened = datetime(2026, 7, 29, 13, 0, tzinfo=UTC)
    fills = outcome.parse_fills([_fill(at="2026-08-07T13:39:09Z")])
    assert len(outcome.exit_fills(fills, symbol="INTL", entry_order_id="e",
                                 exit_side="sell", after=opened)) == 1


# ── what it cost to hold ────────────────────────────────────────────────────────────────────
#
# The SOL short, closed 2026-08-08 after 11 days: the price move was -5.66528 (which is exactly
# what the venue's own ``closedPnl`` reported) and holding it cost 0.17263 in fees and 3.43939 in
# funding across 209 events. So the trade was -0.57R on price and -0.93R in fact. Recording the
# first as the outcome understates a perp loss by 0.36R, and it gets worse the longer the hold.

def test_gross_pnl_is_the_price_move_and_matches_the_venue():
    r = outcome.realized(direction="short", entry=74.4, exit_price=75.887, qty=3.81,
                         stop=77.02, risk_planned=9.9822)
    # The venue reported closedPnl -5.66528 against its own per-print average; recomputing from
    # the qty-weighted price lands within 0.0002, which is the price rounding and not a defect.
    assert round(r.pnl, 5) == -5.66547
    assert round(r.r_at_fill, 4) == -0.5676


def test_costs_are_subtracted_into_a_net_pnl():
    r = outcome.realized(direction="short", entry=74.4, exit_price=75.887, qty=3.81,
                         stop=77.02, risk_planned=9.9822,
                         fees=0.17263, funding=-3.43939)
    assert round(r.pnl_net, 4) == round(r.pnl - 0.17263 - 3.43939, 4)
    assert round(r.r_net_at_fill, 4) == -0.9294


def test_funding_received_improves_the_net():
    """Funding is signed — the short side is paid in a backwardated market, and treating it as a
    cost regardless would understate a winner as reliably as ignoring it overstates a loser."""
    r = outcome.realized(direction="short", entry=100.0, exit_price=100.0, qty=1.0,
                         stop=105.0, risk_planned=5.0, fees=0.0, funding=2.0)
    assert r.pnl == 0.0
    assert r.pnl_net == 2.0


def test_unmeasured_costs_leave_the_net_absent_rather_than_equal_to_gross():
    """The trap this exists for: defaulting costs to zero makes a perp row silently claim its
    holding was free, and a 209-event funding bill reads as 0.00."""
    r = outcome.realized(direction="short", entry=74.4, exit_price=75.887, qty=3.81,
                         stop=77.02, risk_planned=9.9822)
    assert r.fees is None
    assert r.funding is None
    assert r.pnl_net is None
    assert r.r_net_at_fill is None


def test_equities_measure_zero_funding_rather_than_none():
    """An equity genuinely has no funding, so 0.0 is a measurement and the net is knowable.
    ``None`` here would make every Alpaca row uncredible on a term that does not exist."""
    r = outcome.realized(direction="long", entry=29.621233, exit_price=31.21, qty=1639.0,
                         stop=29.19, risk_planned=999.79, fees=0.0, funding=0.0)
    assert r.pnl_net == r.pnl
    assert r.r_net_at_fill == r.r_at_fill


def test_a_row_whose_holding_cost_is_unknown_is_not_evidence():
    """Not ``False`` but ``None`` — unmeasured, the same as an unreadable market. A perp outcome
    missing 0.36R of funding is not a disproven row, it is an unfinished one."""
    q = outcome.fill_quality(qty=3.81, median_volume=1_000_000.0, ceiling=0.01, paper=False,
                             stop_survival=1.0, costs_known=False)
    assert q.credible is None


def test_a_row_with_known_costs_and_nothing_against_it_is_evidence():
    q = outcome.fill_quality(qty=3.81, median_volume=1_000_000.0, ceiling=0.01, paper=False,
                             stop_survival=1.0, costs_known=True)
    assert q.credible is True


def test_a_venue_that_charges_nothing_on_its_prints_reports_zero_fees():
    """Alpaca prints carry no fee field at all. ``None`` here would make ``costs_known`` false on
    every equity close and strip ``credible`` from the entire Alpaca history over a fee that does
    not exist — a silent regression the suite would not otherwise have caught."""
    fills = outcome.parse_fills([_fill(order_id="e")])
    assert outcome.fees_for(fills, ["e"]) == 0.0


def test_a_venue_that_does_charge_is_summed_across_entry_and_exit():
    fills = (
        outcome.ExitFill(order_id="e", symbol="SOL", side="sell", qty=3.81, price=74.4,
                         at=None, closing=False, fee=0.04252),
        outcome.ExitFill(order_id="x", symbol="SOL", side="buy", qty=3.81, price=75.888,
                         at=None, closing=True, fee=0.13011),
    )
    assert round(outcome.fees_for(fills, ["e", "x"]), 5) == 0.17263


def test_no_matching_prints_says_nothing_about_fees():
    """Distinct from a measured zero: there is no order here to have been charged for."""
    assert outcome.fees_for(outcome.parse_fills([_fill(order_id="e")]), ["other"]) is None
