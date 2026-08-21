"""The narrated section.

The transport is mirrored from ``brain.synthesize``, so what is worth testing here is the
boundary: what the model is allowed to see, and that nothing it does can take the digest down.
"""
from __future__ import annotations

import json

import pytest
from digest import narrate


class _Block:
    type = "tool_use"

    def __init__(self, data):
        self.input = data


class _Message:
    def __init__(self, data):
        self.content = [_Block(data)]


class _Client:
    """Records the call and replays a canned answer. ``messages`` points at itself, matching
    ``ClaudeCodeClient``'s duck-typed shape."""

    def __init__(self, reply=None, error=None):
        self.messages = self
        self.calls = []
        self._reply = reply if reply is not None else {"summary": ["ETH went unanimous."]}
        self._error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return _Message(self._reply)


PAYLOAD = [{"asset": "ETH", "before": {"bullish": 1}, "after": {"bullish": 3},
            "flips": [{"person": "Pierre", "was": "bearish", "now": "bullish"}],
            "new_voices": ["krillin"]}]


# ── the boundary ──────────────────────────────────────────────────────────────

def test_the_model_sees_counts_and_names_and_nothing_else():
    """The guarantee that makes a generated line acceptable in a digest at all: no transcript
    text goes in, so no claim can come out that was not computed here first."""
    _, user = narrate.build_prompt(PAYLOAD)
    sent = json.loads(user)
    assert sent == PAYLOAD
    assert "rationale" not in user and "transcript" not in user


def test_the_prompt_forbids_inventing_a_reason():
    """The payload says who flipped, never why. A model asked to explain a flip would supply a
    plausible reason it was never given, and that reads as a finding."""
    system, _ = narrate.build_prompt(PAYLOAD)
    assert "never add" in system.lower() or "must not guess" in system.lower()


# ── output shaping ────────────────────────────────────────────────────────────

def test_lines_come_back_indented_for_the_section():
    out = narrate.narrate(PAYLOAD, client=_Client())
    assert out == "  ETH went unanimous."


def test_blank_lines_from_the_model_are_dropped():
    client = _Client(reply={"summary": ["ETH moved.", "  ", "", "BTC moved."]})
    assert narrate.narrate(PAYLOAD, client=client) == "  ETH moved.\n  BTC moved."


def test_an_empty_payload_never_calls_the_model():
    """Nothing moved. Spending a call to be told so is the section's whole cost for no value."""
    client = _Client()
    assert narrate.narrate([], client=client) == ""
    assert client.calls == []


# ── failure ───────────────────────────────────────────────────────────────────

def test_a_dead_call_raises_rather_than_returning_empty():
    """"The model had nothing to say" and "the call broke" are different facts about the night.
    Collapsing them into an empty string would hide a broken narrator indefinitely."""
    with pytest.raises(narrate.NarrationFailed):
        narrate.narrate(PAYLOAD, client=_Client(error=OSError("claude not found")), retries=1)


def test_an_empty_answer_raises():
    with pytest.raises(narrate.NarrationFailed):
        narrate.narrate(PAYLOAD, client=_Client(reply={"summary": []}), retries=1)


def test_it_retries_before_giving_up():
    client = _Client(error=OSError("flaky"))
    with pytest.raises(narrate.NarrationFailed):
        narrate.narrate(PAYLOAD, client=client, retries=2)
    assert len(client.calls) == 2


# ── what may and may not be retried ──────────────────────────────────────────

def test_a_bug_in_our_own_code_is_not_retried():
    """Retrying an ``AttributeError`` from an SDK shape change burns a second ~110s subprocess
    boot to fail identically, then collapses the cause to a string — so a permanent break reads
    as a quiet roster, every night, forever."""
    client = _Client(error=AttributeError("'Message' object has no attribute 'content'"))
    with pytest.raises(AttributeError):
        narrate.narrate(PAYLOAD, client=client, retries=3)
    assert len(client.calls) == 1


def test_a_transport_error_is_still_retried():
    client = _Client(error=OSError("connection reset"))
    with pytest.raises(narrate.NarrationFailed):
        narrate.narrate(PAYLOAD, client=client, retries=3)
    assert len(client.calls) == 3


def test_retries_back_off_rather_than_hammering(monkeypatch):
    """Two immediate attempts is the worst possible shape against a rate limit — the second
    lands inside the same window as the first."""
    slept: list = []
    monkeypatch.setattr(narrate.time, "sleep", slept.append)
    with pytest.raises(narrate.NarrationFailed):
        narrate.narrate(PAYLOAD, client=_Client(error=OSError("flaky")), retries=3)
    assert slept and all(s > 0 for s in slept)
    assert slept == sorted(slept), "backoff should not shrink"
