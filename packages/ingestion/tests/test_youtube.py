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
