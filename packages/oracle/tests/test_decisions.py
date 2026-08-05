"""The decisions sidecar and its vault mirror.

These cover the one file in ``data/`` that is not regenerable ore: hand-entered judgement,
which nothing in the pipeline can reconstruct. See ``docs/IMPROVEMENTS.md`` §4b.
"""
from __future__ import annotations

import json

import pytest
from oracle import decisions


def _write(path, *records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _rec(key: str, decision: str = "rejected") -> dict:
    return {"candidate_key": key, "decision": decision}


def _keys(path) -> list[str]:
    return [json.loads(ln)["candidate_key"]
            for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── append ────────────────────────────────────────────────────────────────────

def test_append_writes_both_copies(tmp_path):
    primary, mirror = tmp_path / "d.jsonl", tmp_path / "vault" / "d.jsonl"
    decisions.append_decision(primary, _rec("a"), mirror=mirror)
    assert _keys(primary) == ["a"]
    assert _keys(mirror) == ["a"]


def test_a_failing_mirror_never_costs_the_decision(tmp_path):
    """The mirror is a backup, not a gate. A vault that unmounts mid-session must not take
    the session's judgement down with it — that is the scarce input here."""
    primary = tmp_path / "d.jsonl"
    blocked = tmp_path / "file.txt"
    blocked.write_text("not a directory", encoding="utf-8")
    warned: list[str] = []
    decisions.append_decision(primary, _rec("a"), mirror=blocked / "nested" / "d.jsonl",
                              warn=warned.append)
    assert _keys(primary) == ["a"]
    assert warned, "a silently dropped mirror write is the failure mode being prevented"


def test_append_without_a_mirror_is_unchanged(tmp_path):
    primary = tmp_path / "d.jsonl"
    decisions.append_decision(primary, _rec("a"))
    assert _keys(primary) == ["a"]


# ── sync ──────────────────────────────────────────────────────────────────────

def test_sync_seeds_a_missing_mirror_from_the_primary(tmp_path):
    """The rows that exist before mirroring is switched on are exactly the ones with no
    second copy, so they are the ones most worth copying."""
    primary, mirror = tmp_path / "d.jsonl", tmp_path / "vault" / "d.jsonl"
    _write(primary, _rec("a"), _rec("b"))
    result = decisions.sync_mirror(primary, mirror)
    assert _keys(mirror) == ["a", "b"]
    assert result.copied == 2 and result.restored == 0 and not result.diverged


def test_sync_appends_only_the_rows_the_mirror_lacks(tmp_path):
    primary, mirror = tmp_path / "d.jsonl", tmp_path / "vault" / "d.jsonl"
    _write(primary, _rec("a"), _rec("b"), _rec("c"))
    _write(mirror, _rec("a"))
    result = decisions.sync_mirror(primary, mirror)
    assert _keys(mirror) == ["a", "b", "c"]
    assert result.copied == 2


def test_sync_restores_the_primary_when_data_was_lost(tmp_path):
    """The disaster §4b describes: ``data/`` is gitignored and unbacked, so losing it loses
    every reason a setup was passed on. If the mirror is ahead, it is the survivor."""
    primary, mirror = tmp_path / "d.jsonl", tmp_path / "vault" / "d.jsonl"
    _write(mirror, _rec("a"), _rec("b"))
    result = decisions.sync_mirror(primary, mirror)
    assert _keys(primary) == ["a", "b"]
    assert result.restored == 2 and result.copied == 0


def test_sync_refuses_to_touch_either_file_when_they_diverge(tmp_path):
    """Both files are append-only, so one cannot be a prefix of the other unless they share
    a history. If they do not, appending either into the other invents a sequence that never
    happened — and the correct move is to stop and let a human look."""
    primary, mirror = tmp_path / "d.jsonl", tmp_path / "vault" / "d.jsonl"
    _write(primary, _rec("a"), _rec("b"))
    _write(mirror, _rec("a"), _rec("z"))
    result = decisions.sync_mirror(primary, mirror)
    assert result.diverged
    assert _keys(primary) == ["a", "b"], "primary untouched"
    assert _keys(mirror) == ["a", "z"], "mirror untouched"


def test_sync_is_a_no_op_when_already_equal(tmp_path):
    primary, mirror = tmp_path / "d.jsonl", tmp_path / "vault" / "d.jsonl"
    _write(primary, _rec("a"))
    _write(mirror, _rec("a"))
    result = decisions.sync_mirror(primary, mirror)
    assert (result.copied, result.restored, result.diverged) == (0, 0, False)


def test_sync_handles_both_files_absent(tmp_path):
    result = decisions.sync_mirror(tmp_path / "d.jsonl", tmp_path / "m.jsonl")
    assert (result.copied, result.restored, result.diverged) == (0, 0, False)


def test_a_decision_re_recorded_later_does_not_read_as_divergence(tmp_path):
    """Re-judging a candidate appends a superseding row rather than rewriting the old one, so
    a longer primary with repeated keys is normal history, not corruption."""
    primary, mirror = tmp_path / "d.jsonl", tmp_path / "vault" / "d.jsonl"
    _write(primary, _rec("a", "later"), _rec("a", "approved"))
    _write(mirror, _rec("a", "later"))
    result = decisions.sync_mirror(primary, mirror)
    assert not result.diverged and result.copied == 1
    assert decisions.load_decisions(mirror)["a"]["decision"] == "approved"


# ── load ──────────────────────────────────────────────────────────────────────

def test_load_returns_the_latest_record_per_candidate(tmp_path):
    primary = tmp_path / "d.jsonl"
    _write(primary, _rec("a", "later"), _rec("a", "approved"), _rec("b", "rejected"))
    loaded = decisions.load_decisions(primary)
    assert loaded["a"]["decision"] == "approved"
    assert loaded["b"]["decision"] == "rejected"


def test_load_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert decisions.load_decisions(tmp_path / "nope.jsonl") == {}


@pytest.mark.parametrize("junk", ["", "   ", "\n\n"])
def test_load_skips_blank_lines(tmp_path, junk):
    primary = tmp_path / "d.jsonl"
    primary.write_text(json.dumps(_rec("a")) + "\n" + junk + "\n", encoding="utf-8")
    assert list(decisions.load_decisions(primary)) == ["a"]
