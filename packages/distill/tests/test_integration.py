import os
import pytest

from core.thesis import Source, Thesis
from distill.extract import extract_theses

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"),
                    reason="needs ANTHROPIC_API_KEY")
def test_real_extraction_smoke():
    source = Source(person="Test", platform="youtube",
                    url="https://youtu.be/x", published_at="2025-02-28",
                    transcript_ref="youtube/x")
    text = ("I think Bitcoin is a long here. I'm bullish into year-end and expect "
            "a new all-time high. If we lose the 90k level though, I'm wrong and "
            "would close it. Ethereum I'm just watching for now.")
    theses = extract_theses(text, source)
    assert isinstance(theses, list)
    for t in theses:
        assert isinstance(t, Thesis)
        assert t.asset
        assert t.id.startswith("youtube/x#")


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"),
                    reason="needs ANTHROPIC_API_KEY")
def test_pure_chatter_yields_empty():
    source = Source(person="Test", platform="youtube", url="u",
                    published_at="2025-02-28", transcript_ref="youtube/y")
    text = "Thanks for watching, smash that like button and check the link below."
    assert extract_theses(text, source) == []
