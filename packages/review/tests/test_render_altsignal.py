from datetime import date

from core.review import Holding, Location, Reading, RosterLean
from review.altsignal import ChainLine, MacroRow
from review.render import render_altsignal


def _reading(ticker="SOL"):
    return Reading(
        holding=Holding(ticker=ticker, shares=10.0),
        roster=RosterLean(lean="bullish_roster", bulls=1, bears=0, people=1,
                          newest=date(2026, 8, 1), age_days=33, voices=("someone",)),
        location=Location(where="mid", basis="range", position=0.5),
        verdict="hold",
        price=200.0,
        weekly_trend=None,
    )


def test_nothing_configured_says_so():
    assert "nothing configured" in render_altsignal((), ())


def test_a_chain_line_names_the_holding_and_its_metrics():
    chains = (ChainLine(reading=_reading("SOL"), lines=("Solana chain TVL: $5.93B",)),)
    out = render_altsignal(chains, ())
    assert "SOL" in out
    assert "Solana chain TVL: $5.93B" in out


def test_a_macro_row_names_why_it_is_tracked_and_the_top_reading():
    macro = (MacroRow(why="Fed decision", top=(("KXFED-26DEC-T3.75", 0.72),), others=0),)
    out = render_altsignal((), macro)
    assert "Fed decision" in out
    assert "72%" in out


def test_suppressed_macro_readings_are_counted_not_hidden():
    macro = (MacroRow(why="BTC target", top=(("evt:strike-1", 0.5),), others=4),)
    out = render_altsignal((), macro)
    assert "4 more" in out
