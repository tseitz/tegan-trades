from datetime import date

from core.nearby import (
    DAILY_ZONE,
    GAP,
    RANGE_EDGE,
    RESISTANCE,
    SUPPORT,
    WEEKLY_ZONE,
    Level,
)
from core.review import HOLD, Holding, Location, Reading, RosterLean
from core.setups import DAILY, WEEKLY
from review.levels import SHOWN, shortlist

AS_OF = date(2025, 1, 10)


def _reading(ticker, *, price=100.0, shares=1.0):
    return Reading(
        holding=Holding(ticker=ticker, shares=shares, cost=None),
        roster=RosterLean(lean="silent", bulls=0, bears=0, people=0, newest=None,
                          age_days=None, voices=()),
        location=Location(where="mid", basis="range", position=0.5),
        verdict=HOLD, price=price, weekly_trend="uptrend",
    )


def _level(kind=WEEKLY_ZONE, *, timeframe=WEEKLY, side=SUPPORT, top=99.0, bottom=95.0,
           distance=0.0, invalidation=None):
    return Level(kind=kind, timeframe=timeframe, side=side, top=top, bottom=bottom,
                 distance=distance, invalidation=invalidation)


def test_a_holding_shows_its_most_significant_level_not_all_of_them():
    """234 levels across 77 holdings is a wall, not a shortlist. One row per holding per
    group is what makes the section readable at all."""
    pairs = [(_reading("DE"), [_level(DAILY_ZONE, timeframe=DAILY), _level(WEEKLY_ZONE)])]
    standing, _, _ = shortlist(pairs)
    assert len(standing) == 1
    assert standing[0].level.kind == WEEKLY_ZONE


def test_the_levels_not_shown_are_counted_on_the_row():
    """A row that silently represents four levels reads as one. The count is what keeps the
    cap from looking like the whole truth."""
    pairs = [(_reading("DE"), [_level(WEEKLY_ZONE), _level(GAP), _level(DAILY_ZONE,
                                                                       timeframe=DAILY)])]
    standing, _, _ = shortlist(pairs)
    assert standing[0].others == 2


def test_weekly_outranks_daily_across_holdings():
    pairs = [
        (_reading("NOISE"), [_level(DAILY_ZONE, timeframe=DAILY)]),
        (_reading("REAL"), [_level(WEEKLY_ZONE)]),
    ]
    standing, _, _ = shortlist(pairs)
    assert [s.reading.holding.ticker for s in standing] == ["REAL", "NOISE"]


def test_a_range_edge_outranks_a_gap_and_a_daily_block():
    pairs = [
        (_reading("A"), [_level(GAP)]),
        (_reading("B"), [_level(RANGE_EDGE, timeframe="", top=99.0, bottom=99.0)]),
        (_reading("C"), [_level(DAILY_ZONE, timeframe=DAILY)]),
    ]
    standing, _, _ = shortlist(pairs)
    assert [s.reading.holding.ticker for s in standing] == ["B", "A", "C"]


def test_standing_and_closing_in_are_separate_groups():
    """Price inside a level and price approaching one are different facts. One asks what you
    are standing on; the other asks what is about to happen."""
    pairs = [
        (_reading("IN"), [_level(WEEKLY_ZONE, distance=0.0)]),
        (_reading("NEAR"), [_level(WEEKLY_ZONE, distance=0.01, side=RESISTANCE)]),
    ]
    standing, closing, _ = shortlist(pairs)
    assert [s.reading.holding.ticker for s in standing] == ["IN"]
    assert [s.reading.holding.ticker for s in closing] == ["NEAR"]


def test_one_holding_can_appear_in_both_groups():
    """Standing on weekly support while 1% under weekly resistance is two things worth
    knowing, and collapsing them to one would drop whichever came second."""
    pairs = [(_reading("DE"), [_level(WEEKLY_ZONE, distance=0.0),
                               _level(WEEKLY_ZONE, distance=0.01, side=RESISTANCE)])]
    standing, closing, _ = shortlist(pairs)
    assert standing[0].reading.holding.ticker == "DE"
    assert closing[0].reading.holding.ticker == "DE"


def test_within_a_rank_the_nearest_comes_first():
    pairs = [
        (_reading("FAR"), [_level(WEEKLY_ZONE, distance=0.04, side=RESISTANCE)]),
        (_reading("CLOSE"), [_level(WEEKLY_ZONE, distance=0.001, side=RESISTANCE)]),
    ]
    _, closing, _ = shortlist(pairs)
    assert [s.reading.holding.ticker for s in closing] == ["CLOSE", "FAR"]


def test_standing_rows_of_equal_rank_sort_by_what_the_position_is_worth():
    """Distance is zero for all of them, so it cannot break the tie. The money can, and the
    biggest position is the one where being at a level matters most."""
    pairs = [
        (_reading("SMALL", price=10.0, shares=1.0), [_level(WEEKLY_ZONE, top=11.0, bottom=9.0)]),
        (_reading("BIG", price=10.0, shares=500.0), [_level(WEEKLY_ZONE, top=11.0, bottom=9.0)]),
    ]
    standing, _, _ = shortlist(pairs)
    assert [s.reading.holding.ticker for s in standing] == ["BIG", "SMALL"]


def test_the_cap_reports_what_it_dropped():
    """A silent truncation reads as "this is everything", which is the one thing a levels
    section must never imply."""
    pairs = [(_reading(f"T{i}"), [_level(WEEKLY_ZONE)]) for i in range(SHOWN + 5)]
    standing, _, suppressed = shortlist(pairs)
    assert len(standing) == SHOWN
    assert suppressed == 5


def test_a_holding_with_no_levels_is_absent_rather_than_an_empty_row():
    standing, closing, suppressed = shortlist([(_reading("QUIET"), [])])
    assert (standing, closing, suppressed) == ((), (), 0)


def test_an_unpriced_holding_cannot_be_at_a_level():
    reading = _reading("VTI")
    unpriced = Reading(holding=reading.holding, roster=reading.roster,
                       location=reading.location, verdict=reading.verdict,
                       price=None, weekly_trend=None)
    assert shortlist([(unpriced, [_level(WEEKLY_ZONE)])]) == ((), (), 0)


def test_the_limit_is_a_parameter_so_a_full_listing_is_possible():
    pairs = [(_reading(f"T{i}"), [_level(WEEKLY_ZONE)]) for i in range(30)]
    standing, _, suppressed = shortlist(pairs, limit=None)
    assert len(standing) == 30
    assert suppressed == 0


# ── rendering ──────────────────────────────────────────────────────────────

from review.render import render_levels  # noqa: E402


def _spot(ticker="DE", **kw):
    return __import__("review.levels", fromlist=["Spotlight"]).Spotlight(
        reading=_reading(ticker), level=_level(**kw), others=0)


def test_the_header_names_weekly_zones_as_weekly_not_just_zone():
    """`WEEKLY_ZONE` and `DAILY_ZONE` share a label because the timeframe is printed beside
    them in the rows. The header has no such column, so it has to spell them out or it reads
    as "counting zone, daily zone" — one of which is not a thing."""
    out = render_levels((), (), 0, kinds=(WEEKLY_ZONE, DAILY_ZONE))
    assert "weekly zone" in out
    assert "counting zone" not in out


def test_a_resistance_zone_dies_above_its_invalidation_not_below():
    """The origin swing of a bearish block is a swing HIGH — the zone dies when price gets
    above it. Printing `dies <` there points at the wrong side of the market, which on a
    level someone is deciding against is worse than printing nothing."""
    up = _spot(side=RESISTANCE, invalidation=139.75)
    assert "dies >139.75" in render_levels((up,), (), 0, kinds=(WEEKLY_ZONE,))

    down = _spot(side=SUPPORT, invalidation=11.05)
    assert "dies <11.05" in render_levels((down,), (), 0, kinds=(WEEKLY_ZONE,))


def test_group_labels_are_their_own_line_not_a_cell():
    """Put "standing on it" in the SIDE column and every side below it is padded to fourteen
    characters for a word that is not a side."""
    out = render_levels((_spot(side=SUPPORT),), (), 0, kinds=(WEEKLY_ZONE,))
    assert "  standing on it" in out.splitlines()


def test_an_empty_scan_says_so_rather_than_printing_an_empty_table():
    assert "nothing near a level" in render_levels((), (), 0, kinds=(WEEKLY_ZONE,))


def test_the_suppressed_count_points_at_the_flag_that_shows_them():
    out = render_levels((_spot(),), (), 83, kinds=(WEEKLY_ZONE,))
    assert "83 more" in out
    assert "--levels" in out
