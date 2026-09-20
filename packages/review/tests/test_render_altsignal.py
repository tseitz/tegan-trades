from datetime import date

from core.review import Holding, Location, Reading, RosterLean
from review.altsignal import ChainLine, MacroRow
from review.render import macro_text, render_altsignal


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


def test_the_whole_altsignal_block_is_unchanged():
    """Byte-identical golden strings, captured at `eb62569` (HEAD before #92) — the net the
    four substring tests above are not. Every one of them stays green through a changed
    em-dash, a lost blank line, a different indent, or altered `(+N more)` spacing, so this is
    what makes #92's extraction of `macro_text` a byte-for-byte refactor rather than a rewrite.
    """
    assert render_altsignal((), ()) == (
        "ALT-SIGNAL — nothing configured yet (see cfg/altsignal.yaml)"
    )

    chains = (ChainLine(
        reading=_reading("SOL"),
        lines=("Solana chain TVL: $5.93B", "Solana stablecoins: $1.20B"),
    ),)
    assert render_altsignal(chains, ()) == (
        "ALT-SIGNAL\n"
        "\n"
        "  SOL\n"
        "    Solana chain TVL: $5.93B\n"
        "    Solana stablecoins: $1.20B"
    )

    macro = (
        MacroRow(why="Fed decision", top=(("KXFED-26DEC-T3.75", 0.72),), others=0),
        MacroRow(why="BTC target",
                 top=(("evt:strike-1", 0.5), ("evt:strike-2", 0.3)), others=4),
    )
    assert render_altsignal(chains, macro) == (
        "ALT-SIGNAL\n"
        "\n"
        "  SOL\n"
        "    Solana chain TVL: $5.93B\n"
        "    Solana stablecoins: $1.20B\n"
        "\n"
        "  MACRO\n"
        "    Fed decision: KXFED-26DEC-T3.75 72%\n"
        "    BTC target: strike-1 50%, strike-2 30% (+4 more)"
    )


def test_macro_text_names_why_and_the_top_readings_with_no_suffix_when_nothing_is_suppressed():
    row = MacroRow(why="Fed decision", top=(("KXFED-26DEC-T3.75", 0.72),), others=0)
    assert macro_text(row) == "Fed decision: KXFED-26DEC-T3.75 72%"


def test_macro_text_counts_suppressed_readings_in_a_trailing_suffix():
    row = MacroRow(why="BTC target",
                   top=(("evt:strike-1", 0.5), ("evt:strike-2", 0.3)), others=4)
    assert macro_text(row) == "BTC target: strike-1 50%, strike-2 30% (+4 more)"
