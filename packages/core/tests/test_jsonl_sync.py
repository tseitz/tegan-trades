"""Merging one append-only JSONL stream into another.

These cover the three files two machines both append to — the decisions sidecar, the order log
and the queue snapshot. The property that matters most is the one
``test_a_local_superset_is_returned_byte_for_byte`` pins: the local side is never reordered, so
``oracle.decisions.sync_mirror`` can still recognise it as a prefix of the vault mirror.
"""
from __future__ import annotations

from core import jsonl_sync


def test_a_disjoint_remote_is_appended_in_remote_order():
    out = jsonl_sync.merge_lines(["a", "b"], ["c", "d"])
    assert out.lines == ["a", "b", "c", "d"]
    assert (out.added, out.unchanged) == (2, 0)


def test_a_local_superset_is_returned_byte_for_byte():
    """The prefix property ``sync_mirror`` depends on.

    ``oracle.decisions.sync_mirror`` reconciles the sidecar against its vault mirror by prefix.
    Reordering or rewriting the local side makes neither file a prefix of the other, which
    latches that mirror off for every later session. So a merge that adds nothing must return
    the local list unchanged, not merely equivalent.
    """
    local = ["a", "b", "c"]
    out = jsonl_sync.merge_lines(local, ["a", "b"])
    assert out.lines == local
    assert (out.added, out.unchanged) == (0, 2)


def test_the_two_machines_interleave_without_losing_either_side():
    out = jsonl_sync.merge_lines(["shared", "laptop1", "laptop2"],
                                 ["shared", "droplet1"])
    assert out.lines == ["shared", "laptop1", "laptop2", "droplet1"]
    assert out.added == 1


def test_identical_streams_are_a_no_op():
    out = jsonl_sync.merge_lines(["a", "b"], ["a", "b"])
    assert out.lines == ["a", "b"]
    assert (out.added, out.unchanged) == (0, 2)


def test_an_empty_local_takes_the_remote_whole():
    """The disaster-recovery case: a machine with no ``data/`` at all."""
    out = jsonl_sync.merge_lines([], ["a", "b"])
    assert out.lines == ["a", "b"]
    assert out.added == 2


def test_an_empty_remote_leaves_local_alone():
    out = jsonl_sync.merge_lines(["a"], [])
    assert out.lines == ["a"]
    assert out.added == 0


def test_duplicate_lines_within_one_side_are_both_kept():
    """Two byte-identical rows on one machine are two real events.

    ``oracle.decisions`` compares verbatim text precisely because identical judgements recorded
    at different times are different rows. Deduplicating within a side would delete history;
    only a line the remote *shares* with local is a copy rather than an event.
    """
    out = jsonl_sync.merge_lines(["a", "a"], [])
    assert out.lines == ["a", "a"]


def test_a_remote_duplicate_of_a_local_line_is_not_appended():
    out = jsonl_sync.merge_lines(["a"], ["a", "a"])
    assert out.lines == ["a"]
    assert out.added == 0


def test_message_names_what_moved():
    assert jsonl_sync.merge_lines(["a"], ["b"]).message == "merged 1 new row(s) from the mirror"


def test_message_is_none_when_nothing_moved():
    assert jsonl_sync.merge_lines(["a"], ["a"]).message is None


def test_merge_file_writes_only_when_something_changed(tmp_path):
    """An unchanged file must keep its mtime.

    ``data-pull.sh`` passes ``--update`` for everything it does not merge, and that flag reads
    mtimes. Rewriting a byte-identical file would move its mtime forward on every pull and
    teach the next push that the local copy is newer than it is.
    """
    path = tmp_path / "d.jsonl"
    path.write_text("a\n", encoding="utf-8")
    before = path.stat().st_mtime_ns
    out = jsonl_sync.merge_file(path, ["a"])
    assert out.added == 0
    assert path.stat().st_mtime_ns == before


def test_merge_file_appends_and_keeps_a_trailing_newline(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text("a\n", encoding="utf-8")
    out = jsonl_sync.merge_file(path, ["a", "b"])
    assert out.added == 1
    assert path.read_text(encoding="utf-8") == "a\nb\n"


def test_merge_file_creates_a_missing_file(tmp_path):
    path = tmp_path / "sub" / "d.jsonl"
    out = jsonl_sync.merge_file(path, ["a"])
    assert out.added == 1
    assert path.read_text(encoding="utf-8") == "a\n"


def test_blank_lines_are_not_treated_as_rows(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text("a\n\n  \nb\n", encoding="utf-8")
    out = jsonl_sync.merge_file(path, ["b", "c"])
    assert path.read_text(encoding="utf-8") == "a\nb\nc\n"
    assert out.added == 1
