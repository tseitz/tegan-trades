"""The digest as an HTML email. Pure: an ``EmailDoc`` in, a page out.

A second painter beside ``render``, reading the same computed objects rather than its text.
Wherever the two must agree on *what* shows — VIEWS grading, portfolio ordering and caps, the
queue counts, run health — this calls the helper ``views`` or ``render`` already uses, so only
the look is written twice. ``tests/test_email_html.py`` holds the parity check.

Mail-client rules the markup obeys: every style inline (Gmail drops ``<style>`` in many
contexts), table layout, one fluid column capped at ``WIDTH`` so a phone needs no media query,
and an explicit background on every container so a dark-mode client has nothing to guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from urllib.parse import quote, urlparse

from core import networth
from treasury import render as treasury_render

from digest import diff, holdings, render, views
from digest.fmt import instant, num

WIDTH = 680

# Gmail clips a message body past ~102 KB and hides everything below the cut, footer included.
# Over the soft budget the page is redrawn compact; over the hard one the caller warns.
SOFT_BUDGET = 90_000
HARD_BUDGET = 100_000

SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif")
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

PAGE_BG = "#f3f4f6"
CARD_BG = "#ffffff"
BORDER = "#e5e7eb"
INK = "#1f2937"
MUTED = "#6b7280"
FAINT = "#9ca3af"
HEADER_BG = "#0f172a"
HEADER_INK = "#f8fafc"
HEADER_MUTED = "#94a3b8"
LINK = "#2563eb"

# (text, background) per tone.
TONES = {
    "bull": ("#047857", "#d1fae5"),
    "bear": ("#b91c1c", "#fee2e2"),
    "neutral": ("#4b5563", "#e5e7eb"),
    "caution": ("#b45309", "#fef3c7"),
    "info": ("#1d4ed8", "#dbeafe"),
}

BUCKET_TONE = {"Bullish": "bull", "Bearish": "bear", "Unsure": "neutral", "Punts": "caution"}
VERDICT_TONE = {"ADD": "bull", "BUY_ZONE": "bull", "TRIM": "bear", "SELL_ZONE": "bear",
                "WATCH": "neutral", "HOLD": "neutral"}

CHART = "https://www.tradingview.com/chart/?symbol={}"

_NARRATED = re.compile(r"^\s*([A-Za-z0-9.\-]{1,12}):\s+(.*)$")


@dataclass(frozen=True, slots=True)
class EmailDoc:
    subject: str
    delta: diff.QueueDelta
    views: tuple[views.PersonView, ...] = ()
    big_picture: dict[str, str] | None = None
    views_note: str | None = None
    views_heading: str = render.VIEWS_HEADING
    roster: str | None = None
    roster_withheld: str | None = None
    book: object = None
    holdings_deltas: tuple = ()
    net_worth: networth.NetWorth | None = None
    net_worth_changes: dict = field(default_factory=dict)
    net_worth_recorded: bool = True
    run: dict | None = None
    problems: tuple[str, ...] = ()
    stale_as_of: str | None = None
    xai_month: float | None = None
    xai_cap: float | None = None
    xai_changed: bool = True


def page(doc: EmailDoc) -> str:
    html = _page(doc, compact=False)
    if len(html) > SOFT_BUDGET:
        html = _page(doc, compact=True)
    return html


def fallback(body: str) -> str:
    """The plain-text digest in a monospace block — what goes out if ``page`` breaks."""
    return _shell(f'<pre style="margin:0;padding:16px;font-family:{MONO};font-size:13px;'
                  f'line-height:1.5;color:{INK};background:{CARD_BG};white-space:pre-wrap;'
                  f'overflow-wrap:anywhere">{escape(body)}</pre>', title="Digest")


# ── page ──────────────────────────────────────────────────────────────────────

def _page(doc: EmailDoc, *, compact: bool) -> str:
    blocks = [
        _header(doc),
        _alerts(doc),
        _roster_section(doc),
        _views_section(doc, compact=compact),
        _queue_section(doc.delta),
        _book_section(doc.book),
        _portfolio_section(doc.holdings_deltas),
        _networth_section(doc),
        _footer(doc),
    ]
    return _shell("".join(b for b in blocks if b), title=doc.subject)


def _shell(inner: str, *, title: str) -> str:
    # The charset meta is load-bearing: a client that trusts the document over the MIME header
    # otherwise decodes every "·" as "Â·".
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light"><meta name="supported-color-schemes" '
        f'content="light"><title>{escape(title)}</title></head>'
        f'<body style="margin:0;padding:0;background:{PAGE_BG};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{PAGE_BG};"><tr><td align="center" style="padding:16px 10px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:{WIDTH}px;width:100%;table-layout:fixed;font-family:{SANS};color:{INK};'
        f'font-size:15px;line-height:1.5;">{inner}</table></td></tr></table></body></html>'
    )


def _row(inner: str, *, pad: str = "0 0 14px 0") -> str:
    return f'<tr><td style="padding:{pad};">{inner}</td></tr>'


def _card(title: str | None, inner: str, *, aside: str = "", accent: str | None = None) -> str:
    edge = f"border-left:4px solid {accent};" if accent else ""
    head = ""
    if title:
        head = (f'<div style="font-size:12px;font-weight:700;letter-spacing:.08em;'
                f'text-transform:uppercase;color:{MUTED};margin:0 0 10px 0;">{escape(title)}'
                f'{aside}</div>')
    return _row(f'<div style="background:{CARD_BG};border:1px solid {BORDER};{edge}'
                f'border-radius:12px;padding:16px 18px;">{head}{inner}</div>')


def _badge(text: str, tone: str, *, strong: bool = False, note: str = "") -> str:
    ink, bg = TONES[tone]
    weight = "700" if strong else "600"
    tail = (f' <span style="font-weight:400;opacity:.8;">{escape(note)}</span>' if note else "")
    return (f'<span style="display:inline-block;margin:0 4px 6px 0;padding:2px 9px;'
            f'border-radius:999px;background:{bg};color:{ink};font-size:13px;'
            f'font-weight:{weight};white-space:nowrap;">{escape(text)}{tail}</span>')


def _ticker(symbol: str, *, tone: str | None = None) -> str:
    href = escape(CHART.format(quote(symbol.strip(), safe="")), quote=True)
    color = TONES[tone][0] if tone else LINK
    return (f'<a href="{href}" style="color:{color};font-weight:700;text-decoration:none;">'
            f'{escape(symbol.strip())}</a>')


def _safe_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        scheme = urlparse(url).scheme
    except ValueError:
        return None
    return url if scheme in ("http", "https") else None


def _muted(text: str, *, size: int = 13) -> str:
    return f'<span style="color:{MUTED};font-size:{size}px;">{escape(text)}</span>'


# ── header + alerts ───────────────────────────────────────────────────────────

def _when(delta: diff.QueueDelta) -> str:
    stamp = instant(delta.current_run) if delta.current_run else None
    return stamp.strftime("%A, %B %-d, %Y") if isinstance(stamp, datetime) else ""


def _chip(label: str, value: str, *, tone: str | None = None) -> str:
    color = TONES[tone][0] if tone else HEADER_INK
    if tone:
        color = {"bull": "#6ee7b7", "bear": "#fca5a5", "caution": "#fcd34d"}.get(tone, color)
    return (f'<div style="display:inline-block;margin:0 18px 6px 0;vertical-align:top;">'
            f'<div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;'
            f'color:{HEADER_MUTED};">{escape(label)}</div>'
            f'<div style="font-size:16px;font-weight:700;color:{color};white-space:nowrap;">'
            f'{escape(value)}</div></div>')


def _header(doc: EmailDoc) -> str:
    chips = []
    counts = render._queue_counts(doc.delta)
    chips.append(_chip("Queue", " · ".join(counts) if counts else "quiet",
                       tone="caution" if doc.delta.arrived else None))
    if doc.net_worth is not None:
        change = doc.net_worth_changes.get(1)
        tone = None
        if change is not None:
            tone = "bull" if change.amount >= 0 else "bear"
        value = treasury_render.money(doc.net_worth.total)
        if change is not None:
            value += f"  {render._change(change)}"
        chips.append(_chip("Net worth", value, tone=tone))
    clean = render._is_clean(doc.run)
    chips.append(_chip("Run", "clean" if clean else "needs a look",
                       tone="bull" if clean else "bear"))
    return _row(
        f'<div style="background:{HEADER_BG};border-radius:12px;padding:20px 20px 16px 20px;">'
        f'<div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;'
        f'color:{HEADER_MUTED};">Tegan Trades</div>'
        f'<div style="font-size:24px;font-weight:800;color:{HEADER_INK};margin:2px 0 2px 0;">'
        f'Roster Digest</div>'
        f'<div style="font-size:13px;color:{HEADER_MUTED};margin:0 0 14px 0;">'
        f'{escape(_when(doc.delta))}</div>'
        f'<div>{"".join(chips)}</div></div>')


def _alert_lines(doc: EmailDoc) -> list[list[str]]:
    groups = []
    if doc.stale_as_of:
        groups.append([f"Stale queue — no snapshot was recorded today; the queue describes "
                       f"{doc.stale_as_of}."])
    if not doc.delta.bootstrap:
        groups.append([line for line in render._caveats(doc.delta) if line.strip()])
    groups.append([line for line in render._run_section(
        doc.run, xai_month=doc.xai_month, xai_cap=doc.xai_cap, xai_changed=doc.xai_changed)
        if line.strip()])
    groups.append([line for line in render._problems_section(doc.problems) if line.strip()])
    return [g for g in groups if g]


def _alerts(doc: EmailDoc) -> str:
    groups = _alert_lines(doc)
    if not groups:
        return ""
    ink, bg = TONES["bear"]
    items = []
    for group in groups:
        head, *rest = group
        body = "".join(f'<div style="font-family:{MONO};font-size:12px;color:{INK};'
                       f'overflow-wrap:anywhere;">{escape(line.strip())}</div>' for line in rest)
        items.append(f'<div style="margin:0 0 8px 0;"><div style="font-weight:700;color:{ink};">'
                     f'{escape(head.strip())}</div>{body}</div>')
    return _row(f'<div style="background:{bg};border:1px solid #fecaca;border-radius:12px;'
                f'padding:14px 18px 6px 18px;">{"".join(items)}</div>')


# ── roster ────────────────────────────────────────────────────────────────────

def _roster_section(doc: EmailDoc) -> str:
    if doc.roster_withheld:
        return _card("Roster", f'<div style="color:{MUTED};">'
                               f'{escape(doc.roster_withheld.strip())}</div>')
    if not doc.roster:
        return ""
    rows = []
    for line in doc.roster.splitlines():
        if not line.strip():
            continue
        match = _NARRATED.match(line)
        if match:
            rows.append(
                f'<tr><td style="padding:6px 12px 6px 0;vertical-align:top;white-space:nowrap;'
                f'width:1%;">{_ticker(match.group(1))}</td>'
                f'<td style="padding:6px 0;vertical-align:top;border-bottom:1px solid #f3f4f6;">'
                f'{escape(match.group(2))}</td></tr>')
        else:
            rows.append(f'<tr><td colspan="2" style="padding:6px 0;">{escape(line.strip())}'
                        f'</td></tr>')
    return _card("Roster moved", '<table role="presentation" width="100%" cellpadding="0" '
                 f'cellspacing="0" border="0" style="font-size:14px;">{"".join(rows)}</table>',
                 accent="#6366f1")


# ── views ─────────────────────────────────────────────────────────────────────

def _views_section(doc: EmailDoc, *, compact: bool) -> str:
    _, _, scope = doc.views_heading.partition(" — ")
    aside = (f'<span style="font-weight:400;text-transform:none;letter-spacing:0;'
             f'color:{FAINT};"> · {escape(scope)}</span>') if scope else ""
    if not doc.views:
        if not doc.views_note:
            return ""
        return _card("Views", f'<div style="color:{MUTED};">{escape(doc.views_note.strip())}'
                              f'</div>', aside=aside)
    big = doc.big_picture or {}
    title = _row(f'<div style="font-size:12px;font-weight:700;letter-spacing:.08em;'
                 f'text-transform:uppercase;color:{MUTED};padding:4px 4px 0 4px;">'
                 f'Views{aside}</div>', pad="4px 0 10px 0")
    cards = [(_collapsed_card if view.collapsed else _person_card)(
        view, big.get(view.person), compact=compact) for view in doc.views]
    return title + "".join(cards)


def _video_title(video: views.Video, *, size: int = 14) -> str:
    text = escape(views.label(video))
    url = _safe_url(video.url)
    if url:
        text = (f'<a href="{escape(url, quote=True)}" style="color:{INK};'
                f'text-decoration:none;">{text}</a>')
    return (f'<div style="font-size:{size}px;font-weight:600;margin:0 0 8px 0;">'
            f'{_muted(views.day(video.published_at), size=size - 1)} · {text}</div>')


def _fields(video: views.Video, big_picture: str | None) -> str:
    out = []
    if big_picture:
        out.append(f'<div style="font-size:15px;margin:0 0 10px 0;">{escape(big_picture)}</div>')
    year = views._year(video.published_at)
    for name, entries in views.buckets(video).items():
        tone = BUCKET_TONE[name]
        badges = "".join(
            _badge(e.asset, tone, strong="high" in (notes := views.entry_notes(e, year)),
                   note=" · ".join(n for n in notes if n != "high"))
            for e in entries)
        out.append(
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:0 0 2px 0;"><tr><td style="width:70px;vertical-align:top;'
            f'padding:3px 8px 0 0;font-size:12px;font-weight:700;color:{TONES[tone][0]};">'
            f'{escape(name)}</td><td style="vertical-align:top;">{badges}</td></tr></table>')
    if held := views.held(video):
        out.append(f'<div style="font-size:13px;color:{MUTED};margin:2px 0 6px 0;">'
                   f'<b style="color:{FAINT};font-weight:700;">Held</b> '
                   f'{escape(" · ".join(held))}</div>')
    if watching := views.watching(video.stances):
        items = "".join(f'<li style="margin:0 0 4px 0;">{escape(t)}</li>' for t in watching)
        out.append(f'<div style="font-size:12px;font-weight:700;color:{MUTED};'
                   f'margin:8px 0 4px 0;">Watching</div>'
                   f'<ul style="margin:0;padding:0 0 0 18px;font-size:14px;color:{INK};">'
                   f'{items}</ul>')
    return "".join(out)


def _person_card(view: views.PersonView, big_picture: str | None, *, compact: bool) -> str:
    shown = [v for v in view.videos if v.distilled]
    undistilled = len(view.videos) - len(shown)
    parts = [f'<div style="font-size:18px;font-weight:800;margin:0 0 4px 0;">'
             f'{escape(view.person)}</div>']
    if shown and shown[0].stances:
        parts.append(_video_title(shown[0]))
        parts.append(_fields(shown[0], big_picture))
        rest = shown[1:]
    else:
        if big_picture:
            parts.append(f'<div style="margin:0 0 10px 0;">{escape(big_picture)}</div>')
        rest = shown
    for video in rest:
        parts.append('<div style="border-top:1px solid #f3f4f6;margin:12px 0 0 0;'
                     'padding:12px 0 0 0;">')
        if not video.stances:
            parts.append(_video_title(video, size=13) + _muted("no market views"))
        elif compact:
            parts.append(_video_title(video, size=13) + _compact_line(video))
        else:
            parts.append(_video_title(video, size=13) + _fields(video, None))
        parts.append("</div>")
    if undistilled:
        parts.append(f'<div style="margin:10px 0 0 0;">'
                     f'{_muted(f"{undistilled} video{"" if undistilled == 1 else "s"} not yet distilled")}'
                     f'</div>')
    return _card(None, "".join(parts))


def _compact_line(video: views.Video) -> str:
    out = []
    for part in views.collapsed_parts(video):
        name, _, rest = part.partition(" ")
        tone = BUCKET_TONE.get(name, "neutral")
        out.append(f'<span style="font-size:12px;font-weight:700;color:{TONES[tone][0]};">'
                   f'{escape(name)}</span> <span style="font-size:13px;">{escape(rest)}</span>')
    return '<div style="margin:0 0 4px 0;">' + " &nbsp; ".join(out) + "</div>"


def _collapsed_card(view: views.PersonView, big_picture: str | None, *, compact: bool) -> str:
    n = len(view.videos)
    parts = [f'<div style="font-size:18px;font-weight:800;margin:0 0 4px 0;">'
             f'{escape(view.person)} {_muted(f"· {n} video{"" if n == 1 else "s"}", size=14)}'
             f'</div>']
    if big_picture:
        parts.append(f'<div style="margin:0 0 10px 0;">{escape(big_picture)}</div>')
    quiet = 0
    for video in view.videos:
        if not views.collapsed_parts(video):
            quiet += 1
            continue
        parts.append(f'<div style="border-top:1px solid #f3f4f6;padding:8px 0 4px 0;">'
                     f'{_video_title(video, size=13)}{_compact_line(video)}</div>')
    if quiet:
        parts.append(_muted(f"+{quiet} with nothing new or not yet distilled"))
    return _card(None, "".join(parts))


# ── queue, book ───────────────────────────────────────────────────────────────

def _code(text: str) -> str:
    return (f'<code style="font-family:{MONO};font-size:12px;background:#f3f4f6;'
            f'border:1px solid {BORDER};border-radius:6px;padding:2px 6px;'
            f'white-space:nowrap;">{escape(text)}</code>')


def _queue_section(delta: diff.QueueDelta) -> str:
    if delta.bootstrap:
        badges = _badge(f"first run · {delta.qualified} qualified", "info")
    else:
        counts = render._queue_counts(delta)
        if counts:
            badges = "".join(_badge(c, "caution" if "TRIGGER" in c else "info", strong=True)
                             for c in counts)
        else:
            badges = _badge(f"quiet · {delta.qualified} qualified", "neutral")
    return _card("Setups queue", f'<div>{badges}</div><div style="margin:4px 0 0 0;">'
                                 f'{_muted("Review with ")}{_code("uv run setups")}</div>')


def _book_section(book) -> str:
    events, notice = render._book_parts(book)
    if not events and not notice:
        return ""
    items = "".join(f'<li style="margin:0 0 4px 0;">{escape(str(e))}</li>' for e in events)
    tail = f'<div style="margin:6px 0 0 0;">{_muted(notice)}</div>' if notice else ""
    return _card("Book", f'<ul style="margin:0;padding:0 0 0 18px;font-size:14px;">{items}</ul>'
                         f'{tail}')


# ── portfolio ─────────────────────────────────────────────────────────────────

def _portfolio_section(deltas) -> str:
    return "".join(_portfolio_card(rows) for d in deltas
                   if (rows := render.portfolio_rows(d)) is not None)


def _cell(inner: str, *, mono: bool = False, align: str = "left", muted: bool = False) -> str:
    font = f"font-family:{MONO};font-size:12px;" if mono else "font-size:13px;"
    color = f"color:{MUTED};" if muted else ""
    return (f'<td style="padding:6px 10px 6px 0;border-bottom:1px solid #f3f4f6;'
            f'vertical-align:top;text-align:{align};{font}{color}">{inner}</td>')


def _table(rows: list[str]) -> str:
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;margin:0 0 8px 0;">{"".join(rows)}</table>')


def _subhead(text: str) -> str:
    return (f'<div style="font-size:12px;font-weight:700;color:{MUTED};margin:10px 0 4px 0;">'
            f'{escape(text)}</div>')


def _portfolio_card(rows: render.PortfolioRows) -> str:
    d = rows.delta
    parts = []
    if d.stale:
        parts.append(_badge(f"STALE — written down {d.age_days} days ago", "bear", strong=True))
    if rows.changes:
        lines = []
        for change in rows.changes:
            verdict = change.reading.verdict
            if verdict in holdings.LOUD:
                was = "new to the file" if change.before is None else f"was {change.before}"
                lines.append("<tr>" + _cell(_badge(verdict, VERDICT_TONE.get(verdict, "neutral"),
                                                   strong=True))
                             + _cell(_ticker(change.ticker))
                             + _cell(escape(render.roster_text(change.reading)), mono=True)
                             + _cell(escape(render.where_text(change.reading)))
                             + _cell(escape(was), muted=True) + "</tr>")
            else:
                lines.append("<tr>" + _cell(_badge(verdict, "neutral"))
                             + _cell(_ticker(change.ticker))
                             + f'<td colspan="3" style="padding:6px 0;font-size:13px;'
                               f'color:{MUTED};border-bottom:1px solid #f3f4f6;">'
                               f'no longer {escape(str(change.before))}</td></tr>')
        parts.append(_table(lines))
    if rows.arrivals:
        parts.append(_subhead("Reached a level overnight"))
        lines = []
        for spot in rows.arrivals:
            level = spot.level
            band = (num(level.bottom) if level.top == level.bottom
                    else f"{num(level.bottom)}–{num(level.top)}")
            tone = "bull" if level.side == "support" else "bear"
            lines.append("<tr>" + _cell(_ticker(spot.reading.holding.ticker))
                         + _cell(escape(num(spot.reading.price)), mono=True, align="right")
                         + _cell(_badge(f"{level.timeframe} {level.side}", tone))
                         + _cell(escape(band), mono=True)
                         + _cell(escape(render.roster_text(spot.reading)), mono=True, muted=True)
                         + "</tr>")
        parts.append(_table(lines))
        if rows.arrivals_dropped:
            parts.append(f'<div>{_muted(f"and {rows.arrivals_dropped} more — ")}'
                         f'{_code("uv run review --levels")}</div>')
    footer = []
    if d.left:
        n = len(d.left)
        footer.append(f"{n} position{'' if n == 1 else 's'} stepped off a level")
    standing = [f"{n} {v}" for v, n in sorted(rows.standing_loud.items())]
    if rows.watching:
        standing.append(f"{rows.watching} WATCH")
    standing.append(f"{d.positions} positions standing")
    footer.append(" · ".join(standing))
    parts.append(f'<div style="margin:8px 0 0 0;">{_muted(" · ".join(footer))}</div>')
    if d.unpriced:
        parts.append(f'<div style="margin:6px 0 0 0;">{_muted(f"No price for {", ".join(d.unpriced)} — ")}'
                     f'{_code(f"uv run fetch-prices --portfolio {d.portfolio}")}</div>')
    aside = (f'<span style="font-weight:400;text-transform:none;letter-spacing:0;color:{FAINT};">'
             f' · first look</span>') if d.bootstrap else ""
    return _card(f"Portfolio — {d.portfolio}", "".join(parts), aside=aside)


# ── net worth, footer ─────────────────────────────────────────────────────────

def _networth_section(doc: EmailDoc) -> str:
    net = doc.net_worth
    if net is None:
        return ""
    chips = []
    for days, change in sorted(doc.net_worth_changes.items()):
        tone = "bull" if change.amount >= 0 else "bear"
        chips.append(_badge(f"{render._change(change)} {days}d{'*' if change.uneven else ''}",
                            tone, strong=True))
    if not chips:
        chips.append(_badge("first look", "neutral"))
    notes = [f"across {net.pots} mandate{'' if net.pots == 1 else 's'}"]
    if net.unpriced:
        notes.append(f"excludes {net.unpriced} unpriced")
    if not doc.net_worth_recorded:
        notes.append("not recorded")
    mismatch = ""
    if net.mismatched:
        mismatch = (f'<div style="margin:6px 0 0 0;">{_muted(f"Left out {", ".join(net.mismatched)}"
                    f" — our price and the file disagree")}</div>')
    return _card("Net worth",
                 f'<div style="font-size:26px;font-weight:800;margin:0 0 6px 0;">'
                 f'{escape(treasury_render.money(net.total))}</div><div>{"".join(chips)}</div>'
                 f'<div>{_muted(" · ".join(notes))}</div>{mismatch}')


def _footer(doc: EmailDoc) -> str:
    stamp = " ".join(line.strip() for line in render._provenance(doc.delta, doc.run)
                     if line.strip())
    return _row(f'<div style="text-align:center;padding:4px 0 8px 0;font-size:11px;'
                f'color:{FAINT};">{escape(stamp)}</div>')
