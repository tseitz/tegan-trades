"""How old is the price cache — asked locally, with no network call.

``setups`` reads whatever is on disk. Between 2026-09-04 and 2026-09-11 the laptop's rclone
token expired, no command said so, and every run scored six-day-old bars and re-presented a
queue that had already been triaged. This is the line that would have caught it.

**Why the age is measured here and not asked of the Drive mirror.** The obvious check is to read
the mirror's ``MANIFEST.txt`` and compare its ``backed_up_at`` against what this machine last
pulled. That check is defeated by the failure it exists to catch: ``backup.sh`` writes the
manifest *after* the ``rclone copy`` it exits on, so a night the droplet's backup fails leaves
``backed_up_at`` frozen at yesterday and the laptop reads "not behind". A guard derived from the
pipeline whose failure it is meant to detect fails closed to "healthy". Newest mtime under the
price cache has none of that: it is a ``stat`` walk over local disk, it works offline, and it
names the harm directly rather than one step removed.

**Unknown is stale, never fresh.** A cache nobody can date is not evidence of freshness — a fresh
clone with an empty ``data/`` would otherwise score every asset against no bars and call the
result a queue. ``digest.cli``'s "wrong is worse than missing" applies exactly here.

The mtime is when a fetch last *wrote*, not the date of the newest bar. That is the wanted
question: a weekend leaves Friday's bar newest however often you fetch, so bar dates would report
a healthy cache as two days stale every Sunday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import cache

#: The droplet fetches nightly, so anything past one night means a run was missed. Deliberately
#: not tighter: the laptop is expected to sit idle for hours during a session, and a guard that
#: fires on a normal evening is one that gets ignored on the evening it matters.
STALE_AFTER = timedelta(days=1)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Freshness:
    """The cache's age, and whether that is too old to run on."""

    age: timedelta | None

    @property
    def is_stale(self) -> bool:
        return self.age is None or self.age > STALE_AFTER

    @property
    def message(self) -> str:
        if self.age is None:
            return "prices: never fetched on this machine — STALE"
        return f"prices: fetched {_humanise(self.age)} ago{' — STALE' if self.is_stale else ''}"


def _humanise(age: timedelta) -> str:
    hours = age.total_seconds() / 3600
    if hours < 24:
        return f"{hours:.0f} hours"
    return f"{age.days} days"


def check(*, root: Path | None = None, now=None) -> Freshness:
    """Age of the newest file anywhere under the price cache.

    Newest rather than oldest: a fetch skips symbols it cannot route, so unroutable files linger
    indefinitely beside current ones and the oldest would report years of staleness forever.
    """
    root = Path(cache.DATA_ROOT if root is None else root)
    newest = max((p.stat().st_mtime for p in root.rglob("*") if p.is_file()), default=None)
    if newest is None:
        return Freshness(age=None)
    return Freshness(age=(now or _now()) - datetime.fromtimestamp(newest, tz=UTC))
