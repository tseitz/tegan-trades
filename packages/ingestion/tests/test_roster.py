import textwrap
from datetime import date

import pytest

from youtube_transcript_api import TranscriptsDisabled

from ingestion import deadletters
from ingestion.channel import VideoMeta, VideoStub
from ingestion.roster import (
    ChannelResult,
    ChannelTarget,
    RunAborted,
    active_targets,
    format_summary,
    ingest_channel,
    ingest_roster,
    load_watchlist,
    unreachable_active,
)
from ingestion.youtube import TranscriptBlocked


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


def test_ingest_channel_aborts_run_on_block_preserving_partial(tmp_path):
    saved = {}
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def transcript(vid):
        if vid == "blocked0001":
            raise TranscriptBlocked("YouTube IP block")
        return f"text-{vid}"

    with pytest.raises(RunAborted) as excinfo:
        ingest_channel(
            target,
            today=date(2026, 7, 22),
            resolve=lambda ch, n: [_stub("good000001"), _stub("blocked0001"), _stub("good000002")],
            hydrate=lambda vid: _meta(vid, "2026-07-01"),
            fetch_transcript=transcript,
            exists=lambda platform, vid: False,
            save_video=lambda vid, meta, text: saved.update({vid: text}),
        )

    result = excinfo.value.result
    assert result.ingested == ["good000001"]          # saved before the block
    assert any(v == "blocked0001" for v, _ in result.failed)
    assert "good000002" not in saved                   # never attempted after block


def test_ingest_roster_stops_after_block(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))   # Alice, Bob both active

    def fake_ingest_channel(target, **kwargs):
        r = ChannelResult(target.person, target.channel)
        if target.person == "Alice":
            r.ingested.append("vidA")
            r.failed.append(("blockedA", "transcript: blocked [BLOCKED — run aborted]"))
            raise RunAborted(r)
        r.ingested.append("vid_" + target.person)      # Bob — must never run
        return r

    results = ingest_roster(wl, _ingest_channel=fake_ingest_channel)
    assert [r.person for r in results] == ["Alice"]    # Bob not processed
    assert results[0].ingested == ["vidA"]             # Alice's partial preserved


def test_format_summary_reports_counts():
    r = ChannelResult("Alice", "@alice")
    r.ingested = ["a", "b"]
    r.skipped = ["c"]
    r.stale = ["d"]
    r.dead = ["e"]
    r.pending = ["f"]
    r.failed = [("g", "transcript: something nobody expected")]
    text = format_summary([r])
    assert "Alice" in text
    assert "2 ingested" in text
    assert "1 skipped" in text
    assert "1 stale" in text
    assert "1 dead" in text
    assert "1 pending" in text
    assert "1 failed" in text
    assert "nobody expected" in text  # failure reasons surfaced


# ── permanently dead videos ─────────────────────────────────────────────────

def test_captions_disabled_is_recorded_dead_rather_than_failed(tmp_path):
    """The nightly of 2026-07-27 reported `10 failed` and all ten were expected, so an
    eleventh — a genuinely new failure — could not have been noticed. Permanent outcomes move
    out so `failed` goes back to meaning "nobody expected this"."""
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def transcript(vid):
        raise TranscriptsDisabled(vid)

    result = ingest_channel(
        target,
        today=date(2026, 7, 27),
        resolve=lambda ch, n: [_stub("nocaption01")],
        hydrate=lambda vid: _meta(vid, "2026-07-01"),
        fetch_transcript=transcript,
        exists=lambda platform, vid: False,
        save_video=lambda *a: pytest.fail("nothing to save"),
    )
    assert result.dead == ["nocaption01"]
    assert result.failed == []
    assert result.dead_registry["nocaption01"]["reason"] == deadletters.NO_CAPTIONS


def test_a_fresh_upload_without_captions_yet_is_pending_not_dead(tmp_path):
    """The regression this rule was written for, from live data. TTrades' `Je7cd9HJUBE`
    ("Morning Q&A", published 2026-07-27) raised `TranscriptsDisabled` in the 06:30 nightly and
    ingested cleanly at 11:00 the same day — YouTube generates automatic captions well after
    the upload lands. Without the grace period the registry would have buried a same-day video
    from an active roster member forever, which is precisely the false positive it exists to
    be too careful to make."""
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def transcript(vid):
        raise TranscriptsDisabled(vid)

    result = ingest_channel(
        target,
        today=date(2026, 7, 27),
        resolve=lambda ch, n: [_stub("Je7cd9HJUBE")],
        hydrate=lambda vid: _meta(vid, "2026-07-27"),   # published today
        fetch_transcript=transcript,
        exists=lambda platform, vid: False,
        save_video=lambda *a: pytest.fail("nothing to save"),
    )
    assert result.pending == ["Je7cd9HJUBE"]
    assert result.dead == [] and result.failed == []
    assert result.dead_registry == {}


def test_an_already_dead_video_is_not_re_fetched(tmp_path):
    target = ChannelTarget("Alice", "@alice", 50, 730)
    registry = deadletters.record(
        "nocaption01", deadletters.NO_CAPTIONS, registry={}, today=date(2026, 7, 1)
    )
    result = ingest_channel(
        target,
        today=date(2026, 7, 27),
        resolve=lambda ch, n: [_stub("nocaption01")],
        hydrate=lambda vid: pytest.fail("should not hydrate a dead video"),
        fetch_transcript=lambda vid: pytest.fail("should not fetch a dead video"),
        exists=lambda platform, vid: False,
        save_video=lambda *a: pytest.fail("nothing to save"),
        dead=registry,
    )
    assert result.dead == ["nocaption01"]


def test_a_dead_video_is_still_counted_rather_than_vanishing(tmp_path):
    """A registry whose entries stopped appearing anywhere would be unfalsifiable — "correctly
    skipped" and "quietly lost" would look identical, which is the failure `verify-roster`
    exists to catch one level up."""
    target = ChannelTarget("Alice", "@alice", 50, 730)
    registry = deadletters.record(
        "nocaption01", deadletters.NO_CAPTIONS, registry={}, today=date(2026, 7, 1)
    )
    result = ingest_channel(
        target,
        today=date(2026, 7, 27),
        resolve=lambda ch, n: [_stub("nocaption01")],
        hydrate=lambda vid: _meta(vid, "2026-07-20"),
        fetch_transcript=lambda vid: "x",
        exists=lambda platform, vid: False,
        save_video=lambda *a: None,
        dead=registry,
    )
    assert "1 dead" in format_summary([result])


def test_a_deleted_video_is_pending_not_buried(tmp_path):
    """A metadata failure happens *before* hydration, so there is no `published_at` to
    age-gate against — the grace period that makes a transcript verdict safe cannot apply
    here. Retried forever rather than buried on a prose match alone: a couple of wasted
    hydrates a night against the risk of discarding a video because yt-dlp reworded something.
    `failed` still ends up clean, which was the point."""
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def hydrate(vid):
        raise RuntimeError("ERROR: [youtube] gone0000001: This video is not available")

    result = ingest_channel(
        target,
        today=date(2026, 7, 27),
        resolve=lambda ch, n: [_stub("gone0000001")],
        hydrate=hydrate,
        fetch_transcript=lambda vid: pytest.fail("should not reach the transcript"),
        exists=lambda platform, vid: False,
        save_video=lambda *a: pytest.fail("nothing to save"),
    )
    assert result.pending == ["gone0000001"]
    assert result.dead == [] and result.failed == []
    assert result.dead_registry == {}


def test_a_scheduled_livestream_is_pending_not_dead_and_not_failed(tmp_path):
    """It becomes fetchable on its own, so retrying is right — but it is not a failure, and
    burying it would discard the stream permanently the day before it aired."""
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def hydrate(vid):
        raise RuntimeError("ERROR: [youtube] soon0000001: This live event will begin in 2 days.")

    result = ingest_channel(
        target,
        today=date(2026, 7, 27),
        resolve=lambda ch, n: [_stub("soon0000001")],
        hydrate=hydrate,
        fetch_transcript=lambda vid: pytest.fail("should not reach the transcript"),
        exists=lambda platform, vid: False,
        save_video=lambda *a: pytest.fail("nothing to save"),
    )
    assert result.pending == ["soon0000001"]
    assert result.dead == [] and result.failed == []
    assert result.dead_registry == {}


def test_an_unrecognised_error_stays_a_failure(tmp_path):
    """The safe direction. A mis-parse that leaves something in `failed` costs one wasted
    fetch a night; a mis-parse that buries it discards the video for good."""
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def hydrate(vid):
        raise RuntimeError("ERROR: [youtube] weird000001: something new and unclassified")

    result = ingest_channel(
        target,
        today=date(2026, 7, 27),
        resolve=lambda ch, n: [_stub("weird000001")],
        hydrate=hydrate,
        fetch_transcript=lambda vid: pytest.fail("should not reach the transcript"),
        exists=lambda platform, vid: False,
        save_video=lambda *a: pytest.fail("nothing to save"),
    )
    assert result.dead == []
    assert [vid for vid, _ in result.failed] == ["weird000001"]


def test_a_blocked_video_is_never_buried(tmp_path):
    """The one case that must not become permanent: an IP block is systemic and temporary, so
    recording it would discard a video forever over one bad night's egress."""
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def transcript(vid):
        raise TranscriptBlocked("ip blocked")

    with pytest.raises(RunAborted) as caught:
        ingest_channel(
            target,
            today=date(2026, 7, 27),
            resolve=lambda ch, n: [_stub("blocked0001")],
            hydrate=lambda vid: _meta(vid, "2026-07-20"),
            fetch_transcript=transcript,
            exists=lambda platform, vid: False,
            save_video=lambda *a: pytest.fail("nothing to save"),
        )
    result = caught.value.result
    assert result.dead == []
    assert result.dead_registry == {}
    assert [vid for vid, _ in result.failed] == ["blocked0001"]


def test_the_registry_persists_across_a_sweep(tmp_path):
    """Written once at the end of the sweep, so tomorrow's run starts from what today learned."""
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    dead_path = tmp_path / "_dead.json"

    def fake_ingest_channel(target, *, root, today, dead):
        r = ChannelResult(target.person, target.channel)
        r.dead = [f"dead-{target.person}"]
        r.dead_registry = deadletters.record(
            f"dead-{target.person}", deadletters.NO_CAPTIONS,
            registry=dead, today=date(2026, 7, 27),
        )
        return r

    ingest_roster(wl, _ingest_channel=fake_ingest_channel, dead_path=dead_path)
    assert set(deadletters.load(dead_path)) == {"dead-Alice", "dead-Bob"}


# ── silently-skipped active people ──────────────────────────────────────────

def test_unreachable_active_people_are_reported_not_silently_dropped(tmp_path):
    """An `active` person whose channels are all unreachable vanishes from the sweep with
    no signal. Checkmate sat that way for days: marked active, configured as a podcast,
    contributing nothing. A consensus count is only meaningful if you know the denominator.
    """
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    skipped = unreachable_active(wl)
    by_name = {s.person: s for s in skipped}
    assert set(by_name) == {"Dan"}          # active, but his only channel is access=paid
    assert "youtube/@danpaid (paid)" in by_name["Dan"].reason


def test_active_person_with_a_non_youtube_platform_is_reported(tmp_path):
    wl = load_watchlist(_write(tmp_path, textwrap.dedent("""
        people:
          - name: "Podcaster"
            status: active
            channels:
              - { platform: podcast, id: "Some Show", access: ok }
    """)))
    assert active_targets(wl) == []
    skipped = unreachable_active(wl)
    assert [s.person for s in skipped] == ["Podcaster"]
    assert "podcast/Some Show (ok)" in skipped[0].reason


def test_active_person_with_no_channels_at_all_is_reported(tmp_path):
    wl = load_watchlist(_write(tmp_path, textwrap.dedent("""
        people:
          - name: "Ghost"
            status: active
    """)))
    assert [s.person for s in unreachable_active(wl)] == ["Ghost"]


def test_reachable_and_non_active_people_are_not_reported(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    names = {s.person for s in unreachable_active(wl)}
    assert "Alice" not in names and "Bob" not in names   # reachable
    assert "Cara" not in names                            # candidate, not active
