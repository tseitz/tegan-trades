"""Videos that will never yield a transcript, recorded so the sweep stops re-asking.

**The cost this exists to remove is a signal, not a network call.** Re-fetching six dead
videos a night is cheap — none of them reach the retry backoff, because the exceptions that
mark them permanent are not ``RequestException`` and so bypass ``_TRANSIENT`` on the first
attempt. What is expensive is that ``failed`` was never zero. The nightly run of 2026-07-27
reported ``10 failed`` and every one was expected: 6 with captions disabled, 2 deleted, 2
livestreams not yet aired. A counter that always reads ten cannot report the eleventh, which
is precisely the silent-failure mode the nightly cycle is built to avoid — see
``scripts/nightly.sh``, where the same reasoning gives every step a recorded status and a
missing vault line, rather than a quiet log, is the signal.

So the registry's job is to move the permanently-dead out of ``failed`` and leave that count
meaning "something happened that nobody has accounted for".

**Only genuinely terminal outcomes belong here.** An IP block is not one — it is systemic and
transient, it already has its own abort path (``TranscriptBlocked``), and recording a blocked
video as dead would permanently discard content over a temporary network condition. That is
why callers pass an explicit reason drawn from a closed set rather than this module inferring
one from an exception it was handed.

The file is a plain JSON map under ``data/``, so it is regenerable in the sense that matters:
delete it and every entry is simply re-attempted, costing one sweep. ``first_seen`` is kept so
a future recheck policy ("retry anything marked dead over a year ago") has something to work
from — captions can be switched on after the fact, even if none of these ever have been.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from ingestion.store import DATA_ROOT

# Beside the transcripts it is about, not in a sibling tree — the two are only meaningful
# together, and a restore that brought back one without the other would re-fetch the lot.
DEAD_FILE = DATA_ROOT / "_dead.json"

# Why a video can never be ingested. Closed set: a caller reaching for a reason that is not
# here is describing something transient, and transient failures must stay in ``failed``.
NO_CAPTIONS = "no_captions"       # uploader disabled subtitles; nothing to fetch, ever
UNAVAILABLE = "unavailable"       # deleted, private, or region-locked out of existence
AGE_RESTRICTED = "age_restricted"  # needs an authenticated session the sweep does not have
REASONS = frozenset({NO_CAPTIONS, UNAVAILABLE, AGE_RESTRICTED})


def load(path: Path = DEAD_FILE) -> dict[str, dict]:
    """The registry, or an empty one. A malformed file is *not* silently replaced."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def is_dead(video_id: str, registry: dict[str, dict]) -> bool:
    return video_id in registry


def record(
    video_id: str,
    reason: str,
    *,
    registry: dict[str, dict],
    today: date | None = None,
) -> dict[str, dict]:
    """A new registry with ``video_id`` marked dead. Never mutates the one passed in.

    Re-recording an already-dead video keeps the original ``first_seen``: the question that
    date answers is "how long has this been dead", and refreshing it every night would make
    every entry look like it died today.
    """
    if reason not in REASONS:
        raise ValueError(f"{reason!r} is not a permanent failure; leave it in `failed`")
    if video_id in registry:
        return registry
    stamped = today or datetime.now(UTC).date()
    return {
        **registry,
        video_id: {"reason": reason, "first_seen": stamped.isoformat()},
    }


def save(registry: dict[str, dict], path: Path = DEAD_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    return path
