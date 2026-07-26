from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from ingestion import verify
from ingestion.verify import (
    BROKEN,
    CONFIRMED_DORMANT,
    EMPTY,
    OK,
    REVIVED,
    UNDATED,
    ChannelProbe,
)


def _probe(**kw):
    base = dict(person="P", channel="@c", recorded_access="ok",
                tabs={"videos": 5}, newest_title="a video", error="")
    base.update(kw)
    return ChannelProbe(**base)


# ── the two failures that actually happened ─────────────────────────────────────

def test_a_channel_recorded_ok_but_unreachable_is_broken():
    """@RealVision, recorded by Phase 0 as verified `access: ok`, has no videos tab and no
    streams tab. The sweep reported it as `0 ingested, 0 skipped, 0 stale, 0 failed` — the same
    line a healthy up-to-date channel prints."""
    p = _probe(recorded_access="ok", tabs={}, error="does not have a videos tab")
    assert p.verdict == BROKEN


def test_a_channel_recorded_dormant_with_a_recent_video_has_revived():
    """@TraderSZ. The research was right about the uploads tab — newest is 2023-10-01 — and
    wrong about the channel: he moved to livestreams. Only the marker was suppressing him."""
    recent = (date.today() - timedelta(days=5)).isoformat()
    p = _probe(recorded_access="dormant", tabs={"videos": 5, "streams": 5},
               newest_title="Bitcoin for Ledges", newest_at=recent)
    assert p.verdict == REVIVED


def test_an_archive_of_old_videos_is_still_dormant_not_revived():
    """The false alarm this check shipped with. Mark Newton's UC… channel resolves and returns
    videos, newest 'Cnbc interview 3/28/17' — so "has videos" flagged a marker that was right.
    An archive is not a feed. A report that cries wolf gets ignored, which is exactly how the
    @RealVision typo survived a whole sweep."""
    p = _probe(recorded_access="dormant", tabs={"videos": 5},
               newest_title="Cnbc interview 3/28/17", newest_at="2017-03-28")
    assert p.verdict == CONFIRMED_DORMANT


def test_a_written_off_channel_with_videos_but_no_date_is_undated_not_guessed():
    """Refusing to decide is the honest verdict — claiming either way from a title would be
    inventing evidence. It still counts as a problem so it gets looked at."""
    p = _probe(recorded_access="dormant", tabs={"videos": 5}, newest_at=None)
    assert p.verdict == UNDATED


def test_a_revived_verdict_needs_content_not_just_reachability():
    """A reachable-but-empty channel marked dormant is correctly marked."""
    p = _probe(recorded_access="dormant", tabs={"videos": 0, "streams": 0})
    assert p.verdict == CONFIRMED_DORMANT


def test_the_date_is_only_fetched_when_it_could_change_the_verdict():
    """`hydrate` is a full yt-dlp extract per video — fine for the two written-off channels,
    slow across all seventeen. A channel recorded `ok` cannot be REVIVED, so its date is
    irrelevant and must not be fetched."""
    calls = []

    def _hydrate(vid):
        calls.append(vid)
        raise AssertionError("should not be called")

    stub = SimpleNamespace(video_id="v1", title="t")
    verify.probe_channel("P", {"id": "@c", "platform": "youtube", "access": "ok"},
                         _list_tab=lambda c, t, n: [stub], _hydrate=_hydrate)
    assert calls == []


def test_the_date_is_fetched_for_a_written_off_channel_that_has_videos():
    stub = SimpleNamespace(video_id="v1", title="t")
    p = verify.probe_channel(
        "P", {"id": "@c", "platform": "youtube", "access": "dormant"},
        _list_tab=lambda c, t, n: [stub],
        _hydrate=lambda vid: SimpleNamespace(published_at="2026-07-20"))
    assert p.newest_at == "2026-07-20"


def test_a_dormant_channel_that_is_unreachable_is_confirmed_not_broken():
    p = _probe(recorded_access="dormant", tabs={}, error="404")
    assert p.verdict == CONFIRMED_DORMANT


def test_a_reachable_channel_with_no_videos_at_all_is_empty_not_ok():
    """Mark Newton's UC… channel: real, resolvable, and produced 0 transcripts across the whole
    Phase-1 sweep. Distinct from BROKEN — the channel exists, it just has nothing."""
    p = _probe(recorded_access="ok", tabs={"videos": 0, "streams": 0})
    assert p.verdict == EMPTY


def test_a_healthy_channel_is_ok():
    assert _probe().verdict == OK


def test_only_the_tabs_that_resolved_are_counted():
    """Most channels have no /streams tab and that is not an error — resolve_recent already
    treats a missing tab as empty. Only a channel with *no* usable tab is broken."""
    p = _probe(recorded_access="ok", tabs={"videos": 3})
    assert p.reachable and p.verdict == OK


# ── structural checks, no network needed ────────────────────────────────────────

def test_the_same_channel_declared_by_two_people_is_reported():
    """The merge detector. `TraderSZ (Z$1)` conflated two traders in one entry, which attached
    a stranger's X handle to this person's YouTube. The mirror image — one channel claimed by
    two people — would split or double-count a voice, and agreement counts people."""
    watchlist = {"people": [
        {"name": "A", "channels": [{"platform": "youtube", "id": "@shared", "access": "ok"}]},
        {"name": "B", "channels": [{"platform": "youtube", "id": "@shared", "access": "ok"}]},
    ]}
    assert verify.duplicate_channels(watchlist) == [("youtube", "@shared", ["A", "B"])]


def test_channel_ownership_comparison_ignores_case():
    watchlist = {"people": [
        {"name": "A", "channels": [{"platform": "x", "id": "Handle", "access": "grok"}]},
        {"name": "B", "channels": [{"platform": "x", "id": "handle", "access": "grok"}]},
    ]}
    assert verify.duplicate_channels(watchlist)[0][2] == ["A", "B"]


def test_one_person_declaring_a_channel_twice_is_not_a_duplicate():
    watchlist = {"people": [
        {"name": "A", "channels": [
            {"platform": "youtube", "id": "@a", "access": "ok"},
            {"platform": "x", "id": "@a", "access": "grok"},
        ]},
    ]}
    assert verify.duplicate_channels(watchlist) == []


def test_an_alias_that_collides_with_another_persons_name_is_reported():
    """Aliases exist so one speaker's several labels collapse to one voice. An alias that
    matches a *different* person does the opposite — it merges two people."""
    watchlist = {"people": [
        {"name": "The DeFi Report (Michael Nadeau)", "aliases": ["Benjamin Cowen"]},
        {"name": "Benjamin Cowen"},
    ]}
    assert verify.alias_collisions(watchlist) == [
        ("The DeFi Report (Michael Nadeau)", "Benjamin Cowen")]


def test_an_alias_matching_its_own_person_is_fine():
    watchlist = {"people": [{"name": "The DeFi Report", "aliases": ["The DeFi Report"]}]}
    assert verify.alias_collisions(watchlist) == []


def test_a_person_with_no_channels_at_all_is_reported():
    watchlist = {"people": [{"name": "Ghost", "status": "active", "channels": []}]}
    assert verify.channel_less(watchlist) == ["Ghost"]


def test_a_dormant_person_with_no_channels_is_not_reported():
    """`dormant` means kept for the record. Only a person we intend to ingest needs a route."""
    watchlist = {"people": [{"name": "Ghost", "status": "dormant", "channels": []}]}
    assert verify.channel_less(watchlist) == []


# ── the report ──────────────────────────────────────────────────────────────────

def test_the_summary_leads_with_problems_and_exits_nonzero_on_them():
    probes = [_probe(person="Good"), _probe(person="Bad", recorded_access="ok", tabs={},
                                            error="no videos tab")]
    report = verify.Report(probes=probes, duplicates=[], alias_collisions=[], channel_less=[])
    assert report.problems and report.problems[0].person == "Bad"
    assert report.exit_code == 1


def test_a_clean_roster_exits_zero():
    report = verify.Report(probes=[_probe()], duplicates=[], alias_collisions=[],
                           channel_less=[])
    assert report.problems == []
    assert report.exit_code == 0


def test_a_structural_problem_alone_still_fails_the_run():
    report = verify.Report(probes=[_probe()], duplicates=[("x", "@a", ["A", "B"])],
                           alias_collisions=[], channel_less=[])
    assert report.exit_code == 1
