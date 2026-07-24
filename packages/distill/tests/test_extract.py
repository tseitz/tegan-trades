import pytest
from core.thesis import Source, Thesis
from distill.extract import extract_theses, ExtractionFailed

SOURCE = Source(person="P", platform="youtube", url="u",
                published_at="2025-01-01", transcript_ref="youtube/vid00000001")

VALID_INPUT = {"theses": [{
    "thesis_type": "macro_lean", "domain": "crypto", "asset": "BTC",
    "direction": "long", "timeframe": "macro", "conviction": "med",
    "summary": "Bullish BTC", "confidence": 0.6,
}]}
INVALID_INPUT = {"theses": [{"thesis_type": "trade", "domain": "crypto",
    "asset": "ETH", "direction": "long", "timeframe": "swing",
    "conviction": "high", "summary": "no invalidation", "confidence": 0.9}]}  # trade w/o invalidation


class _ToolBlock:
    type = "tool_use"
    def __init__(self, data): self.input = data


class _Msg:
    def __init__(self, data): self.content = [_ToolBlock(data)]


class _FakeClient:
    """Returns queued tool inputs, one per .messages.create call."""
    def __init__(self, *inputs):
        self._queue = list(inputs)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        return _Msg(self._queue.pop(0))


HEARD_INPUT = {"theses": [{
    "thesis_type": "macro_lean", "domain": "crypto", "asset": "ETH",
    "asset_heard": "ethereal", "direction": "long", "timeframe": "swing",
    "conviction": "med", "summary": "ETH bid", "confidence": 0.55,
}]}


def test_extract_passes_through_asset_heard():
    client = _FakeClient(HEARD_INPUT)
    out = extract_theses("body", SOURCE, client=client, extracted_at="t")
    assert out[0].asset == "ETH"
    assert out[0].asset_heard == "ethereal"


def test_extract_returns_enriched_theses():
    client = _FakeClient(VALID_INPUT)
    out = extract_theses("body", SOURCE, client=client,
                         extracted_at="2026-07-23T00:00:00+00:00")
    assert len(out) == 1
    assert isinstance(out[0], Thesis)
    assert out[0].asset == "BTC"
    assert out[0].id == "youtube/vid00000001#0"
    assert out[0].extraction.model  # model stamped


def test_extract_empty_is_ok():
    client = _FakeClient({"theses": []})
    assert extract_theses("body", SOURCE, client=client, extracted_at="t") == []


def test_extract_retries_once_then_succeeds():
    client = _FakeClient(INVALID_INPUT, VALID_INPUT)
    out = extract_theses("body", SOURCE, client=client, extracted_at="t")
    assert client.calls == 2
    assert len(out) == 1


def test_extract_raises_after_retry_exhausted():
    client = _FakeClient(INVALID_INPUT, INVALID_INPUT)
    with pytest.raises(ExtractionFailed):
        extract_theses("body", SOURCE, client=client, extracted_at="t")
    assert client.calls == 2
