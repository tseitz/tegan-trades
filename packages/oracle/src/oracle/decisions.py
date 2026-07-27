"""The decisions sidecar — append-only JSONL, plus a mirrored copy in the vault.

**This is the one file under ``data/`` that is not ore.** ``docs/ARCHITECTURE.md`` describes
that tree as machine-generated and regenerable, and for transcripts, theses, stances and
prices that is exactly right — delete them and a command rebuilds them. It is false here.
These rows are hand-entered judgement, they are what ``docs/IMPROVEMENTS.md`` §4 calls the
only ground truth available for validating any of the four scorers, and nothing can
reconstruct them. ``.gitignore`` excludes ``data/``, so without a second copy the file lives
in exactly one place, on one disk, unbacked.

Approvals already reach durable storage — ``setups_cli.render_note`` appends them to the
vault. Rejections did not, and rejections are the verdict the calibration pass is actually
waiting on: the failure mode was precisely "lose ``data/`` and you keep the trades you liked
and lose every reason you passed on something".

The mirror is **subordinate to the primary, never a gate on it**. Every write goes to the
sidecar first and succeeds or fails on its own; a mirror that cannot be written warns and is
skipped. Losing a backup is recoverable, losing a session's judgement is not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def load_decisions(path) -> dict[str, dict]:
    """Latest decision record per candidate key.

    The whole record is returned, not just the verdict, because deciding whether a deferral has
    become actionable needs the state captured at the time. Later lines win, so re-deciding a
    zone supersedes the earlier answer without rewriting history.
    """
    decisions: dict[str, dict] = {}
    for rec in _rows(path):
        decisions[rec["candidate_key"]] = rec
    return decisions


def _lines(path) -> list[str]:
    """Non-blank lines, verbatim — the unit both copies are compared and appended by."""
    path = Path(path)
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _rows(path) -> list[dict]:
    return [json.loads(ln) for ln in _lines(path)]


def _append_lines(path, lines: list[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(ln + "\n" for ln in lines))


def append_decision(path, record: dict, *, mirror=None, warn=None) -> None:
    """Append one decision to the sidecar, and to the mirror when one is configured.

    The mirror write is wrapped because it targets a path outside the repo — an unmounted or
    read-only vault must not be able to destroy the decision that was just entered. It warns
    rather than passing silently: a mirror everyone believes is running and isn't is worse
    than no mirror at all, since it is only ever consulted once the primary is already gone.
    """
    line = json.dumps(record)
    _append_lines(path, [line])
    if mirror is None:
        return
    try:
        _append_lines(mirror, [line])
    except OSError as exc:
        if warn is not None:
            warn(f"warning: could not mirror decision to {mirror}: {exc}")


@dataclass(frozen=True)
class SyncResult:
    """What ``sync_mirror`` did — reported so a silent no-op can't look like a healthy sync."""
    copied: int = 0      # primary -> mirror
    restored: int = 0    # mirror -> primary, i.e. ``data/`` had been lost
    diverged: bool = False

    @property
    def message(self) -> str | None:
        if self.diverged:
            return ("warning: the decisions sidecar and its vault mirror have diverged; "
                    "neither was modified and mirroring is off for this session")
        if self.restored:
            return f"restored {self.restored} decision(s) from the vault mirror"
        if self.copied:
            return f"mirrored {self.copied} decision(s) to the vault"
        return None


def sync_mirror(primary, mirror) -> SyncResult:
    """Reconcile the two copies before a session starts, and report what moved.

    Both files are append-only, so under normal operation one is always a prefix of the other:
    whichever is shorter simply stopped earlier. That gives the three cases real meaning —

    * mirror behind → copy the missing rows forward (the ordinary case, and how rows written
      before mirroring existed acquire a second copy);
    * mirror ahead → **restore the primary from it**, which is the disaster this exists for:
      ``data/`` is gitignored and unbacked, so if it is gone the mirror is the survivor;
    * neither a prefix of the other → refuse to touch either. Appending one into the other
      would splice two histories into a sequence that never happened, and a decision log that
      quietly invents its own past is worth less than one that stops and asks for a human.

    Comparison is line-by-line on the raw JSON text rather than on parsed records, because
    identical judgements recorded at different times are genuinely different rows and only the
    verbatim text distinguishes them.
    """
    primary_lines, mirror_lines = _lines(primary), _lines(mirror)
    if primary_lines == mirror_lines:
        return SyncResult()
    if _is_prefix(mirror_lines, primary_lines):
        missing = primary_lines[len(mirror_lines):]
        _append_lines(mirror, missing)
        return SyncResult(copied=len(missing))
    if _is_prefix(primary_lines, mirror_lines):
        missing = mirror_lines[len(primary_lines):]
        _append_lines(primary, missing)
        return SyncResult(restored=len(missing))
    return SyncResult(diverged=True)


def _is_prefix(shorter: list[str], longer: list[str]) -> bool:
    return len(shorter) < len(longer) and longer[:len(shorter)] == shorter
