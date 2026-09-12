"""Merge one append-only JSONL stream into another, for files two machines both write.

``data/`` is gitignored and lives on the laptop, the droplet, and an rclone mirror between them.
``rclone copy`` replaces the destination wholesale, so any file both machines append to loses
whichever side pushed second. Three files are in that position — ``data/setups/decisions.jsonl``,
``data/execution/orders.jsonl`` and ``data/setups/queue.jsonl``. This is what ``data-pull.sh``
calls instead of letting the bulk copy touch them.

**The local side is never reordered, and that is the whole design.**
``oracle.decisions.sync_mirror`` reconciles the decisions sidecar against its vault mirror by
testing whether one file's lines are a *prefix* of the other's. That mirror lives outside
``data/`` and is the last surviving copy of the only non-regenerable file in the repo. Sorting
the merged stream — by ``decided_at`` or anything else — makes neither file a prefix of the
other, so ``sync_mirror`` reports divergence, ``setups_cli`` sets ``mirror_path = None``, and the
mirror stays off for every later session because nothing ever clears that state. So: keep local
verbatim, append the remote's set-difference at the end. Boring, and it cannot latch anything off.

Ordering the result by timestamp would not have worked anyway. ``setups_cli`` stamps
``decided_at`` once per sitting rather than per row, so every row from one session shares a value
and the sort key cannot separate them.

**Only cross-side duplicates collapse.** Two byte-identical lines on one machine are two real
events — ``oracle.decisions`` compares verbatim text for exactly this reason. A line the remote
shares with local is a copy of one event; a line local repeats is two.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MergeResult:
    """What the merge did, reported so a silent no-op cannot look like a healthy sync."""

    lines: list[str]
    added: int = 0
    unchanged: int = 0

    @property
    def message(self) -> str | None:
        if not self.added:
            return None
        return f"merged {self.added} new row(s) from the mirror"


def merge_lines(local: list[str], remote: list[str]) -> MergeResult:
    """Local verbatim, then every remote line local does not already hold, in remote order."""
    held = set(local)
    seen = set(held)
    added: list[str] = []
    for line in remote:
        if line in seen:
            continue
        seen.add(line)
        added.append(line)
    return MergeResult(
        lines=local + added,
        added=len(added),
        unchanged=sum(1 for line in remote if line in held),
    )


def read_lines(path) -> list[str]:
    """Non-blank lines, verbatim — the unit the merge compares and appends by.

    Matches ``oracle.decisions._lines`` so the two readers of the decisions sidecar can never
    disagree about what counts as a row.
    """
    path = Path(path)
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def merge_file(path, remote: list[str]) -> MergeResult:
    """Merge ``remote`` into the file at ``path``, writing only when something changed.

    The no-write-on-no-change is load-bearing rather than an optimisation: ``data-pull.sh``
    passes ``--update`` for every file it does *not* merge, and that flag compares mtimes.
    Rewriting a byte-identical file moves its mtime forward on every pull, which would tell the
    next push that this machine holds something newer than it does.
    """
    path = Path(path)
    result = merge_lines(read_lines(path), remote)
    if result.added:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(ln + "\n" for ln in result.lines), encoding="utf-8")
    return result
