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
    assert "%" not in out


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
