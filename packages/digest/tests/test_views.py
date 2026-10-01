"""The VIEWS block: windowing, bucketing and the hard wrap."""
from __future__ import annotations

from datetime import date

from core.stance import ExtractedStance, build_stance
from core.thesis import Source
from digest import views

TODAY = date(2026, 9, 30)

YT_SIDECAR = {
    "url": "https://www.youtube.com/watch?v=ZH5ivfBD-KQ",
    "title": "Gold: Dubious Speculation",
    "published_at": "2026-09-29",
    "duration": 1390,
    "channel_id": "UCRvqjQPSeaWn-uEx-w0XOIg",
    "person": "Benjamin Cowen",
    "was_live": False,
    "platform": "youtube",
    "source_id": "ZH5ivfBD-KQ",
}

X_SIDECAR = {
    "person": "Magic Lines (Stephen)",
    "handle": "0xmagiclines",
    "url": "https://x.com/0xmagiclines",
    "published_at": "2026-09-29",
    "post_count": 6,
    "post_urls": ["https://x.com/0xmagiclines/status/2080471114564899166"],
    "platform": "x",
    "source_id": "0xmagiclines-2026-09-29",
}


def stance(ref, asset, lean, *, person="Benjamin Cowen", published="2026-09-29",
           conviction=None, watching=None):
    src = Source(person=person, platform=ref.split("/")[0], url="https://example.com",
                 published_at=published, transcript_ref=ref)
    return build_stance(
        ExtractedStance.model_validate({
            "asset": asset, "lean": lean, "conviction": conviction, "watching": watching,
            "rationale": f"{person} on {asset}"}),
        source=src, model="m", extracted_at="2026-09-30T00:00:00+00:00")


def sidecar(ref, published, person="P", title="T"):
    platform, source_id = ref.split("/")
    return {"title": title, "published_at": published, "person": person, "url": "u",
            "platform": platform, "source_id": source_id}


def fold(stances, sidecars, **kw):
    return views.fold(stances, sidecars, today=TODAY, collapsed=kw.pop("collapsed", frozenset()),
                      **kw)


def test_buckets_lean_and_conviction():
    ref = "youtube/ZH5ivfBD-KQ"
    ss = [
        stance(ref, "SOL", "bullish"),
        stance(ref, "BTC", "bullish", conviction="high"),
        stance(ref, "ETH", "bullish", conviction="med"),
        stance(ref, "BTC", "bullish", conviction="med"),
        stance(ref, "GOLD", "bearish", conviction="high"),
        stance(ref, "XPL", "neutral"),
        stance(ref, "CRCL", "uncertain", conviction="high"),
        stance(ref, "ANOM", "bullish", conviction="low"),
        stance(ref, "PEPE", "bearish", conviction="low"),
    ]
    out = views.lines(fold(ss, [YT_SIDECAR]))
    text = "\n".join(out)
    assert 'Benjamin Cowen · Sep 29 · "Gold: Dubious Speculation"' in text
    assert "Bullish     BTC (high), SOL, ETH" in text
    assert "Bearish     GOLD (high)" in text
    assert "Unsure      CRCL, XPL" in text
    assert "Punts       ANOM, PEPE" in text
    assert text.count("BTC") == 1


def test_empty_buckets_omitted():
    out = "\n".join(views.lines(fold([stance("youtube/ZH5ivfBD-KQ", "BTC", "bullish")],
                                     [YT_SIDECAR])))
    assert "Bearish" not in out and "Punts" not in out and "Unsure" not in out


def test_window_boundary():
    inside = sidecar("youtube/in", "2026-09-28", person="A")
    outside = sidecar("youtube/out", "2026-09-27", person="B")
    result = fold([], [inside, outside])
    assert [v.person for v in result] == ["A"]
    assert fold([], [outside], window_days=4)[0].person == "B"


def test_undistilled_and_no_market_views():
    sidecars = [sidecar("youtube/a", "2026-09-30", "P", "Fresh"),
                sidecar("youtube/b", "2026-09-29", "P", "Chat"),
                sidecar("youtube/c", "2026-09-28", "P", "Views")]
    ss = [stance("youtube/c", "BTC", "bullish", person="P", published="2026-09-28")]
    result = fold(ss, sidecars, distilled=frozenset({"youtube/b", "youtube/c"}))
    videos = {v.ref: v for v in result[0].videos}
    assert not videos["youtube/a"].distilled and videos["youtube/b"].distilled
    text = "\n".join(views.lines(result))
    assert '1 video not yet distilled' in text
    assert 'Sep 29 · "Chat" — no market views' in text
    assert "Fresh" not in text


def test_x_sidecar_labelled_by_handle_and_stance_fallback_without_sidecar():
    ref = "x/0xmagiclines-2026-09-29"
    ss = [stance(ref, "NQ", "bullish", person="Magic Lines (Stephen)"),
          stance("x/orphan-2026-09-30", "MU", "bearish", person="Orphan", published="2026-09-30")]
    result = fold(ss, [X_SIDECAR])
    text = "\n".join(views.lines(result))
    assert "Magic Lines (Stephen) · Sep 29 · @0xmagiclines · 6 posts" in text
    assert "Orphan · Sep 30 · (untitled)" in text


def test_bad_sidecars_skipped():
    junk = [None, "x", {"platform": "youtube", "source_id": "z"},
            {"published_at": "2026-09-30"}]
    assert fold([], junk) == []


def test_people_ordered_by_newest_video_then_name():
    sidecars = [sidecar("youtube/a", "2026-09-29", "Zed"), sidecar("youtube/b", "2026-09-30", "Yan"),
                sidecar("youtube/c", "2026-09-29", "Abe")]
    assert [v.person for v in fold([], sidecars)] == ["Yan", "Abe", "Zed"]


def test_collapsed_channels_sort_last_even_when_newest():
    sidecars = [sidecar("youtube/a", "2026-09-30", "tasty"), sidecar("youtube/b", "2026-09-28", "Abe")]
    order = [v.person for v in fold([], sidecars, collapsed=frozenset({"tasty"}))]
    assert order == ["Abe", "tasty"]


def test_watching_dedupe_cap_and_truncate():
    ref = "youtube/ZH5ivfBD-KQ"
    long = "word " * 60
    ss = [stance(ref, "A", "bullish", watching="low one", conviction="low"),
          stance(ref, "B", "bullish", watching="same"), stance(ref, "C", "bullish", watching="same"),
          stance(ref, "D", "bullish", watching=long, conviction="high"),
          stance(ref, "E", "bullish", watching="fourth")]
    out = views.lines(fold(ss, [YT_SIDECAR]))
    bullets = [ln for ln in out if "•" in ln]
    assert len(bullets) == views.WATCHING_MAX
    assert "…" in "\n".join(out)
    assert "low one" not in "\n".join(out)


def test_every_line_wrapped_to_width():
    ref = "youtube/ZH5ivfBD-KQ"
    ss = [stance(ref, f"TOKEN{i}", "bullish") for i in range(40)]
    ss.append(stance(ref, "BTC", "bearish", watching="long " * 80))
    sc = dict(YT_SIDECAR, title="A very long title " * 10)
    out = views.lines(fold(ss, [sc]), big_picture={"Benjamin Cowen": "overall posture " * 20})
    assert out and all(len(ln) <= views.WIDTH for ln in out)
    assert any(ln.lstrip().startswith("Big picture") for ln in out)


def test_collapsed_format_and_tail():
    sidecars = [sidecar("youtube/a", "2026-09-30", "tasty", "Micron"),
                sidecar("youtube/b", "2026-09-29", "tasty", "Jobs"),
                sidecar("youtube/c", "2026-09-29", "tasty", "Quiet")]
    ss = [stance("youtube/b", "SPX", "bullish", person="tasty"),
          stance("youtube/b", "VIX", "bearish", person="tasty", conviction="high")]
    out = views.lines(fold(ss, sidecars, collapsed=frozenset({"tasty"}),
                           distilled=frozenset({"youtube/c"})),
                      big_picture={"tasty": "ignored"})
    assert out[0] == "  tasty · 3 videos"
    assert '    Sep 29 · "Jobs" — Bullish SPX · Bearish VIX' in out
    assert "    +2 with no views or not yet distilled" in out
    assert "ignored" not in "\n".join(out)


def test_empty_input():
    assert fold([], []) == []
    assert views.lines([]) == []
