"""What the digest has already told you. The one file this package both reads and writes.

Everything else here is a diff between two things the pipeline wrote. This holds the third
input a *daily* diff needs and neither snapshot carries: **what was in yesterday's email.**

Two sections use it, for the same reason:

- ``roster_reported`` — the roster window is seven days wide, so one video produces a move for
  seven nights. See ``roster.unreported``.
- ``holdings_verdicts`` — a portfolio verdict is a standing answer, not an event. Without last
  night's copy there is nothing to subtract and all 77 would report as new every morning.
- ``xai_reported`` — the monthly spend total only moves when ``ingest-x`` runs, and that is off
  by default. A number that cannot change is not a diff, and shouting it nightly trains the eye
  to skip the run-health line it sits under.

**Never raises, and a lost file only ever costs a repeat.** Every failure here degrades to an
empty memory, which makes the digest say something twice. The opposite bias — treating an
unreadable file as "already reported" — would drop real movement silently, and silence is the
one failure this package has no way to surface.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Sections, so a caller cannot typo a key into a silently empty memory.
ROSTER = "roster_reported"
#: ``{portfolio: {ticker: verdict}}`` as of last night. The third section that needs a memory,
#: and for the sharpest version of the same reason: a verdict is a *standing* answer, so
#: without yesterday's copy every night would report all of them as new.
HOLDINGS = "holdings_verdicts"
#: ``{portfolio: {ticker: "kind:side"}}`` — which level each position was standing on last
#: night. Separate from the verdicts because they move independently: price can walk onto a
#: weekly block while the roster says nothing new for a month.
HOLDINGS_LEVELS = "holdings_levels"
XAI = "xai_reported"
WINDOW = "last_window_start"


def load(path: Path, *, warn=None) -> dict:
    """The stored memory, or ``{}``. Never raises."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Not a problem worth a line in the digest. It is the normal state of a fresh clone and
        # of the first run after this file was introduced.
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        if warn:
            warn(f"warning: could not read the digest memory at {Path(path).name}, so anything "
                 f"already reported may appear again: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def save(path: Path, state: dict, *, warn=None) -> bool:
    """Write ``state``. Returns whether it landed. Never raises.

    A failed write is warned about rather than swallowed. It is survivable — the cost is a
    repeated section tomorrow — but it is also exactly how this feature would quietly stop
    working, so it has to reach the reader.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        if warn:
            warn(f"warning: could not write the digest memory to {path.name}, so tonight's "
                 f"roster movement will be reported again tomorrow: {exc}")
        return False
    return True


def roster_seen(state: dict) -> dict:
    """The reported-event map, keyed as ``roster.event_keys`` builds them."""
    seen = state.get(ROSTER)
    return seen if isinstance(seen, dict) else {}


def is_repeat(state: dict, window_start: str | None) -> bool:
    """Whether this digest diffs against the same baseline as the last one.

    **Keyed on the window START, not on the current run.** Two digests went out on 2026-08-25
    saying the same things; ``setups`` had run again between them, so their *current* stamps
    differed and comparing those catches nothing. What they shared was the baseline — both
    diffed against the 2026-08-23 run, which is what made the content identical.

    A ``None`` baseline is the bootstrap night and never counts. Marking that "again" would be
    wrong on the one run where the reader has least context to judge the header by.
    """
    if window_start is None:
        return False
    return state.get(WINDOW) == window_start


def xai_changed(state: dict, month_total: float | None) -> bool:
    """Whether the monthly xAI total has moved since it was last reported.

    An absent record means it has never been reported, which counts as changed — a first run
    should say where the spend stands rather than start from silence.
    """
    if month_total is None:
        return False
    recorded = state.get(XAI)
    if not isinstance(recorded, (int, float)):
        return True
    return round(float(recorded), 4) != round(float(month_total), 4)


def holdings_seen(state: dict) -> dict:
    """Last night's ``{portfolio: {ticker: verdict}}``. Degrades to an empty memory like every
    other reader here — the cost is one repeated section, never a dropped one."""
    seen = state.get(HOLDINGS)
    return seen if isinstance(seen, dict) else {}


def holdings_levels_seen(state: dict) -> dict:
    """Last night's ``{portfolio: {ticker: level key}}``, or an empty memory."""
    seen = state.get(HOLDINGS_LEVELS)
    return seen if isinstance(seen, dict) else {}
