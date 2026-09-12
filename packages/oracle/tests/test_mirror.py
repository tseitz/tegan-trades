"""The rclone boundary, and the two-way reconcile for files both machines append to.

Every test here injects a fake runner. Nothing in this file may touch the network or shell out —
the failure that motivated the module was an expired OAuth token, so a suite that needs a working
one to pass would be untestable exactly when it mattered.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from oracle import mirror

MANIFEST = """backed_up_at: 2026-09-11T11:54:51Z
host: tegan-trades
transcripts: 1348
theses:      1347
decisions:   238 rows
remote:      Total objects: 6.337k (6337) Total size: 151.805 MiB
"""


class FakeRclone:
    """Records calls; serves canned file bodies keyed by remote-relative path."""

    def __init__(self, files: dict[str, str] | None = None, fail: bool = False):
        self.files = dict(files or {})
        self.fail = fail
        self.calls: list[list[str]] = []

    @staticmethod
    def _key(target: str) -> str:
        """Strip the ``remote:path`` prefix, leaving the path relative to the mirror root."""
        if "/MANIFEST.txt" in target:
            return "MANIFEST.txt"
        _, sep, tail = target.partition("/data/")
        return f"data/{tail}" if sep else target

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self.fail:
            return mirror.Run(ok=False, out="", err="invalid_grant: maybe token expired")
        if args[0] == "cat":
            body = self.files.get(self._key(args[1]))
            if body is None:
                return mirror.Run(ok=False, out="", err="directory not found")
            return mirror.Run(ok=True, out=body, err="")
        if args[0] == "rcat":
            self.files[self._key(args[1])] = kwargs.get("stdin", "")
            return mirror.Run(ok=True, out="", err="")
        return mirror.Run(ok=True, out="", err="")


# ── manifest parsing ──────────────────────────────────────────────────────────

def test_backed_up_at_is_parsed_from_the_z_suffixed_stamp():
    assert mirror.parse_manifest(MANIFEST).backed_up_at == datetime(
        2026, 9, 11, 11, 54, 51, tzinfo=UTC)


def test_host_is_parsed():
    assert mirror.parse_manifest(MANIFEST).host == "tegan-trades"


def test_a_count_written_with_its_unit_still_parses():
    """``backup.sh`` writes ``decisions:   238 rows`` — the value is not an integer.

    A parser assuming ``int(value)`` returns None on every real manifest, which reads as a
    healthy absent field rather than as a bug.
    """
    assert mirror.parse_manifest(MANIFEST).decisions == 238


def test_a_colon_bearing_value_does_not_break_the_split():
    """``remote: Total objects: 6.337k ...`` has three colons on one line."""
    assert mirror.parse_manifest(MANIFEST).host == "tegan-trades"


def test_an_empty_manifest_yields_all_none_rather_than_raising():
    m = mirror.parse_manifest("")
    assert (m.backed_up_at, m.host, m.decisions) == (None, None, None)


def test_a_garbled_timestamp_is_none_not_an_exception():
    assert mirror.parse_manifest("backed_up_at: yesterday\n").backed_up_at is None


def test_a_non_numeric_count_is_none():
    assert mirror.parse_manifest("decisions: lots of rows\n").decisions is None


# ── the rclone boundary ───────────────────────────────────────────────────────

def test_fetch_manifest_returns_the_body():
    rc = FakeRclone({"MANIFEST.txt": MANIFEST})
    assert mirror.fetch_manifest("gdrive:Coding/tegan-trades", runner=rc) == MANIFEST


def test_an_expired_token_yields_none_rather_than_raising():
    """The exact production failure. It must degrade, not crash a trading session."""
    assert mirror.fetch_manifest("gdrive:x", runner=FakeRclone(fail=True)) is None


def test_the_token_hint_names_the_reconnect_command():
    """An error nobody can act on is an error nobody acts on."""
    assert "rclone config reconnect" in mirror.TOKEN_HINT


# ── the synced set is defined once ────────────────────────────────────────────

def test_the_exclude_flags_cover_every_synced_file():
    """``data-pull.sh`` must exclude exactly what this module reconciles.

    The list is emitted from here rather than written out in the shell script, because the
    silent-loss mode is the two drifting apart: the bulk copy replaces a file, the merge then
    unions the remote copy with itself, and every unit test still passes.
    """
    flags = mirror.exclude_flags()
    for rel in mirror.SYNCED:
        assert f"--exclude\n{rel}" in flags


def test_exclude_paths_are_relative_to_the_data_root_not_the_repo():
    """``rclone copy "$DEST/data/" data/`` roots patterns at ``data/``.

    ``data/setups/decisions.jsonl`` would match nothing and silently exclude nothing.
    """
    assert all(not rel.startswith("data/") for rel in mirror.SYNCED)
    assert "setups/decisions.jsonl" in mirror.SYNCED


def test_the_receipt_is_in_the_synced_set_so_it_never_travels():
    assert mirror.RECEIPT_EXCLUDE in mirror.exclude_flags()


# ── reconcile ─────────────────────────────────────────────────────────────────

def test_reconcile_pulls_rows_the_local_file_lacks(tmp_path):
    local = tmp_path / "data" / "setups" / "decisions.jsonl"
    local.parent.mkdir(parents=True)
    local.write_text("a\n", encoding="utf-8")
    rc = FakeRclone({"data/setups/decisions.jsonl": "a\nb\n"})
    report = mirror.reconcile("gdrive:Coding/tegan-trades", root=tmp_path / "data", runner=rc)
    assert local.read_text(encoding="utf-8") == "a\nb\n"
    assert report.pulled == 1


def test_reconcile_pushes_rows_the_mirror_lacks(tmp_path):
    local = tmp_path / "data" / "setups" / "decisions.jsonl"
    local.parent.mkdir(parents=True)
    local.write_text("a\nb\n", encoding="utf-8")
    rc = FakeRclone({"data/setups/decisions.jsonl": "a\n"})
    report = mirror.reconcile("gdrive:Coding/tegan-trades", root=tmp_path / "data", runner=rc)
    assert rc.files["data/setups/decisions.jsonl"] == "a\nb\n"
    assert report.pushed == 1


def test_reconcile_never_reorders_the_local_side(tmp_path):
    """The prefix property ``oracle.decisions.sync_mirror`` depends on."""
    local = tmp_path / "data" / "setups" / "decisions.jsonl"
    local.parent.mkdir(parents=True)
    local.write_text("z\ny\n", encoding="utf-8")
    rc = FakeRclone({"data/setups/decisions.jsonl": "a\n"})
    mirror.reconcile("gdrive:x", root=tmp_path / "data", runner=rc)
    assert local.read_text(encoding="utf-8").startswith("z\ny\n")


def test_reconcile_uploads_nothing_when_the_two_already_agree(tmp_path):
    local = tmp_path / "data" / "setups" / "decisions.jsonl"
    local.parent.mkdir(parents=True)
    local.write_text("a\n", encoding="utf-8")
    rc = FakeRclone({"data/setups/decisions.jsonl": "a\n"})
    mirror.reconcile("gdrive:x", root=tmp_path / "data", runner=rc)
    assert not [c for c in rc.calls if c[0] == "rcat"]


def test_a_file_absent_from_the_mirror_is_uploaded_not_treated_as_empty(tmp_path):
    """A mirror that has never seen this file must not read as 'the remote deleted everything'."""
    local = tmp_path / "data" / "setups" / "queue.jsonl"
    local.parent.mkdir(parents=True)
    local.write_text("a\n", encoding="utf-8")
    rc = FakeRclone({})
    report = mirror.reconcile("gdrive:x", root=tmp_path / "data", runner=rc)
    assert rc.files["data/setups/queue.jsonl"] == "a\n"
    assert report.pulled == 0


def test_a_file_absent_on_both_sides_is_skipped_quietly(tmp_path):
    (tmp_path / "data").mkdir()
    report = mirror.reconcile("gdrive:x", root=tmp_path / "data", runner=FakeRclone({}))
    assert (report.pulled, report.pushed) == (0, 0)
    assert report.ok


def test_reconcile_reports_not_ok_when_rclone_fails(tmp_path):
    """``data-pull.sh`` has no ``set -e``; a warning would be swallowed and print success."""
    local = tmp_path / "data" / "setups" / "decisions.jsonl"
    local.parent.mkdir(parents=True)
    local.write_text("a\n", encoding="utf-8")
    report = mirror.reconcile("gdrive:x", root=tmp_path / "data", runner=FakeRclone(fail=True))
    assert report.ok is False


def test_a_failed_reconcile_leaves_the_local_file_untouched(tmp_path):
    local = tmp_path / "data" / "setups" / "decisions.jsonl"
    local.parent.mkdir(parents=True)
    local.write_text("a\n", encoding="utf-8")
    mirror.reconcile("gdrive:x", root=tmp_path / "data", runner=FakeRclone(fail=True))
    assert local.read_text(encoding="utf-8") == "a\n"


def test_main_exits_non_zero_when_reconcile_fails(tmp_path, capsys):
    local = tmp_path / "data" / "setups" / "decisions.jsonl"
    local.parent.mkdir(parents=True)
    local.write_text("a\n", encoding="utf-8")
    rc = FakeRclone(fail=True)
    code = mirror.main(["reconcile", "--dest", "gdrive:x", "--root", str(tmp_path / "data")],
                       runner=rc)
    assert code != 0
    assert "rclone config reconnect" in capsys.readouterr().err


def test_main_excludes_prints_flags_for_the_shell(capsys):
    assert mirror.main(["excludes"]) == 0
    assert "setups/decisions.jsonl" in capsys.readouterr().out


def test_main_synced_prints_bare_paths_one_per_line(capsys):
    """``--force`` restores these wholesale, and needs the paths without the flag noise."""
    assert mirror.main(["synced"]) == 0
    assert capsys.readouterr().out.split() == list(mirror.SYNCED)


@pytest.mark.parametrize("rel", ["setups/decisions.jsonl", "execution/orders.jsonl",
                                 "setups/queue.jsonl"])
def test_all_three_exposed_files_are_reconciled(tmp_path, rel):
    local = tmp_path / "data" / rel
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("a\n", encoding="utf-8")
    rc = FakeRclone({f"data/{rel}": "a\nb\n"})
    mirror.reconcile("gdrive:x", root=tmp_path / "data", runner=rc)
    assert local.read_text(encoding="utf-8") == "a\nb\n"
