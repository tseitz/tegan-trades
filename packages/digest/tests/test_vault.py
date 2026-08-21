"""Writing the digest into the vault.

Almost all of this is one rule: never create a fake vault tree. An unmounted or renamed vault
must produce a warning and no file, because a note written to a directory nobody opens looks
exactly like a note that was filed.
"""
from __future__ import annotations

from datetime import date

from digest import vault


def _vault(tmp_path):
    """A vault with the anchor directory present — the state a real machine is in."""
    anchor = tmp_path / "Trading"
    anchor.mkdir(parents=True)
    return anchor


def test_writes_one_note_per_day(tmp_path):
    anchor = _vault(tmp_path)
    written = vault.write(anchor, "body text", on=date(2026, 8, 21))
    assert written == anchor / vault.FOLDER / "2026-08-21.md"
    assert "body text" in written.read_text(encoding="utf-8")


def test_the_note_carries_the_date_as_a_heading(tmp_path):
    written = vault.write(_vault(tmp_path), "body", on=date(2026, 8, 21))
    assert written.read_text(encoding="utf-8").startswith("# Digest 2026-08-21")


def test_creates_the_digest_folder_inside_a_real_vault(tmp_path):
    """Creating a subfolder *inside* a vault that is demonstrably there is not the failure the
    guard exists to stop. Scattering the vault itself onto a machine without one is."""
    anchor = _vault(tmp_path)
    assert not (anchor / vault.FOLDER).exists()
    vault.write(anchor, "body", on=date(2026, 8, 21))
    assert (anchor / vault.FOLDER).is_dir()


def test_refuses_when_the_anchor_is_missing_and_warns(tmp_path):
    """The vault is not mounted, or has been renamed. ``mkdir`` here would build a convincing
    empty tree somewhere nobody reads — the trap ``execution.journal.append`` and
    ``setups_cli.resolve_vault_note`` both exist to avoid."""
    warned: list[str] = []
    assert vault.write(tmp_path / "no-such-vault", "body", on=date(2026, 8, 21),
                       warn=warned.append) is None
    assert warned
    assert not (tmp_path / "no-such-vault").exists()


def test_a_write_failure_warns_and_returns_none(tmp_path):
    """Runs inside a nightly step. Losing the note costs a surface; raising costs the run."""
    anchor = _vault(tmp_path)
    blocker = anchor / vault.FOLDER
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    warned: list[str] = []
    assert vault.write(anchor, "body", on=date(2026, 8, 21), warn=warned.append) is None
    assert warned


def test_rewriting_the_same_day_replaces_rather_than_appends(tmp_path):
    """A digest is a snapshot of one morning, not a log. Two runs on one day should leave the
    later view, not both — unlike the trade journal, which is genuinely append-only."""
    anchor = _vault(tmp_path)
    vault.write(anchor, "first", on=date(2026, 8, 21))
    written = vault.write(anchor, "second", on=date(2026, 8, 21))
    body = written.read_text(encoding="utf-8")
    assert "second" in body and "first" not in body
