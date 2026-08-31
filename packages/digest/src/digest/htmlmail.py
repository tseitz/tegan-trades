"""Painting the finished digest for an inbox. Pure; takes the rendered text and nothing else.

**This is not a second renderer, and it must never become one.** It receives the same string the
terminal and the vault note get, and it may only change how those characters look. Every fact in
the mail therefore still comes from ``render``, so the three surfaces cannot disagree — which is
the guarantee that made the digest plain text in the first place.

**Why it exists.** ``render`` pads columns to fixed widths, and that alignment only holds in a
monospace font. The mail was a lone ``text/plain`` part, so Gmail drew it proportional and every
column collapsed. The work was being done and thrown away at the last step.

The plain-text part is still sent, first and unchanged. A client that prefers it, or cannot read
HTML at all, loses nothing.
"""

from __future__ import annotations

import re
from html import escape

# A stack, not one font. Menlo is on every Mac, Consolas on every Windows, and ``ui-monospace``
# picks the system default where the client honours it. Any of them keeps the columns.
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"

INK = "#1f2328"
HEADING = "#0b1117"
MUTED = "#8b949e"
ALERT = "#b3261e"
UP = "#0a7a53"
DOWN = "#b3261e"
CAUTION = "#a55a00"
LINK = "#0969da"

# Where a ticker goes when you click it. TradingView resolves a bare symbol across exchanges,
# so no venue prefix is needed and none would be right for a list mixing equities and coins.
CHART = "https://www.tradingview.com/chart/?symbol={}"

# A section heading is the only thing ``render`` puts at column zero in capitals. Prose it emits
# there — "First run — ...", "Quiet night — ..." — starts with an ordinary word, so this cannot
# swallow it.
_HEADING = re.compile(r"^[A-Z]{2,}\b")

# Headings that report trouble rather than content.
_ALERT_HEADINGS = ("STALE", "PROBLEMS", "NOTE", "RUN  exit")

# The two run stamps on the last line. Five spaces and an arrow is a shape nothing else emits.
_PROVENANCE = re.compile(r"^ {5}\S.*→")

# Applied to body lines only. Order is irrelevant: each match is replaced by a span whose own
# text is lowercase, so no later pattern can match inside one already written.
_WORDS = (
    (re.compile(r"\bLONG\b"), UP),
    (re.compile(r"\bSHORT\b"), DOWN),
    (re.compile(r"\bADD\b"), UP),
    (re.compile(r"\bTRIM\b"), CAUTION),
    (re.compile(r"\bWATCH\b"), MUTED),
    (re.compile(r"\bSTALE\b"), ALERT),
)


# A ticker as it appears in the body: capitals, possibly with digits, never with a space. The
# upper bound is 8 because SOLVBTC and STKAAVE are real rows in the crypto account.
_TICKER = re.compile(r"\b[A-Z][A-Z0-9]{0,7}\b")

# Capitalised words that are vocabulary rather than a symbol. Without this the first ALL-CAPS
# token on "ADD  HOOD — was HOLD" is a link to a chart for a stock called ADD.
_VOCABULARY = frozenset({
    "ADD", "TRIM", "WATCH", "HOLD", "LONG", "SHORT", "STALE", "NOTE", "RUN", "BOOK", "R",
    "TP", "SL", "OVER", "PROBLEMS", "NO", "VIEW", "AT", "THE",
})


def _ticker(line: str):
    """The first token on a line that looks like a symbol, or ``None``.

    First, not all of them. Every row in the digest leads with the thing it is about, and a
    line that linked each of "COIN 178.64 weekly support ... 4 bull/3 bear" would be a row of
    blue with no column to anchor on.
    """
    for match in _TICKER.finditer(line):
        if match.group() not in _VOCABULARY:
            return match
    return None


def _paint(line: str) -> str:
    """One line, styled. Escaping happens here and only here.

    Built by walking the RAW line and splicing markup between the spans that earned it, rather
    than by running substitutions over the result. Substituting in sequence means the second
    pattern searching inside the markup the first one wrote, and the day a ticker collides with
    a vocabulary word that produces a tag nested inside its own href.
    """
    if _HEADING.match(line):
        color = ALERT if line.startswith(_ALERT_HEADINGS) else HEADING
        return f'<span style="color:{color};font-weight:700">{escape(line)}</span>'
    if _PROVENANCE.match(line):
        return f'<span style="color:{MUTED}">{escape(line)}</span>'

    spans = []
    symbol = _ticker(line)
    if symbol:
        href = escape(CHART.format(symbol.group()), quote=True)
        spans.append((symbol.start(), symbol.end(),
                      f'<a href="{href}" style="color:{LINK};text-decoration:none">'
                      f"{escape(symbol.group())}</a>"))
    for pattern, color in _WORDS:
        for word in pattern.finditer(line):
            spans.append((word.start(), word.end(),
                          f'<span style="color:{color};font-weight:600">'
                          f"{escape(word.group())}</span>"))

    out, at = [], 0
    for start, end, markup in sorted(spans):
        if start < at:
            continue
        out.append(escape(line[at:start]))
        out.append(markup)
        at = end
    out.append(escape(line[at:]))
    return "".join(out)


def wrap(body: str) -> str:
    """The rendered digest as one monospace block.

    ``white-space: pre`` rather than ``pre-wrap``. Wrapping is what destroys the alignment this
    whole module exists to keep, and a wrapped line also drops a number into the left margin
    where it reads as a new row. Without a viewport tag a phone zooms the block out to fit,
    which keeps the columns true at the cost of small text — the right trade for a table.

    Colours are set on every element rather than inherited from a stylesheet. Mail clients strip
    ``<style>`` blocks, and an unstyled fallback here is a white-on-white section.

    The charset tag is not decoration. The MIME part already declares UTF-8, but a client that
    trusts the document over the header decodes every "·" as "Â·" — which is most of the
    punctuation in a digest. Seen in a browser the first time this block was rendered.
    """
    painted = "\n".join(_paint(line) for line in body.splitlines())
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:0;background:#ffffff">'
        f'<pre style="font-family:{MONO};font-size:13px;line-height:1.55;color:{INK};'
        'background:#ffffff;margin:0;padding:16px;white-space:pre">'
        f"{painted}</pre></body></html>"
    )
