from datetime import UTC, date, datetime, timedelta

import pytest
from ingestion.channel import (
    VideoMeta,
    VideoStub,
    _published_at,
    _with_retry,
    channel_base_url,
    hydrate,
    is_recent_enough,
    list_tab,
    resolve_recent,
    tab_url,
)
from yt_dlp.utils import DownloadError


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


def test_resolve_recent_honours_an_explicit_tab_list():
    entries = _fake_entries({
        "videos": [{"id": "vid00000001", "title": "V1"}],
        "streams": [{"id": "str00000001", "title": "S1"}],
    })
    stubs = resolve_recent("@x", max_videos=10, tabs=("videos",), _list_entries=entries)
    assert [s.video_id for s in stubs] == ["vid00000001"]


def test_resolve_recent_never_reads_a_tab_that_was_not_asked_for():
    """The cap is per tab, so an unwanted tab doubles a run rather than being merely noisy."""
    seen: list[str] = []

    def entries(url, limit):
        seen.append(url)
        return [{"id": "vid00000001", "title": "V1"}]

    resolve_recent("@x", max_videos=10, tabs=("videos",), _list_entries=entries)
    assert seen == ["https://www.youtube.com/@x/videos"]


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


def test_with_retry_recovers_from_a_blocked_proxy_ip(monkeypatch):
    monkeypatch.setattr("ingestion.channel.proxy_url", lambda: "http://proxy:8080")
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) < 3:
            raise DownloadError("Sign in to confirm you're not a bot")
        return "ok"

    result = _with_retry(call, sleep=lambda _: None)
    assert result == "ok"
    assert len(attempts) == 3


def test_with_retry_raises_immediately_with_no_proxy_configured(monkeypatch):
    monkeypatch.setattr("ingestion.channel.proxy_url", lambda: None)
    attempts = []

    def call():
        attempts.append(1)
        raise DownloadError("Sign in to confirm you're not a bot")

    with pytest.raises(DownloadError):
        _with_retry(call, sleep=lambda _: None)
    assert len(attempts) == 1  # a single IP will hit the same block every retry


def test_with_retry_does_not_retry_a_permanent_error(monkeypatch):
    monkeypatch.setattr("ingestion.channel.proxy_url", lambda: "http://proxy:8080")
    attempts = []

    def call():
        attempts.append(1)
        raise DownloadError("This channel does not have a streams tab")

    with pytest.raises(DownloadError):
        _with_retry(call, sleep=lambda _: None)
    assert len(attempts) == 1  # not one of the known transient markers


def test_with_retry_raises_last_error_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("ingestion.channel.proxy_url", lambda: "http://proxy:8080")

    def call():
        raise DownloadError("HTTP Error 429: Too Many Requests")

    with pytest.raises(DownloadError, match="429"):
        _with_retry(call, retries=3, sleep=lambda _: None)


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


def test_is_recent_enough_true_at_exact_cutoff():
    today = date(2026, 7, 22)
    cutoff_date = (today - timedelta(days=730)).isoformat()
    assert is_recent_enough(cutoff_date, max_age_days=730, today=today) is True


def test_published_at_falls_back_when_upload_date_is_not_a_valid_calendar_date():
    # 8 chars, non-numeric segment -> slices to a garbage ISO-shaped string
    info = {"upload_date": "2026AB01", "timestamp": 1782864000}
    assert _published_at(info) == "2026-07-01"


def test_published_at_falls_back_when_upload_date_is_syntactically_numeric_but_invalid():
    # Feb 31 doesn't exist
    info = {"upload_date": "20260231", "timestamp": 1782864000}
    assert _published_at(info) == "2026-07-01"


def test_published_at_none_when_upload_date_invalid_and_no_timestamp():
    info = {"upload_date": "2026AB01"}
    assert _published_at(info) is None


def test_published_at_handles_timestamp_zero_without_falsy_bug():
    # timestamp=0 is falsy but a legitimate (if unrealistic) epoch value
    info = {"timestamp": 0}
    assert _published_at(info) == "1970-01-01"


@pytest.mark.integration
def test_resolve_and_hydrate_real_channel():
    # TTrades posts both uploads and livestreams; small cap to keep it fast.
    stubs = resolve_recent("@TTrades_edu", max_videos=5)
    assert len(stubs) > 0
    assert any(s.tab == "streams" for s in stubs), "expected at least one livestream VOD"

    meta = hydrate(stubs[0].video_id)
    assert meta.published_at is not None
    assert len(meta.published_at) == 10  # YYYY-MM-DD
    assert meta.channel_id and meta.channel_id.startswith("UC")


@pytest.mark.integration
def test_ingest_channel_real_end_to_end(tmp_path):
    from ingestion.roster import ChannelTarget, ingest_channel
    from ingestion.store import load

    target = ChannelTarget("Cowen", "@benjaminjcowen", max_videos=1, max_age_days=3650)
    result = ingest_channel(target, root=tmp_path, today=datetime.now(UTC).date())

    # Either the one video ingested, or it lacked captions/date — but never crashed.
    handled = result.ingested + result.skipped + result.stale + [v for v, _ in result.failed]
    assert len(handled) >= 1
    for vid in result.ingested:
        assert len(load("youtube", vid, root=tmp_path)) > 0
