"""Painting the digest for an inbox.

The contract worth testing is a negative one: this module may restyle the rendered text and may
never restate it. Everything else here is colour, which is cheap to get wrong and cheap to fix.
"""
from __future__ import annotations

import re

from digest import htmlmail

TRIGGER = ("VIEWS — last 3 days\n"
           "  TraderMayne · Sep 30 · a video\n"
           "    Bullish  BTC (high), ETH\n")


def _text(html: str) -> str:
    """The painted block with every tag stripped, which is what a reader ends up seeing."""
    inner = html.split("<body", 1)[1].split(">", 1)[1].rsplit("</body>", 1)[0]
    rows = re.findall(r"<div style=\"white-space:pre-wrap[^>]*>(.*?)</div>", inner)
    return "\n".join(re.sub(r"<[^>]+>", "", row) for row in rows).replace("&nbsp;", "")


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


def test_lines_wrap_to_the_screen():
    """A phone shrank a non-wrapping block to fit its widest line, which made all of it tiny."""
    html = htmlmail.wrap(TRIGGER)
    assert "pre-wrap" in html
    assert 'name="viewport"' in html


def _hang_em(html: str, needle: str) -> str:
    row = next(r for r in html.split("<div") if needle in r)
    return re.search(r"padding-left:([\d.]+)em", row).group(1)


def test_a_wrapped_line_hangs_under_the_text_after_its_label():
    """A continuation in the left margin reads as a new row."""
    html = htmlmail.wrap("    Bullish      BTC, ETH\n  BE       LONG   21 @ 221.68\n")
    assert _hang_em(html, "Bullish") == f"{17 * htmlmail._CHAR_EM:.1f}"
    assert _hang_em(html, "LONG") == f"{11 * htmlmail._CHAR_EM:.1f}"


def test_bullets_and_quoted_titles_carry_no_link():
    """A bullet opening "A wipeout…" or a title saying "IPO" is English, not a symbol."""
    html = htmlmail.wrap('      • A wipeout below\n  TraderMayne · Sep 30 · "The IPO top" · BTC\n')
    assert "symbol=A\"" not in html and "symbol=IPO" not in html
    assert "symbol=BTC" in html


def test_a_bullet_hangs_under_its_text_and_prose_two_in():
    html = htmlmail.wrap("                 • yields topping\n  BTC: TraderMayne flipped\n")
    assert _hang_em(html, "yields") == f"{19 * htmlmail._CHAR_EM:.1f}"
    assert _hang_em(html, "flipped") == f"{4 * htmlmail._CHAR_EM:.1f}"


def test_a_section_heading_is_marked_out_from_its_rows():
    assert f'color:{htmlmail.HEADING};font-weight:700">VIEWS — last 3 days' in htmlmail.wrap(TRIGGER)


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


def test_a_gate_verdict_word_does_not_steal_the_link():
    """`treasury.render` prints a gate verdict as "Safety OK". `_ticker` scans the whole line,
    not the first token, so an "OK" anywhere on a row would otherwise become a chart link."""
    painted = htmlmail.wrap("  aave-v3        4.20% · Safety OK")
    assert "<a href" not in painted


def test_a_levels_led_loud_row_still_links_the_ticker_not_the_verdict():
    """`SELL_ZONE`/`BUY_ZONE` cannot match `_TICKER` on their own — the underscore breaks the
    word boundary the pattern needs — but they are still in `_VOCABULARY` so a future rename
    to a bare word does not silently start stealing this link."""
    painted = htmlmail.wrap("  SELL_ZONE HOOD — was TRIM · 5 bull/2 bear 10d")
    assert "symbol=HOOD" in painted
    assert "symbol=SELL_ZONE" not in painted and "symbol=SELL" not in painted


def test_only_the_first_symbol_on_a_row_is_linked():
    """Every row leads with the thing it is about. Linking each match would make an arrival row
    a line of blue with no column to anchor on."""
    painted = htmlmail.wrap("  COIN  178.64  weekly support 160.00–185.41 GLD")
    assert painted.count("<a href") == 1


def test_a_heading_carries_no_link():
    """``PORTFOLIO`` is not a ticker, and a linked heading would read as the section itself
    being clickable."""
    assert "<a href" not in htmlmail.wrap("PORTFOLIO — retirement")


def test_the_net_worth_heading_carries_no_link():
    """``WORTH`` reads like a ticker to ``_TICKER``, same as ``TREASURY`` — the heading match
    has to win before the scan ever runs. #75 added the equivalent test for TREASURY for
    exactly this reason."""
    assert "<a href" not in htmlmail.wrap("NET WORTH — $134,515.93 across 4 mandates")


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
