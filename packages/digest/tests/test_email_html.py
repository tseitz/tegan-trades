"""The HTML painter: what it shows, what it never shows, and that it agrees with the text."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from html import unescape

from core import networth
from core.nearby import SUPPORT, WEEKLY_ZONE, Level
from core.review import ADD, HOLD, TRIM, Holding, Location, Reading, RosterLean
from core.setups import WEEKLY
from core.stance import ExtractedStance, build_stance
from core.thesis import Source
from digest import diff, email_html, holdings, render, views
from review.levels import Spotlight

REF = "youtube/abc123"


def _snap(as_of: str) -> dict:
    return {"run": f"{as_of}T11:00:00+00:00", "as_of": as_of, "score_version": 1,
            "filters": {}, "population": {"qualified": 1}, "rows": []}


def _delta(**over) -> diff.QueueDelta:
    delta = diff.compare(_snap("2026-10-28"), _snap("2026-10-29"))
    return replace(delta, **over) if over else delta


def _stance(asset, lean, *, conviction=None, watching=None, ref=REF, person="Raoul Pal"):
    src = Source(person=person, platform="youtube", url="https://example.com",
                 published_at="2026-10-29", transcript_ref=ref)
    return build_stance(
        ExtractedStance.model_validate({"asset": asset, "lean": lean, "conviction": conviction,
                                        "watching": watching, "rationale": "r"}),
        source=src, model="m", extracted_at="2026-10-29T12:00:00+00:00")


def _video(stances, *, title="Is the Bull Trend Broken?", url="https://youtu.be/abc123",
           priors=None):
    return views.Video(ref=REF, title=title, published_at="2026-10-29", url=url,
                       distilled=True, stances=tuple(stances), priors=priors or {})


def _person(video, *, person="Raoul Pal", collapsed=False):
    return views.PersonView(person=person, collapsed=collapsed, videos=(video,))


def _reading(ticker, verdict):
    return Reading(
        holding=Holding(ticker=ticker, shares=1.0, cost=None),
        roster=RosterLean(lean="bullish", bulls=3, bears=1, people=4, newest=date(2026, 10, 1),
                          age_days=7, voices=("A",), thin=False),
        location=Location(where="at_support", basis="zone", position=None),
        verdict=verdict, price=493.79, weekly_trend="uptrend")


def _portfolio():
    spot = Spotlight(reading=_reading("MU", HOLD),
                     level=Level(kind=WEEKLY_ZONE, timeframe=WEEKLY, side=SUPPORT, top=1042.0,
                                 bottom=967.38, distance=0.0), others=0)
    return holdings.HoldingsDelta(
        portfolio="retirement",
        changed=(holdings.Change(ticker="DELL", before=HOLD, reading=_reading("DELL", ADD)),
                 holdings.Change(ticker="VRT", before=None, reading=_reading("VRT", TRIM))),
        arrived=(spot,), standing={ADD: 7}, positions=56)


def _doc(**over) -> email_html.EmailDoc:
    eth = _stance("ETH", "bearish", conviction="high", watching="ETH/BTC reclaiming its cloud")
    video = _video([eth, _stance("BTC", "bullish"), _stance("UNI", "bullish", conviction="high")],
                   priors={eth.id: {"lean": "bullish", "published_at": "2026-09-03"}})
    fields = {
        "subject": "13 new · 12 out", "delta": _delta(), "views": (_person(video),),
        "big_picture": {"Raoul Pal": "Turns bearish on ETH."},
        "views_heading": render.VIEWS_HEADING_SINCE,
        "roster": "  ETH: triple flip bearish.\n  OIL: two flipped bullish.",
        "holdings_deltas": (_portfolio(),),
        "net_worth": networth.NetWorth(total=186470.77, pots=7, unpriced=1),
        "net_worth_changes": {1: networth.Change(days=1, baseline=None, amount=4900.0, pct=2.7,
                                              uneven=True)}}
    fields.update(over)
    return email_html.EmailDoc(**fields)


def _text(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", html))


def test_the_page_carries_each_section_it_was_given():
    html = email_html.page(_doc())
    text = _text(html)
    for expected in ("Roster Digest", "Roster moved", "triple flip bearish", "Raoul Pal",
                     "Turns bearish on ETH.", "ETH/BTC reclaiming its cloud", "Setups queue",
                     "uv run setups", "Portfolio — retirement", "Net worth", "$186,470.77"):
        assert expected in text, expected


def test_a_flip_shows_what_it_was():
    assert "was bullish Sep 3" in _text(email_html.page(_doc()))


def test_empty_sections_print_nothing():
    html = email_html.page(_doc(views=(), roster=None, holdings_deltas=(), net_worth=None))
    text = _text(html)
    for absent in ("Roster moved", "Views", "Portfolio", "Net worth"):
        assert absent not in text, absent


def test_holding_and_treasury_have_no_way_in():
    fields = set(email_html.EmailDoc.__dataclass_fields__)
    assert not fields & {"holding", "resting", "treasury_delta"}
    text = _text(email_html.page(_doc()))
    assert "HOLDING" not in text and "TREASURY" not in text


def test_user_text_is_escaped():
    video = _video([_stance("BTC", "bullish")], title="<script>alert(1)</script>")
    html = email_html.page(_doc(views=(_person(video),),
                                roster="  BTC: <b>bold</b> & co"))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>bold</b>" not in html


def test_a_non_http_url_is_not_linked():
    video = _video([_stance("BTC", "bullish")], url="javascript:alert(1)")
    assert "javascript:" not in email_html.page(_doc(views=(_person(video),)))


def test_tickers_link_to_a_chart_with_the_symbol_quoted():
    assert 'href="https://www.tradingview.com/chart/?symbol=BRK.B%26X"' in email_html._ticker(
        "BRK.B&X")
    assert 'symbol=ETH"' in email_html.page(_doc())


def test_the_charset_is_declared_in_the_document():
    assert '<meta charset="utf-8">' in email_html.page(_doc())


def test_an_over_budget_page_is_redrawn_compact(monkeypatch):
    many = tuple(_video([_stance(f"A{i}", "bullish", watching="w" * 200)]) for i in range(3))
    view = views.PersonView(person="P", collapsed=False, videos=many)
    full = email_html._page(_doc(views=(view,)), compact=False)
    monkeypatch.setattr(email_html, "SOFT_BUDGET", len(full) - 1)
    assert len(email_html.page(_doc(views=(view,)))) < len(full)


def test_fallback_is_the_escaped_text():
    html = email_html.fallback("QUEUE <x> · 1 new")
    assert "QUEUE &lt;x&gt; · 1 new" in html and '<meta charset="utf-8">' in html


def test_the_html_names_every_ticker_and_verdict_the_text_body_does():
    """Parity with ``render.markdown``: the two painters read the same objects, so anything the
    text shows in VIEWS, ROSTER MOVED or PORTFOLIO must reach the inbox too."""
    doc = _doc()
    body = render.markdown(doc.delta, roster=doc.roster, holdings_deltas=doc.holdings_deltas,
                           views="\n".join(views.lines(list(doc.views),
                                                       big_picture=doc.big_picture)),
                           views_heading=doc.views_heading)
    html_text = _text(email_html.page(doc))
    tokens = set(re.findall(r"\b[A-Z][A-Z0-9_]{1,9}\b", body))
    tokens -= {"VIEWS", "ROSTER", "MOVED", "PORTFOLIO", "QUEUE", "NOTE", "STALE", "LONG",
               "SHORT", "RUN"}
    missing = sorted(t for t in tokens if t not in html_text)
    assert not missing
