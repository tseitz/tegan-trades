import textwrap
from datetime import date

import pytest

from ingestion.channel import VideoMeta, VideoStub
from ingestion.roster import (
    ChannelResult,
    ChannelTarget,
    active_targets,
    format_summary,
    ingest_channel,
    ingest_roster,
    load_watchlist,
)


WATCHLIST = textwrap.dedent("""
    people:
      - name: "Alice"
        status: active
        channels:
          - { platform: youtube, id: "@alice", access: ok }
          - { platform: x, id: "alice", access: grok }
      - name: "Bob"
        status: active
        backfill: { max_videos: 10, max_age_days: 365 }
        channels:
          - { platform: youtube, id: "UCbbbbbbbbbbbbbbbbbbbbbb", access: ok }
      - name: "Cara"
        status: candidate
        channels:
          - { platform: youtube, id: "@cara", access: ok }
      - name: "Dan"
        status: active
        channels:
          - { platform: youtube, id: "@danpaid", access: paid }
""")


def _write(tmp_path, text):
    p = tmp_path / "watchlist.yaml"
    p.write_text(text)
    return p


def test_active_targets_selects_active_youtube_ok_only(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    targets = active_targets(wl)
    names = [(t.person, t.channel) for t in targets]
    assert names == [("Alice", "@alice"), ("Bob", "UCbbbbbbbbbbbbbbbbbbbbbb")]
    # Cara excluded (candidate); Dan excluded (access=paid); Alice's x channel excluded


def test_active_targets_applies_defaults_and_overrides(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    by_person = {t.person: t for t in active_targets(wl)}
    assert by_person["Alice"] == ChannelTarget("Alice", "@alice", 50, 730)
    assert by_person["Bob"] == ChannelTarget("Bob", "UCbbbbbbbbbbbbbbbbbbbbbb", 10, 365)


def _stub(vid, tab="videos"):
    return VideoStub(vid, f"title-{vid}", tab)


def _meta(vid, published_at, was_live=False):
    return VideoMeta(vid, f"title-{vid}", published_at, 100, "UCabc", was_live)


def test_ingest_channel_happy_path_saves_rich_metadata(tmp_path):
    saved = {}
    target = ChannelTarget("Alice", "@alice", max_videos=50, max_age_days=730)

    result = ingest_channel(
        target,
        today=date(2026, 7, 22),
        resolve=lambda ch, n: [_stub("vid00000001", "streams")],
        hydrate=lambda vid: _meta(vid, "2026-07-01", was_live=True),
        fetch_transcript=lambda vid: f"text-{vid}",
        exists=lambda platform, vid: False,
        save_video=lambda vid, meta, text: saved.update({vid: (meta, text)}),
    )

    assert result.ingested == ["vid00000001"]
    assert result.skipped == [] and result.stale == [] and result.failed == []
    meta, text = saved["vid00000001"]
    assert text == "text-vid00000001"
    assert meta["person"] == "Alice"
    assert meta["published_at"] == "2026-07-01"
    assert meta["was_live"] is True
    assert meta["channel_id"] == "UCabc"
    assert meta["url"] == "https://www.youtube.com/watch?v=vid00000001"
    assert meta["title"] == "title-vid00000001"


def test_ingest_channel_skips_already_stored(tmp_path):
    target = ChannelTarget("Alice", "@alice", 50, 730)
    result = ingest_channel(
        target,
        today=date(2026, 7, 22),
        resolve=lambda ch, n: [_stub("have0000001")],
        hydrate=lambda vid: pytest.fail("should not hydrate a stored video"),
        fetch_transcript=lambda vid: "x",
        exists=lambda platform, vid: True,
        save_video=lambda *a: pytest.fail("should not save"),
    )
    assert result.skipped == ["have0000001"]
    assert result.ingested == []


def test_ingest_channel_buckets_stale_and_missing_date(tmp_path):
    target = ChannelTarget("Alice", "@alice", 50, 730)
    result = ingest_channel(
        target,
        today=date(2026, 7, 22),
        resolve=lambda ch, n: [_stub("old0000001"), _stub("nodate00001")],
        hydrate=lambda vid: _meta(vid, "2019-01-01") if vid == "old0000001"
                            else _meta(vid, None),
        fetch_transcript=lambda vid: "x",
        exists=lambda platform, vid: False,
        save_video=lambda *a: pytest.fail("neither video should be saved"),
    )
    assert result.stale == ["old0000001"]
    assert result.failed == [("nodate00001", "missing published_at")]


def test_ingest_channel_logs_transcript_failure_and_continues(tmp_path):
    saved = {}
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def transcript(vid):
        if vid == "bad0000001":
            raise RuntimeError("no captions")
        return f"text-{vid}"

    result = ingest_channel(
        target,
        today=date(2026, 7, 22),
        resolve=lambda ch, n: [_stub("bad0000001"), _stub("good000001")],
        hydrate=lambda vid: _meta(vid, "2026-07-01"),
        fetch_transcript=transcript,
        exists=lambda platform, vid: False,
        save_video=lambda vid, meta, text: saved.update({vid: text}),
    )
    assert result.ingested == ["good000001"]
    assert result.failed == [("bad0000001", "transcript: no captions")]
    assert "good000001" in saved and "bad0000001" not in saved


def test_ingest_roster_runs_each_target(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    calls = []

    def fake_ingest_channel(target, **kwargs):
        calls.append(target.person)
        r = ChannelResult(target.person, target.channel)
        r.ingested.append("vid_" + target.person)
        return r

    results = ingest_roster(wl, _ingest_channel=fake_ingest_channel)
    assert calls == ["Alice", "Bob"]
    assert [r.person for r in results] == ["Alice", "Bob"]


def test_format_summary_reports_counts():
    r = ChannelResult("Alice", "@alice")
    r.ingested = ["a", "b"]
    r.skipped = ["c"]
    r.stale = ["d"]
    r.failed = [("e", "transcript: no captions")]
    text = format_summary([r])
    assert "Alice" in text
    assert "2 ingested" in text
    assert "1 skipped" in text
    assert "1 stale" in text
    assert "1 failed" in text
    assert "no captions" in text  # failure reasons surfaced
