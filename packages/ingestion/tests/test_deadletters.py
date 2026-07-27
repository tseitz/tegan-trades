import json
from datetime import date

import pytest

from ingestion import deadletters
from ingestion.deadletters import (
    NO_CAPTIONS,
    UNAVAILABLE,
    is_dead,
    load,
    record,
    save,
)


def test_an_absent_registry_reads_as_empty(tmp_path):
    assert load(tmp_path / "_dead.json") == {}


def test_recording_marks_a_video_dead_with_its_reason_and_date():
    registry = record("vid1", NO_CAPTIONS, registry={}, today=date(2026, 7, 27))
    assert is_dead("vid1", registry)
    assert registry["vid1"] == {"reason": NO_CAPTIONS, "first_seen": "2026-07-27"}


def test_recording_returns_a_new_registry_rather_than_mutating():
    before = {}
    after = record("vid1", NO_CAPTIONS, registry=before, today=date(2026, 7, 27))
    assert before == {}
    assert after != before


def test_re_recording_keeps_the_original_first_seen():
    """``first_seen`` answers "how long has this been dead". Refreshing it nightly would make
    every entry look like it died today, which is the one thing the field is for."""
    day_one = record("vid1", NO_CAPTIONS, registry={}, today=date(2026, 7, 1))
    day_two = record("vid1", NO_CAPTIONS, registry=day_one, today=date(2026, 7, 27))
    assert day_two["vid1"]["first_seen"] == "2026-07-01"


def test_a_transient_reason_is_refused_rather_than_recorded():
    """The registry is permanent by construction, so the closed reason set is the guardrail.
    An IP block is the case that matters: it is systemic and temporary, it already has its own
    abort path, and recording it here would discard a video forever over a bad night."""
    with pytest.raises(ValueError, match="not a permanent failure"):
        record("vid1", "ip_blocked", registry={}, today=date(2026, 7, 27))


def test_the_registry_round_trips_through_disk(tmp_path):
    path = tmp_path / "_dead.json"
    registry = record("vid2", UNAVAILABLE, registry={}, today=date(2026, 7, 27))
    save(registry, path)
    assert load(path) == registry
    assert json.loads(path.read_text())["vid2"]["reason"] == UNAVAILABLE


def test_a_malformed_registry_raises_rather_than_starting_over(tmp_path):
    """Swallowing this would re-ingest every dead video and hide that the file was corrupt —
    a silent reset dressed as a clean run."""
    path = tmp_path / "_dead.json"
    path.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        load(path)


def test_the_registry_lives_beside_the_transcripts_it_describes():
    """Only meaningful together: a restore bringing back one without the other re-fetches
    everything the registry existed to stop."""
    from ingestion.store import DATA_ROOT
    assert deadletters.DEAD_FILE.parent == DATA_ROOT
