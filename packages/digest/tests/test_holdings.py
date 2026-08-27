from datetime import date

from core.review import (
    ADD,
    HOLD,
    NO_VIEW,
    TRIM,
    WATCH,
    Holding,
    Location,
    Reading,
    RosterLean,
)
from digest import holdings

AS_OF = date(2025, 1, 10)


def _reading(ticker, verdict, *, price=100.0, bulls=2, bears=0, thin=False):
    return Reading(
        holding=Holding(ticker=ticker, shares=1.0, cost=None),
        roster=RosterLean(lean="bullish", bulls=bulls, bears=bears, people=bulls + bears,
                          newest=date(2025, 1, 3), age_days=7, voices=("A", "B"), thin=thin),
        location=Location(where="at_support", basis="range", position=0.1),
        verdict=verdict, price=price, weekly_trend="uptrend",
    )


def test_a_new_add_is_reported():
    d = holdings.delta("retirement", [_reading("WULF", ADD)], {"WULF": HOLD})
    assert [(c.ticker, c.before, c.reading.verdict) for c in d.changed] == [("WULF", HOLD, ADD)]


def test_a_verdict_that_did_not_move_is_silent():
    """The whole package is a diff. A TRIM standing for the fifth night is not news, and
    reprinting it nightly is what trains the eye to skip the section."""
    d = holdings.delta("retirement", [_reading("CRM", TRIM)], {"CRM": TRIM})
    assert d.changed == ()
    assert d.standing[TRIM] == 1


def test_movement_between_quiet_verdicts_is_not_reported():
    """HOLD, WATCH and NO_VIEW all mean "do nothing". A position drifting between them asks
    nothing of you, and three such rows a night would bury the one that does."""
    d = holdings.delta(
        "retirement",
        [_reading("GOOG", NO_VIEW), _reading("CAT", WATCH), _reading("MU", HOLD)],
        {"GOOG": HOLD, "CAT": HOLD, "MU": WATCH},
    )
    assert d.changed == ()


def test_dropping_out_of_an_action_is_reported():
    """Just as loud as arriving. A TRIM that quietly stops being a TRIM is the difference
    between a decision you still owe and one the market already made for you."""
    d = holdings.delta("retirement", [_reading("CRM", WATCH)], {"CRM": TRIM})
    assert [(c.ticker, c.before, c.reading.verdict) for c in d.changed] == [("CRM", TRIM, WATCH)]


def test_a_side_flip_is_reported():
    d = holdings.delta("retirement", [_reading("ETN", TRIM)], {"ETN": ADD})
    assert d.changed[0].before == ADD


def test_a_position_you_just_added_to_the_file_has_no_before():
    d = holdings.delta("retirement", [_reading("NEW", ADD)], {"OLD": HOLD})
    assert d.changed[0].before is None
    assert d.bootstrap is False


def test_the_first_ever_run_reports_todays_actions_and_says_it_is_the_first():
    d = holdings.delta("retirement", [_reading("WULF", ADD), _reading("MU", HOLD)], {})
    assert d.bootstrap is True
    assert [c.ticker for c in d.changed] == ["WULF"]


def test_standing_counts_cover_the_quiet_verdicts_too():
    """The count is what keeps a persistent TRIM visible after its paragraph stops printing."""
    d = holdings.delta(
        "retirement",
        [_reading("A", TRIM), _reading("B", ADD), _reading("C", WATCH), _reading("D", HOLD)],
        {"A": TRIM, "B": ADD, "C": WATCH, "D": HOLD},
    )
    assert d.standing[TRIM] == 1
    assert d.standing[ADD] == 1
    assert d.standing[WATCH] == 1
    assert d.positions == 4


def test_unpriced_holdings_are_named_not_dropped():
    """A position with no price gets no verdict, and a section that just omitted it would
    report a portfolio you do not own."""
    d = holdings.delta("retirement", [_reading("VTI", NO_VIEW, price=None)], {})
    assert d.unpriced == ("VTI",)


def test_remember_stores_every_verdict_not_only_the_loud_ones():
    """Tomorrow's diff needs yesterday's answer for every ticker. Storing only the actions
    would make every quiet-to-loud move look like a brand-new position."""
    stored = holdings.remember([_reading("A", TRIM), _reading("B", HOLD)])
    assert stored == {"A": TRIM, "B": HOLD}


def test_an_empty_portfolio_produces_an_empty_delta_not_a_crash():
    d = holdings.delta("retirement", [], {})
    assert d.changed == ()
    assert d.positions == 0
    assert d.is_quiet is True


def test_a_delta_with_changes_is_not_quiet():
    d = holdings.delta("retirement", [_reading("WULF", ADD)], {"WULF": HOLD})
    assert d.is_quiet is False


# ── rendering ──────────────────────────────────────────────────────────────

from digest import render  # noqa: E402


def _delta(**kw):
    base = {"portfolio": "retirement", "changed": (), "standing": {}, "positions": 0,
            "unpriced": (), "bootstrap": False}
    base.update(kw)
    return holdings.HoldingsDelta(**base)


def test_section_is_absent_when_nothing_moved_and_nothing_stands():
    assert render._holdings_section([_delta(standing={HOLD: 5}, positions=5)]) == []


def test_a_standing_action_keeps_a_one_line_presence_after_its_paragraph_stops():
    """The diff goes quiet on night two; the decision you still owe does not. A count is the
    honest middle — visible, but not a repeated paragraph that teaches you to skip the block."""
    out = "\n".join(render._holdings_section([_delta(standing={TRIM: 1, HOLD: 76},
                                                     positions=77)]))
    assert "1 TRIM" in out
    assert "77 position" in out


def test_an_arrival_carries_what_moved_and_why():
    change = holdings.Change(ticker="WULF", before=HOLD, reading=_reading("WULF", ADD))
    out = "\n".join(render._holdings_section([_delta(changed=(change,),
                                                     standing={ADD: 1}, positions=1)]))
    assert "WULF" in out
    assert "was HOLD" in out
    assert "at support" in out          # the location, worded by review.render


def test_a_departure_from_an_action_says_so_without_a_full_line():
    """It asks nothing of you now, so it gets a mention rather than levels and a roster split."""
    change = holdings.Change(ticker="CRM", before=TRIM, reading=_reading("CRM", WATCH))
    out = "\n".join(render._holdings_section([_delta(changed=(change,), positions=1)]))
    assert "CRM" in out
    assert "no longer" in out


def test_a_first_run_says_it_has_never_looked_before():
    """Eight arrivals on night one is not eight things that happened today."""
    change = holdings.Change(ticker="WULF", before=None, reading=_reading("WULF", ADD))
    out = "\n".join(render._holdings_section([_delta(changed=(change,), bootstrap=True,
                                                     standing={ADD: 1}, positions=1)]))
    assert "first" in out.lower()


def test_unpriced_holdings_reach_the_reader():
    out = "\n".join(render._holdings_section([_delta(unpriced=("VTI", "QQQ"), positions=2)]))
    assert "VTI" in out and "QQQ" in out


def test_two_portfolios_each_get_their_own_heading():
    out = "\n".join(render._holdings_section([
        _delta(portfolio="retirement", standing={TRIM: 1}, positions=77),
        _delta(portfolio="brokerage", standing={ADD: 1}, positions=4),
    ]))
    assert "retirement" in out and "brokerage" in out


def test_the_subject_counts_holdings_that_moved():
    change = holdings.Change(ticker="WULF", before=HOLD, reading=_reading("WULF", ADD))
    assert render.holdings_subject([_delta(changed=(change,))]) == ["1 holding moved"]
    assert render.holdings_subject([_delta()]) == []
