from datetime import date

import pytest
from ingestion.channel import channel_base_url, tab_url
from ingestion.channel import VideoStub, list_tab, resolve_recent
from ingestion.channel import VideoMeta, hydrate, is_recent_enough


@pytest.mark.parametrize("channel,expected", [
    ("@benjaminjcowen", "https://www.youtube.com/@benjaminjcowen"),
    ("benjaminjcowen", "https://www.youtube.com/@benjaminjcowen"),
    ("UCYStZ8mMNGOVTj-Z4AbbSrQ", "https://www.youtube.com/channel/UCYStZ8mMNGOVTj-Z4AbbSrQ"),
    ("https://www.youtube.com/@TTrades_edu", "https://www.youtube.com/@TTrades_edu"),
    ("https://www.youtube.com/@TTrades_edu/", "https://www.youtube.com/@TTrades_edu"),
])
def test_channel_base_url(channel, expected):
    assert channel_base_url(channel) == expected


def test_tab_url_appends_tab():
    assert tab_url("@TTrades_edu", "streams") == "https://www.youtube.com/@TTrades_edu/streams"
    assert tab_url("UCYStZ8mMNGOVTj-Z4AbbSrQ", "videos") == \
        "https://www.youtube.com/channel/UCYStZ8mMNGOVTj-Z4AbbSrQ/videos"


def _fake_entries(mapping):
    """Return a fake flat-extract callable keyed by tab url."""
    def _entries(url, limit):
        for tab, rows in mapping.items():
            if url.endswith(f"/{tab}"):
                return rows[:limit]
        raise RuntimeError(f"no such tab: {url}")
    return _entries


def test_list_tab_builds_stubs_from_flat_entries():
    entries = _fake_entries({"videos": [
        {"id": "aaaaaaaaaaa", "title": "One", "live_status": None},
        {"id": "bbbbbbbbbbb", "title": "Two", "live_status": None},
    ]})
    stubs = list_tab("@x", "videos", 10, _entries=entries)
    assert stubs == [
        VideoStub("aaaaaaaaaaa", "One", "videos"),
        VideoStub("bbbbbbbbbbb", "Two", "videos"),
    ]


def test_list_tab_skips_entries_without_id():
    entries = _fake_entries({"videos": [
        {"id": None, "title": "bad"},
        {"id": "ccccccccccc", "title": "good"},
    ]})
    stubs = list_tab("@x", "videos", 10, _entries=entries)
    assert [s.video_id for s in stubs] == ["ccccccccccc"]


def test_resolve_recent_merges_tabs_and_dedupes():
    entries = _fake_entries({
        "videos": [{"id": "vid00000001", "title": "V1"},
                   {"id": "dup00000000", "title": "Dup"}],
        "streams": [{"id": "dup00000000", "title": "Dup"},
                    {"id": "str00000001", "title": "S1"}],
    })
    stubs = resolve_recent("@x", max_videos=10, _list_entries=entries)
    ids = [s.video_id for s in stubs]
    assert ids == ["vid00000001", "dup00000000", "str00000001"]  # videos first, dup kept once
    assert any(s.tab == "streams" and s.video_id == "str00000001" for s in stubs)


def test_resolve_recent_tolerates_missing_streams_tab():
    def entries(url, limit):
        if url.endswith("/videos"):
            return [{"id": "vid00000001", "title": "V1"}]
        raise RuntimeError("This channel has no streams tab")
    stubs = resolve_recent("@x", max_videos=10, _list_entries=entries)
    assert [s.video_id for s in stubs] == ["vid00000001"]


def test_resolve_recent_returns_empty_and_surfaces_failure_when_both_tabs_error(capsys):
    def entries(url, limit):
        raise RuntimeError("boom")

    stubs = resolve_recent("@x", max_videos=10, _list_entries=entries)

    assert stubs == []
    err = capsys.readouterr().err
    assert "@x/videos" in err
    assert "@x/streams" in err
    assert "boom" in err


def test_hydrate_maps_upload_date_to_iso_published_at():
    info = {"title": "T", "upload_date": "20260701", "duration": 1220,
            "channel_id": "UCabc", "was_live": True}
    meta = hydrate("vid00000001", _extract=lambda url: info)
    assert meta == VideoMeta("vid00000001", "T", "2026-07-01", 1220, "UCabc", True)


def test_hydrate_falls_back_to_timestamp_when_no_upload_date():
    # 2026-07-01T00:00:00Z == 1782864000
    info = {"title": "T", "timestamp": 1782864000, "was_live": False}
    meta = hydrate("vid00000002", _extract=lambda url: info)
    assert meta.published_at == "2026-07-01"
    assert meta.was_live is False


def test_hydrate_published_at_none_when_no_date_fields():
    meta = hydrate("vid00000003", _extract=lambda url: {"title": "T"})
    assert meta.published_at is None


def test_is_recent_enough_uses_cutoff():
    today = date(2026, 7, 22)
    assert is_recent_enough("2026-07-01", max_age_days=730, today=today) is True
    assert is_recent_enough("2020-01-01", max_age_days=730, today=today) is False


def test_is_recent_enough_false_for_missing_date():
    assert is_recent_enough(None, max_age_days=730, today=date(2026, 7, 22)) is False
