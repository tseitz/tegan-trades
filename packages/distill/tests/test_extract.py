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
    assert out[0].id.startswith("youtube/vid00000001#")  # content-addressed, not positional
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


# ── one bad thesis must not discard the whole document ──────────────────────────
#
# Hit twice in two days on live data — Capital Flows `udGgR-6lyCQ` and krillin
# `x/LSDinmycoffee-2026-07-24`, both `trade.invalidation = None`. Whole-payload validation meant
# one bad call in a list of N threw away all N, and the call was paid for regardless. It gets
# worse on X specifically: a chart post with drawn levels and no stated stop is the normal case
# there, so a missing invalidation is common rather than exceptional.

MIXED_INPUT = {"theses": [
    # a trade with no invalidation — invalid, and the exact shape seen in production
    {"thesis_type": "trade", "domain": "crypto", "asset": "SOL", "direction": "long",
     "timeframe": "swing", "conviction": "high", "summary": "no invalidation",
     "confidence": 0.9},
    {"thesis_type": "macro_lean", "domain": "crypto", "asset": "BTC", "direction": "long",
     "timeframe": "macro", "conviction": "med", "summary": "Bullish BTC", "confidence": 0.6},
    {"thesis_type": "macro_lean", "domain": "crypto", "asset": "ETH", "direction": "short",
     "timeframe": "swing", "conviction": "low", "summary": "ETH soft", "confidence": 0.4},
]}


def test_one_invalid_thesis_does_not_discard_the_valid_ones():
    client = _FakeClient(MIXED_INPUT)
    out = extract_theses("body", SOURCE, client=client, extracted_at="t")
    assert [t.asset for t in out] == ["BTC", "ETH"]
    assert client.calls == 1  # no retry: the response was usable


def test_a_dropped_thesis_is_reported_rather_than_silently_swallowed():
    """`core.setups` keeps its NotASetup tally for the same reason: a document that quietly
    lost half its theses and one that genuinely had two are indistinguishable otherwise."""
    dropped = []
    client = _FakeClient(MIXED_INPUT)
    extract_theses("body", SOURCE, client=client, extracted_at="t", on_drop=dropped.append)
    assert len(dropped) == 1
    assert "invalidation" in dropped[0]


def test_a_response_where_every_thesis_is_invalid_still_retries():
    """All-invalid is a different failure from one-bad-row — it points at the prompt or the
    schema rather than at one awkward call, so it is worth the retry it always got."""
    client = _FakeClient(INVALID_INPUT, VALID_INPUT)
    out = extract_theses("body", SOURCE, client=client, extracted_at="t")
    assert client.calls == 2
    assert [t.asset for t in out] == ["BTC"]


def test_a_genuinely_empty_extraction_is_not_retried():
    """Nothing offered is a real answer — methodology videos and banter days legitimately
    contain no calls. Retrying would double the cost of every quiet document."""
    client = _FakeClient({"theses": []})
    assert extract_theses("body", SOURCE, client=client, extracted_at="t") == []
    assert client.calls == 1


def test_a_structurally_broken_payload_still_retries():
    client = _FakeClient({"nonsense": True}, VALID_INPUT)
    out = extract_theses("body", SOURCE, client=client, extracted_at="t")
    assert client.calls == 2
    assert len(out) == 1
