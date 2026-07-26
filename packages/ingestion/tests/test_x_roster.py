from __future__ import annotations

from datetime import date

import pytest

from ingestion import x_roster
from ingestion.x_search import XPost

WATCHLIST = {
    "people": [
        {"name": "TraderMayne", "status": "active", "channels": [
            {"platform": "youtube", "id": "@TraderMayne", "access": "ok"},
            {"platform": "x", "id": "Tradermayne", "access": "grok"},
        ]},
        {"name": "GCR", "status": "dormant", "channels": [
            {"platform": "x", "id": "GiganticRebirth", "access": "grok"},
        ]},
        {"name": "Lyn Alden", "status": "candidate", "channels": [
            {"platform": "x", "id": "lynaldencontact", "access": "grok"},
        ]},
        {"name": "No X Presence", "status": "active", "channels": [
            {"platform": "youtube", "id": "@nox", "access": "ok"},
        ]},
    ],
    "x_grok_digest": ["Tradermayne", "GiganticRebirth"],
}


def test_the_digest_list_decides_who_is_searched():
    """`x_grok_digest` is the curated search set and is capped at 20 by the API. It is not
    the same as the active roster — the point of X is reaching voices with no long-form feed,
    so dormant and candidate people belong in it."""
    assert x_roster.search_handles(WATCHLIST) == ["Tradermayne", "GiganticRebirth"]


def test_a_handle_resolves_to_the_canonical_person():
    """The fix for double-counting: Mayne's X posts and Mayne's livestream must collapse to
    one voice, because agreement counts people, not feeds."""
    people = x_roster.handle_to_person(WATCHLIST)
    assert people["tradermayne"] == "TraderMayne"
    assert people["giganticrebirth"] == "GCR"


def test_resolution_is_case_insensitive_because_handles_are():
    assert x_roster.person_for("TRADERMAYNE", WATCHLIST) == "TraderMayne"


def test_an_unknown_handle_resolves_to_nothing_rather_than_a_guess():
    """Inventing a person label would attach real theses to a name that is not on the roster,
    and every downstream agreement count would inherit it."""
    assert x_roster.person_for("elonmusk", WATCHLIST) is None


def test_a_digest_handle_with_no_person_entry_is_reported_not_ignored():
    """An unattributable handle is a config gap that would otherwise show up as theses
    credited to 'unknown' — the same silent-supply-gap class as the @RealVision typo."""
    broken = {**WATCHLIST, "x_grok_digest": ["Tradermayne", "ghosthandle"]}
    assert x_roster.unattributable(broken) == ["ghosthandle"]


def test_a_person_declared_on_x_but_absent_from_the_digest_is_reported():
    """Lyn Alden has an x channel but is not searched. The digest runs under its cap of 20, so
    that is a free slot going unused rather than a tradeoff — worth surfacing."""
    assert x_roster.undigested(WATCHLIST) == ["lynaldencontact"]


def test_a_deliberately_excluded_handle_is_not_reported_as_a_gap():
    """A report that mixes decisions in with gaps nags on every run and stops being read —
    which is how the @RealVision typo survived a whole sweep. Deliberate drops are marked."""
    dropped = {
        **WATCHLIST,
        "people": WATCHLIST["people"] + [{
            "name": "thiccy", "status": "candidate",
            "channels": [{"platform": "x", "id": "thiccyth0t", "access": "excluded"}],
        }],
    }
    assert "thiccyth0t" not in x_roster.undigested(dropped)


def test_an_excluded_handle_still_resolves_to_its_person():
    """Excluded means "not searched", not "not this person". If a post from them ever arrives —
    a quoted reply, a handle re-added — it must still attribute correctly rather than be
    dropped as unattributable."""
    dropped = {
        **WATCHLIST,
        "people": WATCHLIST["people"] + [{
            "name": "thiccy", "status": "candidate",
            "channels": [{"platform": "x", "id": "thiccyth0t", "access": "excluded"}],
        }],
    }
    assert x_roster.person_for("thiccyth0t", dropped) == "thiccy"


# ── turning a harvest into stored documents ─────────────────────────────────────

def test_documents_carry_the_canonical_person_not_the_handle(tmp_path):
    posts = [XPost("Tradermayne", "1", "https://x.com/Tradermayne/status/1",
                   "2026-07-24", "BTC heavy", "")]
    written = x_roster.store_posts(posts, WATCHLIST, root=tmp_path)
    assert len(written) == 1
    sidecar = (tmp_path / "x" / "Tradermayne-2026-07-24.json")
    import json
    meta = json.loads(sidecar.read_text())
    assert meta["person"] == "TraderMayne"
    assert meta["platform"] == "x"
    assert meta["published_at"] == "2026-07-24"


def test_posts_from_an_unattributable_handle_are_not_stored(tmp_path):
    posts = [XPost("elonmusk", "1", "u", "2026-07-24", "hi", "")]
    written = x_roster.store_posts(posts, WATCHLIST, root=tmp_path)
    assert written == []
    assert not (tmp_path / "x").exists()


# ── resuming after a gap ────────────────────────────────────────────────────────
#
# launchd runs a missed StartCalendarInterval job *on wake*, and coalesces several missed days
# into one run. So the nightly's real cadence is "whenever the laptop next wakes", and a fixed
# `--from yesterday` would silently lose every day in between.

def test_the_window_resumes_from_the_last_captured_day(tmp_path):
    """Resumes *from* that day, not the day after: a day captured mid-run is partial, and
    store_posts rewrites a day wholesale, so re-fetching it is correct and idempotent."""
    posts = [XPost("Tradermayne", "1", "u", "2026-07-20", "a", "")]
    x_roster.store_posts(posts, WATCHLIST, root=tmp_path)
    start, truncated = x_roster.resume_window(date(2026, 7, 23), root=tmp_path)
    assert start == "2026-07-20"
    assert truncated is False


def test_a_gap_longer_than_the_cap_is_truncated_and_flagged(tmp_path):
    """A day of the digest costs ~$0.25, so a week is worth paying unprompted and a quarter is
    not. Truncation is reported rather than silent — the untruncated days are genuinely lost."""
    posts = [XPost("Tradermayne", "1", "u", "2026-01-01", "a", "")]
    x_roster.store_posts(posts, WATCHLIST, root=tmp_path)
    start, truncated = x_roster.resume_window(date(2026, 7, 23), root=tmp_path)
    assert start == "2026-07-16"   # 7 days back, not January
    assert truncated is True


def test_the_first_ever_run_just_takes_yesterday(tmp_path):
    start, truncated = x_roster.resume_window(date(2026, 7, 23), root=tmp_path)
    assert start == "2026-07-22"
    assert truncated is False


def test_the_last_ingested_day_is_the_newest_across_all_authors(tmp_path):
    x_roster.store_posts([
        XPost("Tradermayne", "1", "u", "2026-07-20", "a", ""),
        XPost("GiganticRebirth", "2", "u", "2026-07-22", "b", ""),
    ], WATCHLIST, root=tmp_path)
    assert x_roster.last_ingested_day(root=tmp_path) == "2026-07-22"


def test_restoring_the_same_day_replaces_rather_than_duplicates(tmp_path):
    """A day is re-ingestable: running twice on an overlapping window must not create a second
    document, and a late post added to a day must land in that day's file."""
    first = [XPost("Tradermayne", "1", "https://x.com/Tradermayne/status/1",
                   "2026-07-24", "one", "")]
    both = first + [XPost("Tradermayne", "2", "https://x.com/Tradermayne/status/2",
                          "2026-07-24", "two", "")]
    x_roster.store_posts(first, WATCHLIST, root=tmp_path)
    x_roster.store_posts(both, WATCHLIST, root=tmp_path)
    docs = list((tmp_path / "x").glob("*.txt"))
    assert len(docs) == 1
    assert "two" in docs[0].read_text()
