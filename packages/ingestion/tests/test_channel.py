import pytest
from ingestion.channel import channel_base_url, tab_url
from ingestion.channel import VideoStub, list_tab, resolve_recent


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
