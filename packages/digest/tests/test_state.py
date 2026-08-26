"""What the digest remembers between nights.

The bias under test is the one that matters: every failure here must degrade toward saying a
thing TWICE, never toward dropping it. A repeat is visible and annoying; a silent drop is
indistinguishable from a quiet roster, which is the failure this package keeps re-learning.
"""
from __future__ import annotations

import json

import pytest
from digest import state


def test_a_missing_file_is_an_empty_memory_and_not_a_problem(tmp_path):
    """The normal state of a fresh clone. Warning about it would put a `!!` on the subject
    line of every first run."""
    said = []
    assert state.load(tmp_path / "nope.json", warn=said.append) == {}
    assert said == []


def test_an_unreadable_file_is_an_empty_memory_and_says_so(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    said = []
    assert state.load(path, warn=said.append) == {}
    assert said and "already reported may appear again" in said[0]


def test_a_file_that_is_not_an_object_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('["a list"]', encoding="utf-8")
    assert state.load(path) == {}


def test_what_is_written_is_what_comes_back(tmp_path):
    path = tmp_path / "sub" / "state.json"
    assert state.save(path, {state.ROSTER: {"ETH|voice|Pierre": "2026-08-21"}})
    assert state.roster_seen(state.load(path)) == {"ETH|voice|Pierre": "2026-08-21"}


def test_an_unwritable_path_warns_rather_than_raising(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    said = []
    assert state.save(blocker / "state.json", {}, warn=said.append) is False
    assert said and "reported again tomorrow" in said[0]


def test_a_payload_json_cannot_serialise_warns_rather_than_raising(tmp_path):
    """The memory is built from values this package computed, so this should be unreachable —
    but it is attached to a digest that must go out, and a `TypeError` here would take the
    whole email down after it had already been printed."""
    said = []
    assert state.save(tmp_path / "state.json", {"bad": {object()}}, warn=said.append) is False
    assert said


def test_roster_memory_survives_a_corrupt_neighbour(tmp_path):
    """Sections are independent. A garbage `xai_reported` must not empty the roster memory and
    make the whole section repeat."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({state.ROSTER: {"ETH|voice|Pierre": "2026-08-21"},
                                state.XAI: "not a number"}), encoding="utf-8")
    loaded = state.load(path)
    assert state.roster_seen(loaded) == {"ETH|voice|Pierre": "2026-08-21"}
    assert state.xai_changed(loaded, 21.41) is True


def test_a_roster_section_of_the_wrong_shape_is_an_empty_memory():
    assert state.roster_seen({state.ROSTER: ["not", "a", "map"]}) == {}


# ── the spend line only speaks when the number moved ──────────────────────────

@pytest.mark.parametrize("recorded, now, expected", [
    (None, 21.41, True),          # never reported — a first run should say where it stands
    (21.41, 21.41, False),        # frozen, because ingest-x is off. Not news.
    (21.41, 21.42, True),         # spent while over the cap. That IS news.
    (21.41, 0.0, True),           # the month rolled over
    ("garbage", 21.41, True),     # unreadable record falls back to speaking up
])
def test_the_spend_line_speaks_only_on_a_change(recorded, now, expected):
    stored = {} if recorded is None else {state.XAI: recorded}
    assert state.xai_changed(stored, now) is expected


def test_an_unknown_spend_total_is_not_a_change():
    """`None` means the reader was never given a number, not that it held still."""
    assert state.xai_changed({state.XAI: 21.41}, None) is False
