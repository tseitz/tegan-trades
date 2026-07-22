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
