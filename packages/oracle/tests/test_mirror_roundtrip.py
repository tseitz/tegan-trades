"""Two machines, one mirror, no network — the test the unit tests cannot replace.

``backup.sh`` documents that ``$TEGAN_BACKUP_DEST`` may be a plain local path, and both scripts
skip their ``rclone listremotes`` guard when the destination has no colon. So a real rclone
round-trip runs against a temp directory: no Google account, no token, no wire.

**Why this exists.** Every function ``data-pull.sh`` depends on is unit-tested and green. The way
this still loses rows is entirely in the shell: if the bulk ``rclone copy`` is not actually
excluding the append-only files, it replaces one *before* the reconcile runs, the reconcile then
merges the remote copy into a local copy that is already the remote copy, and the union is
computed between a file and itself. Every unit test passes. The pull prints success. The rows are
gone. Only an end-to-end run notices, and the assertion that notices is
``test_a_pull_does_not_clobber_rows_this_machine_alone_has``.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REPO = __import__("pathlib").Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None,
                                reason="rclone not installed")


def _run(script: str, *args, cwd, dest):
    """Invoke the machine's own copy of the script, never the real repo's.

    Both scripts derive their repo root from ``${BASH_SOURCE[0]}`` and then ``cd`` there, so the
    path used to *invoke* them decides which ``data/`` they rewrite. Calling
    ``REPO/scripts/data-pull.sh`` — the obvious thing to write — points them at the real corpus
    and, under ``--force``, replaces the real decisions sidecar with the fixture's contents.
    That happened once while writing these tests; 257 rows became 1. The assertion below is why
    it cannot happen again.
    """
    invoked = cwd / "scripts" / script
    assert REPO not in cwd.parents and cwd != REPO, \
        f"refusing to run {script} against the real repo at {REPO}"

    # Checking the invocation path is not enough, and assuming it was is what let this escape a
    # second time: `oracle.mirror` derives its default data root from the *module's* location, so
    # a reconcile invoked without `--root` reaches the real corpus no matter where the script was
    # called from or what its cwd is. Only watching the real file catches that whole class.
    canaries = {p: p.read_bytes() for p in (REPO / "data").glob("*/*.jsonl") if p.is_file()}
    env = {**os.environ, "TEGAN_BACKUP_DEST": str(dest)}
    result = subprocess.run([str(invoked), *args],
                            cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    for path, before in canaries.items():
        assert path.read_bytes() == before, f"{script} modified the real corpus at {path}"
    return result


@pytest.fixture
def two_machines(tmp_path):
    """A shared mirror plus two checkouts, each with its own ``data/``.

    The scripts resolve the repo from their own location, so each machine is a directory holding
    a ``data/`` and a ``scripts/`` symlinked back to the real ones — enough for both scripts to
    run without copying the whole repo.
    """
    mirror = tmp_path / "mirror"
    (mirror / "data").mkdir(parents=True)
    machines = {}
    for name in ("laptop", "droplet"):
        root = tmp_path / name
        (root / "data" / "setups").mkdir(parents=True)
        (root / "data" / "execution").mkdir(parents=True)
        (root / "scripts").mkdir()
        for script in ("data-pull.sh", "backup.sh"):
            (root / "scripts" / script).symlink_to(REPO / "scripts" / script)
        # Both scripts resolve their repo root from their own location and then `cd` there, so a
        # machine needs to look like a checkout or the `uv run` calls inside them find no project.
        # Everything but `data/` is shared with the real repo — `data/` is the whole point.
        for shared in ("pyproject.toml", "uv.lock", "packages", ".venv"):
            (root / shared).symlink_to(REPO / shared)
        machines[name] = root
    return mirror, machines["laptop"], machines["droplet"]


def _decisions(root):
    return (root / "data" / "setups" / "decisions.jsonl")


def _lines(path):
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_a_pull_does_not_clobber_rows_this_machine_alone_has(two_machines):
    """The headline. A laptop row absent from the mirror must survive a pull.

    This is the exact loss the 2026-09 outage risked: the laptop held 19 decisions the mirror had
    never seen, and any pull with the file inside the bulk copy would have destroyed them.
    """
    mirror, laptop, _ = two_machines
    (mirror / "data" / "setups").mkdir(parents=True)
    (mirror / "data" / "setups" / "decisions.jsonl").write_text(
        '{"candidate_key":"shared"}\n', encoding="utf-8")
    _decisions(laptop).write_text(
        '{"candidate_key":"shared"}\n{"candidate_key":"laptop-only"}\n', encoding="utf-8")

    result = _run("data-pull.sh", cwd=laptop, dest=mirror)

    assert result.returncode == 0, result.stderr
    keys = _lines(_decisions(laptop))
    assert '{"candidate_key":"laptop-only"}' in keys, "the pull destroyed a local-only row"
    assert '{"candidate_key":"shared"}' in keys


def test_each_machine_ends_with_the_union_after_a_full_cycle(two_machines):
    mirror, laptop, droplet = two_machines
    _decisions(laptop).write_text('{"k":"shared"}\n{"k":"laptop"}\n', encoding="utf-8")
    _decisions(droplet).write_text('{"k":"shared"}\n{"k":"droplet"}\n', encoding="utf-8")

    assert _run("data-pull.sh", cwd=laptop, dest=mirror).returncode == 0
    assert _run("data-pull.sh", cwd=droplet, dest=mirror).returncode == 0
    assert _run("data-pull.sh", cwd=laptop, dest=mirror).returncode == 0

    for machine in (laptop, droplet):
        got = set(_lines(_decisions(machine)))
        assert {'{"k":"shared"}', '{"k":"laptop"}', '{"k":"droplet"}'} <= got, machine.name


def test_the_local_side_keeps_its_order_so_the_vault_mirror_still_matches(two_machines):
    """``oracle.decisions.sync_mirror`` compares by prefix — reordering latches it off forever."""
    mirror, laptop, _ = two_machines
    (mirror / "data" / "setups").mkdir(parents=True)
    (mirror / "data" / "setups" / "decisions.jsonl").write_text('{"k":"aaa"}\n', encoding="utf-8")
    _decisions(laptop).write_text('{"k":"zzz"}\n{"k":"mmm"}\n', encoding="utf-8")

    assert _run("data-pull.sh", cwd=laptop, dest=mirror).returncode == 0

    after = _lines(_decisions(laptop))
    assert after[:2] == ['{"k":"zzz"}', '{"k":"mmm"}'], "local rows were reordered"


def test_a_dry_run_writes_nothing(two_machines):
    mirror, laptop, _ = two_machines
    (mirror / "data" / "setups").mkdir(parents=True)
    (mirror / "data" / "setups" / "decisions.jsonl").write_text('{"k":"remote"}\n',
                                                                encoding="utf-8")
    _decisions(laptop).write_text('{"k":"local"}\n', encoding="utf-8")

    assert _run("data-pull.sh", "--dry-run", cwd=laptop, dest=mirror).returncode == 0

    assert _lines(_decisions(laptop)) == ['{"k":"local"}']
    assert not (laptop / "data" / ".last-pull").exists()


def test_the_receipt_never_travels_to_the_mirror(two_machines):
    """Uploaded, it would come back down and tell the other machine it had pulled.

    The assertions are on what reached the mirror, not on ``backup.sh``'s exit code. That code
    reflects its last command — printing a manifest built with ``MANIFEST="$(mktemp)"`` — and
    ``mktemp`` on macOS resolves to ``/var/folders`` regardless of ``TMPDIR``, which a sandboxed
    run denies while still exiting 0. Asserting on the exit code makes this test fail for a
    reason that has nothing to do with what it is checking. Positively asserting that the
    decisions file *did* arrive is the stronger check anyway: it proves the copy ran, so the
    receipt's absence is an exclusion rather than a backup that never happened.
    """
    mirror, laptop, _ = two_machines
    _decisions(laptop).write_text('{"k":"a"}\n', encoding="utf-8")
    (laptop / "data" / ".last-pull").write_text("2026-09-11T11:54:51+00:00\n", encoding="utf-8")

    _run("backup.sh", cwd=laptop, dest=mirror)

    assert (mirror / "data" / "setups" / "decisions.jsonl").exists(), "the backup never ran"
    assert not (mirror / "data" / ".last-pull").exists()


def test_force_takes_the_mirror_copy_wholesale(two_machines):
    """``--force`` means the local corpus is damaged. Merging would splice the damage in."""
    mirror, laptop, _ = two_machines
    (mirror / "data" / "setups").mkdir(parents=True)
    (mirror / "data" / "setups" / "decisions.jsonl").write_text('{"k":"good"}\n',
                                                                encoding="utf-8")
    _decisions(laptop).write_text("corrupted garbage\n", encoding="utf-8")

    assert _run("data-pull.sh", "--force", cwd=laptop, dest=mirror).returncode == 0

    assert _lines(_decisions(laptop)) == ['{"k":"good"}']
