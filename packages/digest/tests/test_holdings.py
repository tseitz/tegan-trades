from datetime import date
from types import SimpleNamespace

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
            "unpriced": (), "bootstrap": False, "arrived": (), "left": (),
            "stale": False, "age_days": None}
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


def test_the_portfolio_section_is_not_silently_swallowed_by_its_own_safety_net(monkeypatch):
    """`_holdings` catches everything so one bad account cannot cost the whole digest. That
    net also swallows a signature change in `review.readings_for` — which happened: the seam
    grew a third return value and the section became a warning nobody would have read.

    This asserts the happy path produces a delta and no warning, so the contract between the
    two packages is held by a failing test rather than by a log line.
    """
    from digest import cli

    book = SimpleNamespace(name="retirement", level_kinds=(),
                           is_stale=lambda *, on: False,
                           age_days=lambda *, on: 0)
    monkeypatch.setattr(cli.portfolios, "available", lambda: ("retirement",))
    monkeypatch.setattr(cli.portfolios, "load", lambda name: book)
    monkeypatch.setattr(
        cli, "readings_for",
        lambda books, *, as_of, registry: [(book, [_reading("WULF", ADD)], (None,))])

    warnings = []
    deltas, (verdicts, levels) = cli._holdings(
        {}, registry=object(), as_of=AS_OF, warn=warnings.append)
    assert warnings == []
    assert [c.ticker for c in deltas[0].changed] == ["WULF"]
    assert verdicts == {"retirement": {"WULF": ADD}}
    # No context, so no levels — but the key must exist, or tomorrow reads as a first run.
    assert levels == {"retirement": {}}


# ── levels reached overnight ───────────────────────────────────────────────

from core.nearby import RESISTANCE, SUPPORT, WEEKLY_ZONE, Level  # noqa: E402
from core.setups import WEEKLY  # noqa: E402
from review.levels import Spotlight  # noqa: E402


def _spot(ticker, *, side=SUPPORT, kind=WEEKLY_ZONE, top=99.0, bottom=95.0):
    return Spotlight(
        reading=_reading(ticker, HOLD),
        level=Level(kind=kind, timeframe=WEEKLY, side=side, top=top, bottom=bottom,
                    distance=0.0),
        others=0,
    )


def test_a_holding_newly_standing_on_a_level_is_an_arrival():
    d = holdings.delta("p", [], {}, on_levels=[_spot("COST")], remembered_levels={})
    assert [s.reading.holding.ticker for s in d.arrived] == ["COST"]


def test_standing_on_the_same_level_as_last_night_is_not_news():
    """The zone's edges drift a little every week as new bars close. Keying on the prices
    would fire an arrival every single night for a position that has not moved."""
    remembered = holdings.remember_levels([_spot("COST", top=99.0, bottom=95.0)])
    later = [_spot("COST", top=99.4, bottom=94.6)]      # same zone, redrawn
    d = holdings.delta("p", [], {}, on_levels=later, remembered_levels=remembered)
    assert d.arrived == ()


def test_moving_from_support_to_resistance_is_an_arrival():
    remembered = holdings.remember_levels([_spot("HOOD", side=SUPPORT)])
    d = holdings.delta("p", [], {}, on_levels=[_spot("HOOD", side=RESISTANCE)],
                       remembered_levels=remembered)
    assert [s.reading.holding.ticker for s in d.arrived] == ["HOOD"]


def test_leaving_a_level_is_reported_with_what_it_left():
    remembered = holdings.remember_levels([_spot("TSLA", side=SUPPORT)])
    d = holdings.delta("p", [], {}, on_levels=[], remembered_levels=remembered)
    assert d.left == (("TSLA", f"{WEEKLY_ZONE}:{SUPPORT}"),)


def test_the_first_run_reports_no_level_arrivals():
    """Every holding is standing on something on night one. Reporting all of them as
    arrivals would say a quiet Tuesday was the busiest night on record."""
    d = holdings.delta("p", [], {}, on_levels=[_spot("A"), _spot("B")], remembered_levels=None)
    assert d.arrived == ()
    assert d.left == ()


def test_remember_levels_keys_on_kind_and_side_only():
    stored = holdings.remember_levels([_spot("COST", side=RESISTANCE)])
    assert stored == {"COST": f"{WEEKLY_ZONE}:{RESISTANCE}"}


def test_level_moves_render_under_the_portfolio_heading():
    d = _delta(arrived=(_spot("COST", side=RESISTANCE),),
               left=(("TSLA", f"{WEEKLY_ZONE}:{SUPPORT}"),),
               standing={HOLD: 2}, positions=2)
    out = "\n".join(render._holdings_section([d]))
    assert "reached a level" in out
    assert "COST" in out and "resistance" in out
    assert "stepped off a level" in out
    assert "TSLA" in out


def test_a_long_arrival_list_is_capped_and_says_what_it_dropped():
    """The scan behind these is uncapped and ranked weekly-first, so the cap keeps the rows
    worth keeping. On 2026-08-30 one account printed seventeen — the biggest block in the
    digest and the least urgent thing in it."""
    spots = tuple(_spot(f"T{n}", side=RESISTANCE) for n in range(9))
    out = "\n".join(render._holdings_section([_delta(arrived=spots, positions=9)]))
    assert "T0" in out and "T4" in out
    assert "T5" not in out
    assert "and 4 more" in out


def test_an_arrival_list_inside_the_cap_says_nothing_about_dropping():
    spots = tuple(_spot(f"T{n}", side=RESISTANCE) for n in range(3))
    out = "\n".join(render._holdings_section([_delta(arrived=spots, positions=3)]))
    assert "more" not in out


def test_a_night_with_no_level_movement_prints_no_level_lines():
    out = "\n".join(render._holdings_section([_delta(standing={TRIM: 1}, positions=5)]))
    assert "reached a level" not in out


def test_level_arrivals_reach_the_subject_line():
    d = _delta(arrived=(_spot("COST"),))
    assert render.holdings_subject([d]) == ["1 at a level"]


# ── a stale file must not mail confident advice ────────────────────────────


def test_a_stale_portfolio_is_called_out_above_its_rows():
    """Worse in the email than in the terminal: you are not looking at the file, so nothing
    else tells you the positions it advises on may not exist any more."""
    d = _delta(stale=True, age_days=63, changed=(holdings.Change(
        ticker="WULF", before=HOLD, reading=_reading("WULF", ADD)),), positions=12)
    lines = render._holdings_section([d])
    assert any("STALE" in line for line in lines)
    warning = next(i for i, text in enumerate(lines) if "STALE" in text)
    row = next(i for i, text in enumerate(lines) if "WULF" in text)
    assert warning < row
    assert any("63" in line for line in lines)


def test_a_fresh_portfolio_says_nothing_about_its_age():
    d = _delta(standing={TRIM: 1}, positions=5)
    assert not any("STALE" in line for line in render._holdings_section([d]))


def test_a_stale_portfolio_reaches_the_subject_line():
    """It changes whether the rest of the line can be believed, which is the same reason
    `stale_as_of` is hoisted into the subject for the queue."""
    assert render.holdings_subject([_delta(stale=True, age_days=63)]) == ["retirement stale"]
