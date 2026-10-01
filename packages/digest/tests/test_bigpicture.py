"""The batched big-picture call: what the model sees, the number-grounding check, and failure."""
from __future__ import annotations

import json

import pytest
from core.stance import Provenance, Stance
from core.thesis import Source
from digest import bigpicture, narrate


class _Block:
    type = "tool_use"

    def __init__(self, data):
        self.input = data


class _Message:
    def __init__(self, data):
        self.content = [_Block(data)]


class _Client:
    def __init__(self, reply=None, error=None):
        self.messages = self
        self.calls = []
        self._reply = reply
        self._error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return _Message(self._reply)


def _stance(asset="BTC", lean="bullish", rationale="weekly MSB held", watching=None,
            conviction="high", person="Mayne"):
    return Stance(
        id=f"v#{asset}", asset=asset, lean=lean, rationale=rationale, conviction=conviction,
        horizon="swing", watching=watching,
        source=Source(person=person, platform="youtube", url="u", published_at="2026-09-30",
                      transcript_ref="youtube/v"),
        extraction=Provenance(model="m", extracted_at="2026-09-30T00:00:00Z"),
    )


PAYLOAD = bigpicture.payload({
    "Mayne": [_stance(watching="monthly close above 82,811")],
    "Cowen": [_stance(asset="ETH", rationale="supply drying up", person="Cowen")],
})


def _reply(*pairs):
    return {"lines": [{"person": p, "line": line} for p, line in pairs]}


# ── the boundary ──────────────────────────────────────────────────────────────

def test_payload_carries_stance_fields_and_nothing_else():
    entry = bigpicture.payload({"Mayne": [_stance()]})[0]
    assert entry["person"] == "Mayne"
    assert set(entry["stances"][0]) == {
        "asset", "lean", "conviction", "horizon", "rationale", "watching", "published_at"}


def test_payload_omits_persons_without_stances():
    assert bigpicture.payload({"Mayne": [], "Cowen": [_stance()]}) == bigpicture.payload(
        {"Cowen": [_stance()]})


def test_prompt_forbids_adding_levels():
    system, user = bigpicture.build_prompt(PAYLOAD)
    assert "Never add a price" in system
    assert json.loads(user) == PAYLOAD


# ── summarize ─────────────────────────────────────────────────────────────────

def test_returns_a_line_per_person():
    client = _Client(_reply(("Mayne", " Bullish on BTC. "), ("Cowen", "Watching ETH supply.")))
    assert bigpicture.summarize(PAYLOAD, client=client) == {
        "Mayne": "Bullish on BTC.", "Cowen": "Watching ETH supply."}
    assert len(client.calls) == 1


def test_an_empty_payload_never_calls_the_model():
    client = _Client(_reply(("Mayne", "x")))
    assert bigpicture.summarize([], client=client) == {}
    assert client.calls == []


def test_invented_persons_are_dropped_and_missing_ones_get_no_line():
    client = _Client(_reply(("Mayne", "Bullish."), ("Nobody", "Made up.")))
    assert bigpicture.summarize(PAYLOAD, client=client) == {"Mayne": "Bullish."}


def test_blank_lines_are_dropped():
    client = _Client(_reply(("Mayne", "Bullish."), ("Cowen", "  ")))
    assert bigpicture.summarize(PAYLOAD, client=client) == {"Mayne": "Bullish."}


# ── failure ───────────────────────────────────────────────────────────────────

def test_a_dead_call_raises():
    with pytest.raises(bigpicture.BigPictureFailed):
        bigpicture.summarize(PAYLOAD, client=_Client(error=OSError("no claude")), retries=1)


def test_an_empty_answer_raises():
    with pytest.raises(bigpicture.BigPictureFailed):
        bigpicture.summarize(PAYLOAD, client=_Client(_reply()), retries=1)


def test_it_retries_with_backoff(monkeypatch):
    slept: list = []
    monkeypatch.setattr(narrate.time, "sleep", slept.append)
    client = _Client(error=OSError("flaky"))
    with pytest.raises(bigpicture.BigPictureFailed):
        bigpicture.summarize(PAYLOAD, client=client, retries=3)
    assert len(client.calls) == 3
    assert slept and slept == sorted(slept)


def test_a_bug_in_our_own_code_is_not_retried():
    client = _Client(error=AttributeError("shape change"))
    with pytest.raises(AttributeError):
        bigpicture.summarize(PAYLOAD, client=client, retries=3)
    assert len(client.calls) == 1


# ── grounding ─────────────────────────────────────────────────────────────────

def test_a_line_with_an_invented_level_is_dropped_and_warned():
    kept, warnings = bigpicture.grounded(
        {"Mayne": "Bullish BTC, eyeing 95,000.", "Cowen": "Watching ETH supply."}, PAYLOAD)
    assert kept == {"Cowen": "Watching ETH supply."}
    assert len(warnings) == 1 and "Mayne" in warnings[0] and "95000" in warnings[0]


def test_a_level_from_the_persons_own_payload_is_kept_with_commas_stripped():
    line = "Bullish BTC; monthly close above 82811 is the tell."
    kept, warnings = bigpicture.grounded({"Mayne": line}, PAYLOAD)
    assert kept == {"Mayne": line} and warnings == []


def test_a_number_from_another_persons_payload_does_not_ground():
    kept, _ = bigpicture.grounded({"Cowen": "Watching 82,811."}, PAYLOAD)
    assert kept == {}


def test_lines_without_numbers_pass():
    kept, warnings = bigpicture.grounded({"Mayne": "Risk-on while yields ease."}, PAYLOAD)
    assert kept and warnings == []
