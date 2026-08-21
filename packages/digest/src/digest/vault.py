"""The digest note, in the vault, where it will actually be read.

Beside ``Trade Logs/``, which ``execution.journal`` and ``oracle.setups_cli`` write. Approvals,
outcomes and the nightly view of the queue are three readings of one question and belong in one
place.

**The vault is the primary copy; email is the mirror.** That is the reverse of how it looks
from the inbox, and it is deliberate — the same contract ``oracle.decisions`` states for its
own mirror. The note is written first and email failing does not roll it back, because a lost
email is one morning's reading and a lost note is a hole in the record.

## The one rule

``mkdir`` never creates the vault. If the anchor directory is absent — an unmounted volume, a
renamed folder, a machine that has no vault at all — this warns and writes nothing. Building
the tree would leave a convincing, empty, unread copy somewhere, which is worse than no copy
because it *looks* filed. Both ``execution.journal.append`` and ``setups_cli.resolve_vault_note``
exist for this trap; this is the third instance of it.

Creating the ``Digest/`` folder *inside* a vault that is demonstrably present is a different
thing and is allowed.
"""

from __future__ import annotations

from pathlib import Path

# The anchor. Its existence is the test for "is the vault really here" — a folder this repo
# already writes into, so it is present on any machine where filing means anything.
DEFAULT_ANCHOR = Path.home() / "vault" / "Trading"

# One note per day, in its own folder. Not appended to a single rolling file: a digest is a
# snapshot of one morning, and a year of them in one note is unreadable and un-linkable.
FOLDER = "Digest"


def note_path(anchor, *, on) -> Path:
    return Path(anchor) / FOLDER / f"{on.isoformat()}.md"


def write(anchor, body: str, *, on, warn=None) -> Path | None:
    """Write the day's note. Returns the path, or ``None`` if nothing was written.

    Never raises. This runs as a nightly step: losing the note costs one surface, while an
    exception would cost whatever the nightly had left to do.

    A second run on the same day **replaces** the note rather than appending. The digest is a
    view of one morning, not a log — unlike the trade journal, where every line is a distinct
    event and appending is the whole point.
    """
    root = Path(anchor)
    if not root.is_dir():
        if warn is not None:
            warn(f"warning: vault {root} is not reachable; the digest was not filed "
                 f"(it is still on stdout, and in the email if that was requested)")
        return None

    path = note_path(root, on=on)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Digest {on.isoformat()}\n\n{body}", encoding="utf-8")
    except OSError as exc:
        if warn is not None:
            warn(f"warning: could not write the digest note {path}: {exc}")
        return None
    return path
