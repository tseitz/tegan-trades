import pytest

from brain.extract import ExtractionFailed, extract_stances
from core.thesis import Source

SOURCE = Source(
    person="Benjamin Cowen",
    platform="youtube",
    url="https://youtu.be/abc123",
    published_at="2026-07-01",
    transcript_ref="youtube/abc123",
)

GOOD = {"asset": "ETH", "lean": "bullish", "rationale": "supply is drying up"}
BAD = {"asset": "BTC", "lean": "moon", "rationale": "not a real lean"}


class _ToolBlock:
    type = "tool_use"

    def __init__(self, data):
        self.input = data


class _Message:
    def __init__(self, data):
        self.content = [_ToolBlock(data)]


class _FakeClient:
    """Duck-typed like ClaudeCodeClient: exposes .messages.create(...)."""

    def __init__(self, *responses):
        self.messages = self
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        item = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return _Message(item)


def _extract(client, **over):
    kw = {"client": client, "model": "claude-sonnet-5",
          "extracted_at": "2026-07-24T12:00:00+00:00", **over}
    return extract_stances("transcript text", SOURCE, **kw)


# ── happy path ──────────────────────────────────────────────────────────────

def test_returns_built_stances_with_ids_and_provenance():
    client = _FakeClient({"stances": [GOOD]})
    result = _extract(client)
    assert len(result.stances) == 1
    s = result.stances[0]
    assert s.asset == "ETH"
    assert s.id.startswith("youtube/abc123#")
    assert s.source == SOURCE
    assert s.extraction.model == "claude-sonnet-5"
    assert s.extraction.extracted_at == "2026-07-24T12:00:00+00:00"


def test_an_empty_extraction_is_a_success_not_a_failure():
    """Most transcripts legitimately contain no stance on any tracked asset."""
    result = _extract(_FakeClient({"stances": []}))
    assert result.stances == []
    assert result.dropped == []


# ── per-item validation: the load-bearing behaviour ─────────────────────────

def test_a_malformed_stance_is_dropped_without_losing_its_siblings():
    result = _extract(_FakeClient({"stances": [GOOD, BAD]}))
    assert [s.asset for s in result.stances] == ["ETH"]
    assert len(result.dropped) == 1
    assert result.dropped[0].raw == BAD


def test_drops_do_not_trigger_a_retry():
    """A dropped item is an expected outcome, not a failure. Retrying would burn
    tokens re-reading a whole transcript to recover one bad stance."""
    client = _FakeClient({"stances": [GOOD, BAD]})
    _extract(client)
    assert client.calls == 1


def test_every_item_malformed_still_returns_rather_than_raising():
    result = _extract(_FakeClient({"stances": [BAD, BAD]}))
    assert result.stances == []
    assert len(result.dropped) == 2


# ── retries: only on call/shape failure ─────────────────────────────────────

def test_retries_a_transient_client_error_then_succeeds():
    client = _FakeClient(RuntimeError("transient"), {"stances": [GOOD]})
    result = _extract(client)
    assert len(result.stances) == 1
    assert client.calls == 2


def test_raises_after_exhausting_retries():
    client = _FakeClient(RuntimeError("always down"))
    with pytest.raises(ExtractionFailed):
        _extract(client, retries=2)
    assert client.calls == 2


def test_a_response_with_no_tool_use_block_is_retried_then_raises():
    class _NoToolClient:
        def __init__(self):
            self.messages = self
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            msg = _Message({})
            msg.content = []
            return msg

    client = _NoToolClient()
    with pytest.raises(ExtractionFailed):
        _extract(client, retries=2)
    assert client.calls == 2


# ── wiring ──────────────────────────────────────────────────────────────────

def test_passes_the_stance_schema_and_prompt_to_the_client():
    client = _FakeClient({"stances": [GOOD]})
    _extract(client)
    kwargs = client.last_kwargs
    assert kwargs["model"] == "claude-sonnet-5"
    assert "stance" in kwargs["system"].lower()
    assert "transcript text" in kwargs["messages"][0]["content"]


def test_extracted_at_defaults_to_now_when_not_supplied():
    client = _FakeClient({"stances": [GOOD]})
    result = extract_stances("t", SOURCE, client=client)
    assert result.stances[0].extraction.extracted_at  # non-empty ISO stamp
