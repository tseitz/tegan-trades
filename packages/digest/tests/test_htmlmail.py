"""Painting the digest for an inbox.

The contract worth testing is a negative one: this module may restyle the rendered text and may
never restate it. Everything else here is colour, which is cheap to get wrong and cheap to fix.
"""
from __future__ import annotations

import re

from digest import htmlmail

TRIGGER = ("AT THE TRIGGER\n"
           "  CWB      LONG   daily · entry 103.24 · stop 99.48\n"
           "           price reached the zone (was not at the zone) · R:R 2.25\n")


def _text(html: str) -> str:
    """The painted block with every tag stripped, which is what a reader ends up seeing."""
    inner = html.split("<pre", 1)[1].split(">", 1)[1].rsplit("</pre>", 1)[0]
    return re.sub(r"<[^>]+>", "", inner)


def test_every_character_of_the_digest_survives_the_paint():
    """The one rule. A fact that appears here and not in the terminal would mean two renderers,
    and the digest is plain text precisely so that cannot happen."""
    assert _text(htmlmail.wrap(TRIGGER)) == TRIGGER.rstrip("\n")


def test_the_document_declares_utf8():
    """A client that trusts the document over the MIME header renders every "·" as "Â·", and the
    digest is mostly middots. Caught by opening the block in a browser."""
    assert '<meta charset="utf-8">' in htmlmail.wrap(TRIGGER)


def test_the_block_is_monospace():
    """``render`` pads columns to fixed widths. In a proportional font that work is invisible,
    which is the whole reason this module exists."""
    assert "monospace" in htmlmail.wrap(TRIGGER)


def test_alignment_is_not_allowed_to_wrap():
    """A wrapped row drops its tail into the left margin, where it reads as a new row."""
    assert "white-space:pre" in htmlmail.wrap(TRIGGER)
    assert "pre-wrap" not in htmlmail.wrap(TRIGGER)


def test_a_section_heading_is_marked_out_from_its_rows():
    assert f'color:{htmlmail.HEADING};font-weight:700">AT THE TRIGGER' in htmlmail.wrap(TRIGGER)


def test_prose_at_column_zero_is_not_mistaken_for_a_heading():
    """``render`` writes "First run — ..." and "Quiet night — ..." unindented. Styled as headings
    they would out-shout the sections they sit beside."""
    assert "font-weight:700" not in htmlmail.wrap("Quiet night — 45 qualified, nothing entered.")


def test_a_heading_that_reports_trouble_is_coloured_as_trouble():
    for line in ("STALE — no queue snapshot was recorded today.",
                 "PROBLEMS — a section below may be missing or unexplained",
                 "RUN  exit 2 · 15 steps"):
        assert htmlmail.ALERT in htmlmail.wrap(line), line


def test_a_clean_run_line_is_not_coloured_as_trouble():
    assert htmlmail.ALERT not in htmlmail.wrap("RUN  clean · 15 steps")


def test_direction_and_action_words_carry_their_own_colour():
    for word, color in (("LONG", htmlmail.UP), ("SHORT", htmlmail.DOWN),
                        ("ADD", htmlmail.UP), ("TRIM", htmlmail.CAUTION)):
        assert f'color:{color};font-weight:600">{word}<' in htmlmail.wrap(f"  {word} HOOD")


def test_a_ticker_links_to_its_chart():
    painted = htmlmail.wrap("  CWB      LONG   daily · entry 103.24")
    assert 'href="https://www.tradingview.com/chart/?symbol=CWB"' in painted


def test_vocabulary_is_not_mistaken_for_a_symbol():
    """The first ALL-CAPS token on "ADD  HOOD — was HOLD" is ADD. Linked blindly, the digest
    offers you a chart for a stock that does not exist."""
    painted = htmlmail.wrap("  ADD   HOOD — was HOLD · 5 bull/2 bear 10d")
    assert "symbol=HOOD" in painted
    assert "symbol=ADD" not in painted and "symbol=HOLD" not in painted


def test_only_the_first_symbol_on_a_row_is_linked():
    """Every row leads with the thing it is about. Linking each match would make an arrival row
    a line of blue with no column to anchor on."""
    painted = htmlmail.wrap("  COIN  178.64  weekly support 160.00–185.41 GLD")
    assert painted.count("<a href") == 1


def test_a_heading_carries_no_link():
    """``PORTFOLIO`` is not a ticker, and a linked heading would read as the section itself
    being clickable."""
    assert "<a href" not in htmlmail.wrap("PORTFOLIO — retirement")


def test_a_long_crypto_symbol_still_links():
    """SOLVBTC and STKAAVE are real rows in the crypto account."""
    assert "symbol=SOLVBTC" in htmlmail.wrap("    SOLVBTC was on daily zone resistance")


def test_a_link_and_a_coloured_word_on_one_row_do_not_nest():
    """Both are spliced into the raw line in one pass. Run as sequential substitutions, the
    second pattern searches inside the markup the first one wrote."""
    painted = htmlmail.wrap("  CWB      LONG   daily")
    assert painted.index("</a>") < painted.index("LONG</span>")
    assert "<a" not in painted[painted.index("LONG") - 60:painted.index("LONG")]


def test_the_run_stamps_recede():
    assert htmlmail.MUTED in htmlmail.wrap("     2026-08-29T11:37:35Z → 2026-08-30T11:51:32Z")


def test_html_in_the_digest_cannot_become_markup():
    """Nothing upstream emits a tag today. A portfolio named ``<b>`` should still print as one
    rather than turning the rest of the mail bold."""
    painted = htmlmail.wrap("  a <b> & an ampersand")
    assert "&lt;b&gt;" in painted and "&amp;" in painted
