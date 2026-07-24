import pytest
from ingestion.youtube import extract_video_id


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
])
def test_extract_video_id(url, expected):
    assert extract_video_id(url) == expected


def test_extract_video_id_rejects_non_youtube():
    with pytest.raises(ValueError):
        extract_video_id("https://example.com/nope")


@pytest.mark.integration
def test_fetch_real_transcript_smoke():
    from ingestion.youtube import fetch_transcript
    # A long-standing, reliably-captioned talk. Swap if it ever goes private.
    text = fetch_transcript("dQw4w9WgXcQ")
    assert len(text) > 100


def test_ingest_video_persists_given_text_and_metadata(tmp_path):
    from ingestion.youtube import ingest_video
    from ingestion.store import load, path_for
    import json

    meta = {"title": "T", "published_at": "2026-07-01", "person": "Someone"}
    vid = ingest_video("abc12345678", meta, text="hello", root=tmp_path)

    assert vid == "abc12345678"
    assert load("youtube", "abc12345678", root=tmp_path) == "hello"
    sidecar = json.loads(path_for("youtube", "abc12345678", root=tmp_path)
                         .with_suffix(".json").read_text())
    assert sidecar["title"] == "T"
    assert sidecar["published_at"] == "2026-07-01"
    assert sidecar["person"] == "Someone"
    assert sidecar["platform"] == "youtube"      # added by store.save
    assert sidecar["source_id"] == "abc12345678"  # added by store.save


def test_ingest_video_fetches_transcript_when_text_omitted(tmp_path, monkeypatch):
    import ingestion.youtube as yt
    from ingestion.store import load

    monkeypatch.setattr(yt, "fetch_transcript", lambda vid: f"fetched:{vid}")
    yt.ingest_video("vid00000001", {"url": "u"}, root=tmp_path)

    assert load("youtube", "vid00000001", root=tmp_path) == "fetched:vid00000001"


def _fake_meta(video_id):
    from ingestion.channel import VideoMeta
    return VideoMeta(video_id=video_id, title="Gold Below 4000", published_at="2026-05-02",
                     duration=900, channel_id="UCxyz", was_live=False)


def test_ingest_url_round_trip(tmp_path, monkeypatch):
    import ingestion.youtube as yt
    from ingestion.store import load, path_for
    import json

    monkeypatch.setattr(yt, "fetch_transcript", lambda vid: f"fetched:{vid}")
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    vid = yt.ingest(url, root=tmp_path, _hydrate=_fake_meta)

    assert vid == "dQw4w9WgXcQ"
    assert load("youtube", "dQw4w9WgXcQ", root=tmp_path) == "fetched:dQw4w9WgXcQ"
    sidecar = json.loads(path_for("youtube", "dQw4w9WgXcQ", root=tmp_path)
                         .with_suffix(".json").read_text())
    assert sidecar["url"] == url
    assert sidecar["platform"] == "youtube"
    assert sidecar["source_id"] == "dQw4w9WgXcQ"


def test_ingest_url_hydrates_metadata(tmp_path, monkeypatch):
    """Regression: ingest() used to persist bare {"url": url}, producing a record with no
    published_at. That later crashed the triage ranker and left the thesis person 'unknown'."""
    import ingestion.youtube as yt
    from ingestion.store import path_for
    import json

    monkeypatch.setattr(yt, "fetch_transcript", lambda vid: f"fetched:{vid}")
    yt.ingest("https://www.youtube.com/watch?v=dQw4w9WgXcQ", person="Heavy Metal Verse",
              root=tmp_path, _hydrate=_fake_meta)

    sidecar = json.loads(path_for("youtube", "dQw4w9WgXcQ", root=tmp_path)
                         .with_suffix(".json").read_text())
    assert sidecar["published_at"] == "2026-05-02"
    assert sidecar["title"] == "Gold Below 4000"
    assert sidecar["channel_id"] == "UCxyz"
    assert sidecar["was_live"] is False
    assert sidecar["person"] == "Heavy Metal Verse"


def test_ingest_url_defaults_person_when_unspecified(tmp_path, monkeypatch):
    import ingestion.youtube as yt
    from ingestion.store import path_for
    import json

    monkeypatch.setattr(yt, "fetch_transcript", lambda vid: f"fetched:{vid}")
    yt.ingest("https://www.youtube.com/watch?v=dQw4w9WgXcQ", root=tmp_path, _hydrate=_fake_meta)

    sidecar = json.loads(path_for("youtube", "dQw4w9WgXcQ", root=tmp_path)
                         .with_suffix(".json").read_text())
    assert sidecar["person"] == "ad-hoc"  # matches ingest-channel's --person default


def test_ingest_url_fails_loudly_when_hydration_fails(tmp_path, monkeypatch):
    """A stub record is worse than no record — it silently poisons downstream ranking."""
    import ingestion.youtube as yt
    from ingestion.store import path_for

    monkeypatch.setattr(yt, "fetch_transcript", lambda vid: f"fetched:{vid}")

    def _boom(video_id):
        raise RuntimeError("yt-dlp exploded")

    with pytest.raises(RuntimeError):
        yt.ingest("https://www.youtube.com/watch?v=dQw4w9WgXcQ", root=tmp_path, _hydrate=_boom)

    assert not path_for("youtube", "dQw4w9WgXcQ", root=tmp_path).exists()


def test_fetch_transcript_translates_request_blocked(monkeypatch):
    import ingestion.youtube as yt
    from ingestion.youtube import TranscriptBlocked
    from youtube_transcript_api import RequestBlocked

    class _BlockedApi:
        def fetch(self, vid):
            raise RequestBlocked(vid)

    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    monkeypatch.setattr(yt, "YouTubeTranscriptApi", lambda *a, **k: _BlockedApi())
    with pytest.raises(TranscriptBlocked):
        yt.fetch_transcript("vid00000001")


def test_timeout_session_injects_default_timeout(monkeypatch):
    import ingestion.youtube as yt
    import requests

    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")

        class _Resp:
            status_code = 200

        return _Resp()

    monkeypatch.setattr(requests.Session, "request", fake_request)
    yt._TimeoutSession().get("http://example.com")
    assert captured["timeout"] == yt._HTTP_TIMEOUT


def test_proxy_config_none_without_env(monkeypatch):
    import ingestion.youtube as yt
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    assert yt._proxy_config() is None


def test_proxy_config_built_from_env(monkeypatch):
    import ingestion.youtube as yt
    from youtube_transcript_api.proxies import WebshareProxyConfig
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "u123")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "p456")
    cfg = yt._proxy_config()
    assert isinstance(cfg, WebshareProxyConfig)
    assert cfg.proxy_username == "u123"
    assert cfg.proxy_password == "p456"


def test_fetch_transcript_retries_transient_then_succeeds(monkeypatch):
    import ingestion.youtube as yt
    import requests
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    calls = {"n": 0}

    class _Snip:
        text = "recovered"

    class _FlakyApi:
        def fetch(self, vid):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.exceptions.ChunkedEncodingError("dropped")
            return [_Snip()]

    monkeypatch.setattr(yt, "_build_api", lambda: _FlakyApi())
    text = yt.fetch_transcript("vid00000001", sleep=lambda _s: None)
    assert text == "recovered"
    assert calls["n"] == 3  # failed twice, succeeded on the third


def test_fetch_transcript_gives_up_after_retries(monkeypatch):
    import ingestion.youtube as yt
    import requests
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)

    class _DeadApi:
        def fetch(self, vid):
            raise requests.exceptions.ConnectionError("nope")

    monkeypatch.setattr(yt, "_build_api", lambda: _DeadApi())
    with pytest.raises(requests.exceptions.RequestException):
        yt.fetch_transcript("v", retries=3, sleep=lambda _s: None)


def test_fetch_transcript_proxied_block_retries_without_abort(monkeypatch):
    # With a proxy (rotating IPs), a RequestBlocked on one IP must NOT become a
    # run-aborting TranscriptBlocked — retry a fresh IP, then fail that video only.
    import ingestion.youtube as yt
    from youtube_transcript_api import RequestBlocked
    from ingestion.youtube import TranscriptBlocked
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "u")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "p")

    class _BlockedApi:
        def fetch(self, vid):
            raise RequestBlocked(vid)

    monkeypatch.setattr(yt, "_build_api", lambda: _BlockedApi())
    with pytest.raises(RequestBlocked):          # bubbles as a plain per-video failure
        yt.fetch_transcript("v", retries=2, sleep=lambda _s: None)
    # and specifically NOT the abort signal:
    try:
        yt.fetch_transcript("v", retries=2, sleep=lambda _s: None)
    except TranscriptBlocked:
        raise AssertionError("proxied block should not raise TranscriptBlocked")
    except RequestBlocked:
        pass
