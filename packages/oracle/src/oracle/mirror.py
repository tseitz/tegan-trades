"""The rclone boundary, and a two-way reconcile for the files both machines append to.

``data/`` is gitignored, so it lives on the laptop, on the droplet, and in an rclone mirror
between them (``$TEGAN_BACKUP_DEST``, default ``gdrive:Coding/tegan-trades``). ``backup.sh``
pushes and ``data-pull.sh`` pulls, both with ``rclone copy``, which **replaces the destination
wholesale**. For ore that is correct — a price file refetched anywhere is the same price file.
For a file two machines *append* to it is a silent loss: whoever copies second wins, and the
other machine's rows are gone with nothing reporting it.

Three files are in that position, and one of them had already diverged when this was written:

* ``setups/decisions.jsonl`` — hand-entered judgement. ``oracle.decisions`` explains why nothing
  can reconstruct it.
* ``execution/orders.jsonl`` — the laptop places, the droplet's ``book --reconcile`` settles.
  The venue can replay the order but not the ``candidate_key`` join, which is the whole point.
* ``setups/queue.jsonl`` — one row per run. The droplet writes ~11:00 UTC from cron, the laptop
  writes interactively, so the two never collide on a timestamp and never share a suffix either.

**``reconcile`` is one primitive doing both directions**, which is what makes it impossible to
clobber: fetch the remote copy, merge it into the local file, and upload the result only if the
local side gained something the mirror lacks. Order is preserved by ``core.jsonl_sync`` — read
its docstring before changing anything here, because reordering the decisions sidecar latches off
the vault mirror that is its last surviving copy.

**The synced set is defined here and nowhere else.** ``data-pull.sh`` gets its ``--exclude``
flags from ``excludes``, rather than listing the same paths in shell. If the two ever drifted the
bulk copy would replace a file *before* the merge ran, the merge would then union the remote copy
with itself, and every unit test in the repo would still pass while the local-only rows were
gone. Paths are relative to ``data/`` because that is the copy root
(``rclone copy "$DEST/data/" data/``); a ``data/``-prefixed pattern matches nothing and excludes
nothing, which fails the same silent way.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core import jsonl_sync

#: Relative to ``data/`` — see the module docstring on why the prefix is absent.
SYNCED = (
    "setups/decisions.jsonl",
    "execution/orders.jsonl",
    "setups/queue.jsonl",
)

#: ``data-pull.sh`` writes this after a successful pull and ``setups`` reads it. It must never
#: travel: ``backup.sh`` uploads all of ``data/``, so without the exclusion the droplet's receipt
#: lands on the laptop and tells it that it pulled when it did not.
RECEIPT = ".last-pull"
RECEIPT_EXCLUDE = RECEIPT

TOKEN_HINT = ("the Google token may have expired — run `rclone config reconnect gdrive:` "
              "(the trailing colon is required)")

#: The same variable and the same default as ``backup.sh`` and ``data-pull.sh``, so pointing at
#: a second destination stays a one-variable change and no two callers can disagree about where
#: the mirror is. A value without a colon is a plain local path, which is what the round-trip
#: test uses to run a real two-machine cycle with no network.
DEST_ENV = "TEGAN_BACKUP_DEST"
DEFAULT_DEST = "gdrive:Coding/tegan-trades"


def default_dest() -> str:
    return os.environ.get(DEST_ENV) or DEFAULT_DEST


@dataclass(frozen=True)
class Run:
    """One rclone invocation's result, so the callers never touch ``CompletedProcess``."""

    ok: bool
    out: str
    err: str


def _rclone(args: list[str], **kwargs) -> Run:
    stdin = kwargs.get("stdin")
    try:
        proc = subprocess.run(["rclone", *args], capture_output=True, text=True,
                              input=stdin, timeout=300, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return Run(ok=False, out="", err=str(exc))
    return Run(ok=proc.returncode == 0, out=proc.stdout, err=proc.stderr)


@dataclass(frozen=True)
class Manifest:
    """What ``backup.sh`` recorded about the snapshot now sitting in the mirror."""

    backed_up_at: datetime | None = None
    host: str | None = None
    decisions: int | None = None


def parse_manifest(text: str) -> Manifest:
    """Parse ``MANIFEST.txt``, tolerating every shape ``backup.sh`` actually emits.

    Two traps live in that format. Values carry units — ``decisions:   238 rows`` is not an
    integer — and one value contains further colons (``remote: Total objects: ...``), so the
    split has to be on the first colon only. A parser that assumes otherwise returns ``None``
    for every field, which reads as a healthy absent value rather than as a bug.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return Manifest(
        backed_up_at=_parse_stamp(fields.get("backed_up_at")),
        host=fields.get("host") or None,
        decisions=_leading_int(fields.get("decisions")),
    )


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _leading_int(value: str | None) -> int | None:
    if not value:
        return None
    head = value.split()[0]
    return int(head) if head.isdigit() else None


def fetch_manifest(dest: str, *, runner=_rclone) -> str | None:
    """The mirror's manifest, or ``None`` if it could not be read for any reason.

    Never raises. An expired token is the single most likely failure and it must degrade to a
    warning — refusing to run offline would make the laptop useless on a plane, and crashing
    mid-session would cost the judgement already entered.
    """
    run = runner(["cat", f"{dest}/MANIFEST.txt"])
    return run.out if run.ok else None


def exclude_flags() -> str:
    """Newline-separated ``--exclude <pattern>`` pairs, for ``xargs``-style use in shell."""
    patterns = [*SYNCED, RECEIPT_EXCLUDE]
    return "\n".join(f"--exclude\n{p}" for p in patterns)


@dataclass(frozen=True)
class Report:
    """What ``reconcile`` moved, per direction, so a silent no-op cannot look healthy."""

    pulled: int = 0
    pushed: int = 0
    ok: bool = True
    problems: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        if not self.ok:
            return "mirror reconcile FAILED: " + "; ".join(self.problems)
        if not (self.pulled or self.pushed):
            return "mirror: in sync"
        return f"mirror: pulled {self.pulled} row(s), pushed {self.pushed} row(s)"


def reconcile(dest: str, *, root: Path | None = None, runner=_rclone) -> Report:
    """Two-way merge of every file in ``SYNCED`` between this machine and the mirror.

    Both directions in one primitive is what makes clobbering impossible: the upload always
    carries the merged union, so it can only ever be a superset of what the mirror held. A file
    the mirror has never seen uploads whole rather than reading as an empty remote — treating
    "absent" as "empty" would be indistinguishable from the remote having lost everything.
    """
    root = Path(root if root is not None else _data_root())
    pulled = pushed = 0
    problems: list[str] = []

    for rel in SYNCED:
        local = root / rel
        remote_body = runner(["cat", f"{dest}/data/{rel}"])
        if not remote_body.ok:
            # Absent is normal — a mirror that has never held this file. Unreadable is not, and
            # the two are distinguished by whether the local file exists to upload instead.
            if not local.exists():
                continue
            if _is_auth_failure(remote_body.err):
                problems.append(f"{rel}: {remote_body.err.strip()}")
                continue
            remote_lines: list[str] = []
        else:
            remote_lines = [ln for ln in remote_body.out.splitlines() if ln.strip()]

        result = jsonl_sync.merge_file(local, remote_lines)
        pulled += result.added
        local_lines = jsonl_sync.read_lines(local)
        if len(local_lines) > len(remote_lines):
            up = runner(["rcat", f"{dest}/data/{rel}"],
                        stdin="".join(ln + "\n" for ln in local_lines))
            if not up.ok:
                problems.append(f"{rel}: upload failed — {up.err.strip()}")
                continue
            pushed += len(local_lines) - len(remote_lines)

    return Report(pulled=pulled, pushed=pushed, ok=not problems, problems=tuple(problems))


def _is_auth_failure(err: str) -> bool:
    return any(token in err for token in ("invalid_grant", "token", "couldn't fetch"))


def _data_root() -> Path:
    # packages/oracle/src/oracle/mirror.py -> ... -> <repo root>
    return Path(__file__).resolve().parents[4] / "data"


def read_receipt(root: Path | None = None) -> datetime | None:
    path = Path(root if root is not None else _data_root()) / RECEIPT
    if not path.exists():
        return None
    return _parse_stamp(path.read_text(encoding="utf-8").strip())


def write_receipt(when: datetime | str, root: Path | None = None) -> None:
    path = Path(root if root is not None else _data_root()) / RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = when if isinstance(when, str) else when.astimezone(UTC).isoformat()
    path.write_text(stamp + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, *, runner=_rclone) -> int:
    parser = argparse.ArgumentParser(prog="oracle.mirror", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("reconcile", help="two-way merge the append-only files")
    rec.add_argument("--dest", required=True, help="an rclone remote:path, or a local path")
    rec.add_argument("--root", type=Path, default=None, help="the data/ root to reconcile")

    sub.add_parser("excludes", help="print --exclude flags for the bulk rclone copy")
    sub.add_parser("synced", help="print the data/-relative path of each reconciled file")

    args = parser.parse_args(argv)

    if args.command == "excludes":
        print(exclude_flags())
        return 0

    if args.command == "synced":
        print("\n".join(SYNCED))
        return 0

    report = reconcile(args.dest, root=args.root, runner=runner)
    print(f"  {report.message}")
    if not report.ok:
        print(f"  {TOKEN_HINT}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
