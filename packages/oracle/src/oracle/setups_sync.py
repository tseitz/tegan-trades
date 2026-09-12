"""The freshness gate ``setups`` runs before it builds a queue.

Separate from ``oracle.freshness`` because that module only answers *how old is the cache*.
This one decides what to do about the answer, which means shelling out, and keeping the two
apart is what lets the age be measured in a test with no subprocess anywhere near it.

**Why it pulls rather than refusing.** A refusal is one more thing to read, understand and act
on at the moment you least want to — and the failure it guards against is precisely that nobody
noticed a warning. Pulling makes the correct outcome the default one.

**Why the age prints on a healthy run too.** ``review`` prints its portfolio age every run and
escalates to ``STALE`` past a threshold; this matches it. A line that appears only on the bad day
is one nobody has learned to read, so on the day it matters it looks like noise.

**A failed pull never raises.** The most likely failure is an expired OAuth token, and a session
already holding entered judgement must not lose it to a network problem. The failure is reported
and the run continues on whatever ore is on disk — with the staleness line still on screen, which
is the honest outcome rather than a silent one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import freshness

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DATA_PULL = _REPO_ROOT / "scripts" / "data-pull.sh"


def _pull() -> bool:
    """Run ``scripts/data-pull.sh``, streaming its output so a multi-minute pull shows progress."""
    try:
        return subprocess.run([str(_DATA_PULL)], cwd=_REPO_ROOT, check=False).returncode == 0
    except OSError:
        return False


def ensure_fresh(*, check=freshness.check, pull=_pull, out=print, enabled: bool = True) -> bool:
    """Report the price cache's age, and pull when it is too old to trade on.

    Returns whether the ore is trustworthy: ``True`` when it was already fresh or the pull
    succeeded, ``False`` when it is stale and still is.
    """
    state = check()
    out(f"  {state.message}")
    if not state.is_stale:
        return True

    if not enabled:
        out("  --no-sync: running on stale prices anyway")
        return False

    out("  pulling from the mirror — this takes a few minutes when days behind ...")
    if pull():
        return True
    out("  could not pull from the mirror; continuing on the ore already on disk")
    return False
