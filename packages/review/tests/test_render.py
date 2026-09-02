from datetime import date

from core.review import (
    ABOVE_RANGE,
    ADD,
    AT_RESISTANCE,
    AT_SUPPORT,
    BEARISH_ROSTER,
    BULLISH_ROSTER,
    HOLD,
    NO_READ,
    NO_VIEW,
    SILENT,
    TRIM,
    UNREADABLE,
    Holding,
    Location,
    Reading,
    RosterLean,
)
from review.render import ORDER, render

AS_OF = date(2025, 1, 10)


def _lean(lean=BEARISH_ROSTER, *, bulls=0, bears=3, people=3, age_days=8,
          voices=("Cowen", "DonAlt", "Pentoshi"), thin=False):
    return RosterLean(
        lean=lean, bulls=bulls, bears=bears, people=people,
        newest=None if age_days is None else date(2025, 1, 10),
        age_days=age_days, voices=voices, thin=thin,
    )


def _reading(ticker="BTC", *, verdict=TRIM, lean=None, where=AT_RESISTANCE,
             price=100.0, shares=2.0, cost=50.0, position=0.81, trend="uptrend"):
    return Reading(
        holding=Holding(ticker=ticker, shares=shares, cost=cost),
        roster=lean or _lean(),
        location=Location(where=where, basis="range", position=position),
        verdict=verdict,
        price=price,
        weekly_trend=trend,
    )


def test_header_names_the_account_and_the_date():
    out = render([_reading()], portfolio="retirement", as_of=AS_OF)
    assert "retirement" in out
    assert "2025-01-10" in out


def test_every_holding_appears_even_when_there_is_nothing_to_do():
    """A position missing from the table is indistinguishable from a position you no longer
    hold. Filtering to the actionable rows is the renderer's most tempting mistake."""
    out = render(
        [_reading("BTC"), _reading("VTI", verdict=NO_VIEW, lean=_lean(SILENT, bears=0,
                                                                     people=0, age_days=None,
                                                                     voices=()))],
        portfolio="retirement", as_of=AS_OF,
    )
    assert "BTC" in out
    assert "VTI" in out


def test_actionable_rows_sort_above_quiet_ones():
    out = render(
        [_reading("QUIET", verdict=HOLD), _reading("LOUD", verdict=TRIM)],
        portfolio="p", as_of=AS_OF,
    )
    assert out.index("LOUD") < out.index("QUIET")


def test_the_sort_order_covers_every_verdict():
    """A verdict missing from ORDER would sort by its absence rather than its urgency, and
    the row would drift to an arbitrary place in the table without anything failing."""
    assert set(ORDER) == {TRIM, ADD, "WATCH", HOLD, NO_VIEW, NO_READ}


def test_an_actionable_row_explains_itself_below_the_table():
    out = render([_reading("BTC", verdict=TRIM)], portfolio="p", as_of=AS_OF)
    assert "DonAlt" in out          # who is behind the call
    assert "8d" in out              # how old it is


def test_a_quiet_row_gets_no_explanation_paragraph():
    """The point of the section is that it is short enough to read. Explaining the HOLDs
    would bury the one line that asks for a decision."""
    out = render([_reading("VTI", verdict=HOLD)], portfolio="p", as_of=AS_OF)
    assert "VTI" in out
    assert out.count("VTI") == 1


def test_an_unpriced_holding_says_so_instead_of_printing_a_blank():
    reading = Reading(
        holding=Holding(ticker="WEIRD", shares=1.0, cost=None),
        roster=_lean(SILENT, bears=0, people=0, age_days=None, voices=()),
        location=Location(where=UNREADABLE, basis="none"),
        verdict=NO_VIEW, price=None, weekly_trend=None,
    )
    out = render([reading], portfolio="p", as_of=AS_OF)
    assert "WEIRD" in out
    assert "no price" in out


def test_a_missing_cost_basis_blanks_pnl_without_blanking_value():
    reading = _reading("BTC", cost=None)
    out = render([reading], portfolio="p", as_of=AS_OF)
    assert "200" in out            # 2 shares x 100 still has a market value


def test_a_row_shows_what_it_is_up_in_percent_as_well_as_in_money():
    """+50 on a 100 position and +50 on a 50,000 one are the same number about two different
    things, and the money column alone cannot tell them apart."""
    out = render([_reading("BTC", price=100.0, shares=2.0, cost=50.0)],
                 portfolio="p", as_of=AS_OF)
    assert "+100.00" in out and "+100.0%" in out


def test_the_account_total_carries_its_profit_and_loss():
    out = render([_reading("BTC", price=100.0, shares=2.0, cost=50.0)],
                 portfolio="p", as_of=AS_OF)
    tail = next(line for line in out.splitlines() if line.strip().startswith("P&L"))
    assert "+100.00" in tail and "+100.0%" in tail


def test_a_wallet_with_no_cost_basis_prints_no_profit_and_loss_total():
    """A chain knows what a coin is worth and never what it cost. A zero there would read as
    an account that has made nothing."""
    out = render([_reading("BTC", cost=None)], portfolio="p", as_of=AS_OF)
    assert not any(line.strip().startswith("P&L") for line in out.splitlines())


def test_the_profit_total_admits_the_rows_it_could_not_grade():
    out = render([_reading("BTC", price=100.0, shares=2.0, cost=50.0),
                  _reading("ETH", cost=None)], portfolio="p", as_of=AS_OF)
    tail = next(line for line in out.splitlines() if line.strip().startswith("P&L"))
    assert "1 with no cost basis" in tail


def test_an_empty_portfolio_says_so():
    out = render([], portfolio="p", as_of=AS_OF)
    assert "no positions" in out


def test_stale_roster_views_are_flagged_not_hidden():
    """An 800-day-old bullish call still votes — dropping it would turn a stale view into an
    absent one — but a reader has to be able to see that is what it is."""
    out = render(
        [_reading("BTC", verdict=ADD, lean=_lean(BULLISH_ROSTER, bulls=2, bears=0, people=2,
                                                 age_days=800, voices=("A", "B")),
                  where=AT_SUPPORT)],
        portfolio="p", as_of=AS_OF,
    )
    assert "800d" in out


def test_the_range_position_is_shown_when_that_is_what_decided_it():
    out = render([_reading("BTC", position=0.81)], portfolio="p", as_of=AS_OF)
    assert "81%" in out


def test_a_zone_reading_names_the_zone_rather_than_a_range_percentage():
    reading = Reading(
        holding=Holding(ticker="BTC", shares=1.0, cost=None),
        roster=_lean(),
        location=Location(where=AT_RESISTANCE, basis="zone", position=None),
        verdict=TRIM, price=100.0, weekly_trend="uptrend",
    )
    out = render([reading], portfolio="p", as_of=AS_OF)
    assert "weekly zone" in out
    assert "%" not in out.split("BTC")[-1].splitlines()[0]


def test_a_breakout_reads_as_a_break_not_as_a_position_in_the_range():
    """No percentage is available once price has left the range, and none must be invented —
    a number here would read as a location inside the leg that price is no longer in."""
    reading = Reading(
        holding=Holding(ticker="SPY", shares=1.0, cost=None),
        roster=_lean(),
        location=Location(where=ABOVE_RANGE, basis="outside", position=None),
        verdict=TRIM, price=100.0, weekly_trend="uptrend",
    )
    out = render([reading], portfolio="p", as_of=AS_OF)
    assert "broke above range" in out
    # The parenthesis, not any "%" on the row: the table has percentage columns of its own
    # now, and they are about the position's profit and its size, never about where price
    # sits in the leg. A range location is the only thing that renders as "label (NN%)".
    assert "range (" not in out


def test_a_softened_verdict_says_why_it_was_softened():
    """Otherwise the grid looks broken: roster bearish, price at resistance, verdict WATCH,
    and nothing on the line explains the gap."""
    out = render(
        [_reading("CRM", verdict="WATCH",
                  lean=_lean(bears=1, people=1, voices=("1000x Podcast",), thin=True))],
        portfolio="p", as_of=AS_OF,
    )
    assert "one voice" in out


def test_a_well_supported_verdict_carries_no_such_marker():
    out = render([_reading("BTC", verdict=TRIM)], portfolio="p", as_of=AS_OF)
    assert "one voice" not in out


def test_totals_line_sums_what_it_can_price():
    out = render(
        [_reading("A", price=100.0, shares=2.0, cost=50.0),
         _reading("B", price=10.0, shares=3.0, cost=5.0)],
        portfolio="p", as_of=AS_OF,
    )
    assert "230" in out            # 200 + 30


def _row(out: str, ticker: str) -> str:
    return next(line for line in out.splitlines() if line.split()[:1] == [ticker])


def test_a_row_shows_how_much_of_the_book_it_is():
    out = render(
        [_reading("A", price=100.0, shares=2.0, cost=50.0),
         _reading("B", price=10.0, shares=3.0, cost=5.0)],
        portfolio="p", as_of=AS_OF,
    )
    assert "87.0%" in _row(out, "A")     # 200 of 230
    assert "13.0%" in _row(out, "B")     # 30 of 230


def test_weight_is_measured_against_the_printed_total_not_against_cash():
    """The denominator has to be a number already on the page, or no row can be checked by
    hand. Cash is reported beside it and deliberately stays out of it."""
    out = render(
        [_reading("A", price=100.0, shares=2.0, cost=80.0)],
        portfolio="p", as_of=AS_OF, cash=200.0,
    )
    assert "100.0%" in _row(out, "A")    # not 50%, which counting the cash would give


def test_an_unpriced_row_has_no_weight_rather_than_a_zero():
    """Zero percent is a claim that the holding is worth nothing. It is not — nobody knows
    what it is worth, which is the same reason it is missing from the total."""
    reading = Reading(
        holding=Holding(ticker="WEIRD", shares=1.0, cost=None),
        roster=_lean(SILENT, bears=0, people=0, age_days=None, voices=()),
        location=Location(where=UNREADABLE, basis="none"),
        verdict=NO_VIEW, price=None, weekly_trend=None,
    )
    out = render([reading, _reading("A")], portfolio="p", as_of=AS_OF)
    assert "%" not in _row(out, "WEIRD")


# ── how old the file is ────────────────────────────────────────────────────


def test_the_header_says_how_old_the_positions_are():
    """A hand-kept file is a snapshot pretending to be a feed. Printing its age every run is
    what stops it quietly becoming fiction."""
    out = render([_reading()], portfolio="p", as_of=AS_OF, age_days=3)
    assert "written 3 days ago" in out


def test_a_file_written_today_says_today_rather_than_zero_days():
    out = render([_reading()], portfolio="p", as_of=AS_OF, age_days=0)
    assert "today" in out
    assert "0 days" not in out


def test_a_stale_file_is_called_out_above_the_table():
    """Under the rows it reads as a footnote about something else — the reader has already
    taken the verdicts as fact by then. Same rule the digest's caveats follow."""
    out = render([_reading()], portfolio="p", as_of=AS_OF, age_days=40, stale=True)
    lines = out.splitlines()
    assert any("STALE" in line for line in lines)
    warning = next(i for i, text in enumerate(lines) if "STALE" in text)
    first_row = next(i for i, text in enumerate(lines) if "BTC" in text)
    assert warning < first_row


def test_a_fresh_file_gets_no_warning():
    out = render([_reading()], portfolio="p", as_of=AS_OF, age_days=2)
    assert "STALE" not in out


def test_age_is_optional_so_a_caller_without_one_still_renders():
    assert "BTC" in render([_reading()], portfolio="p", as_of=AS_OF)


def test_cash_prints_in_the_header_when_the_broker_reported_it():
    out = render([_reading()], portfolio="retirement", as_of=AS_OF, cash=3379.57)
    assert "3,379.57 cash" in out


def test_a_hand_kept_file_says_nothing_about_cash_rather_than_zero():
    """None is not 0.0. A file nobody linked has no balance to report, and printing `0.00 cash`
    would state as fact that there is no room to add."""
    out = render([_reading()], portfolio="retirement", as_of=AS_OF, cash=None)
    assert "cash" not in out


def test_cash_lands_beside_the_adds_not_only_in_the_header():
    """The header is where you learn it; the ADD block is where you need it."""
    out = render([_reading(verdict=ADD, lean=_lean(BULLISH_ROSTER, bulls=3, bears=0))],
                 portfolio="retirement", as_of=AS_OF, cash=500.0)
    assert "500.00 cash to fund 1 ADD(s)" in out


def test_cash_is_not_advertised_when_nothing_asks_you_to_buy():
    out = render([_reading(verdict=HOLD)], portfolio="retirement", as_of=AS_OF, cash=500.0)
    assert "to fund" not in out


def test_a_mark_mismatch_is_the_first_thing_on_the_page():
    """Above the table on purpose. Every number on that row — verdict, level, P&L — is about
    whichever instrument the price came from, and nothing else in the report looks wrong."""
    out = render([_reading(ticker="LINK", price=24.30)], portfolio="retirement", as_of=AS_OF,
                 mismatched=(("LINK", 24.30, 4.85),))
    assert out.index("WRONG INSTRUMENT?") < out.index("TICKER")
    assert "LINK" in out
    assert "5.0x" in out          # a multiple, not a percentage, at this distance


def test_a_clean_account_prints_no_mismatch_banner():
    out = render([_reading()], portfolio="retirement", as_of=AS_OF, mismatched=())
    assert "WRONG INSTRUMENT" not in out


def test_two_accounts_in_one_file_show_which_cash_is_spendable_where():
    out = render([_reading(verdict=ADD, lean=_lean(BULLISH_ROSTER, bulls=3, bears=0))],
                 portfolio="retirement", as_of=AS_OF, cash=3879.57,
                 cash_by={"Roth IRA": 3379.57, "Traditional IRA": 500.0})
    assert "3,879.57 cash to fund 1 ADD(s)" in out
    assert "Roth IRA 3,379.57" in out
    assert "Traditional IRA 500.00" in out


def test_one_account_does_not_restate_its_own_total():
    out = render([_reading(verdict=ADD, lean=_lean(BULLISH_ROSTER, bulls=3, bears=0))],
                 portfolio="retirement", as_of=AS_OF, cash=500.0,
                 cash_by={"Roth IRA": 500.0})
    assert "spendable separately" not in out


# ── the weekly trend on the row ────────────────────────────────────────────


def test_the_row_says_which_way_the_weekly_is_going():
    """It decides a verdict now, so it has to be visible where the verdict is. Leaving it in
    the notes meant only the loud rows ever showed the thing that made them loud."""
    out = render([_reading("BTC", trend="downtrend")], portfolio="p", as_of=AS_OF)
    assert "TREND" in out
    assert "down" in _row(out, "BTC")


def test_a_holding_with_no_chart_has_no_trend():
    reading = Reading(
        holding=Holding(ticker="WEIRD", shares=1.0, cost=None),
        roster=_lean(SILENT, bears=0, people=0, age_days=None, voices=()),
        location=Location(where=UNREADABLE, basis="none"),
        verdict=NO_VIEW, price=None, weekly_trend=None,
    )
    out = render([reading], portfolio="p", as_of=AS_OF)
    assert "trend" not in _row(out, "WEIRD")


def test_a_trim_the_chart_argued_for_says_so():
    """A bullish roster beside a TRIM reads as a broken grid. The line has to carry the
    reason, exactly as a softened verdict does."""
    out = render(
        [_reading("BTC", verdict=TRIM, trend="downtrend",
                  lean=_lean(BULLISH_ROSTER, bulls=3, bears=0, age_days=200))],
        portfolio="p", as_of=AS_OF,
    )
    assert "weekly falling into resistance" in out


def test_an_ordinary_trim_carries_no_such_marker():
    out = render([_reading("BTC", verdict=TRIM, trend="uptrend")], portfolio="p", as_of=AS_OF)
    assert "weekly falling into resistance" not in out


# ── people who spoke without picking a side ────────────────────────────────


def _note_line(out: str, ticker: str) -> str:
    return next(line for line in out.splitlines()
                if line.strip().split()[1:2] == [ticker])


def test_an_undecided_roster_is_not_described_as_silent():
    """The table calls this "1 undecided" and the note called it "silent (nobody)". Someone
    did speak — they just did not pick a side, and that is a different fact from an empty
    room. Two words for one state, in one report, is the drift this module exists to stop."""
    out = render(
        [_reading("COST", verdict=TRIM,
                  lean=_lean(SILENT, bulls=0, bears=0, people=1, age_days=4, voices=()))],
        portfolio="p", as_of=AS_OF,
    )
    note = _note_line(out, "COST")
    assert "undecided" in note
    assert "silent" not in note
    assert "nobody" not in note


def test_an_empty_roster_still_reads_as_silence():
    """The other half of the distinction. Nobody has said anything, so there is no date to
    report and no one to name."""
    out = render(
        [_reading("COST", verdict=TRIM,
                  lean=_lean(SILENT, bulls=0, bears=0, people=0, age_days=None, voices=()))],
        portfolio="p", as_of=AS_OF,
    )
    note = _note_line(out, "COST")
    assert "silent" in note
    assert "nobody" in note
    assert "undecided" not in note
