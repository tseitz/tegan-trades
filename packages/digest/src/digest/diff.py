"""Two queue snapshots in, one night's movement out. Pure — no I/O, no clock, no network.

The hard part here is not computing the difference. It is **refusing to explain it**.

A digest is read fast and trusted by default, so a confident wrong line costs more than a
missing one. Three places this module deliberately stays quiet:

- A trigger state of ``None`` means the trigger pass never ran for that candidate, and no
  comparison against it is valid. See ``oracle.queue_snapshot``.
- A departure whose cause is not knowable is reported as unexplained. ``BuildStats.rejections``
  is a corpus-wide counter of theses refused at ``cross_reference``; it cannot be attributed to
  a row, and borrowing from it would put a specific, confident, wrong cause beside a real event.
- A night where the score filters or the score version moved is flagged whole, because on such
  a night *every* departure has an explanation that has nothing to do with the candidates.

The caller supplies ``decided`` and ``excluded`` rather than this module reading them, which is
what keeps the interesting logic testable against hand-written snapshots instead of a populated
``data/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.rank import parse_date
from core.trigger import ARMED, FIRED, NO_TRIGGER, NO_ZONE_TAG

from digest.fmt import instant

# Why a candidate left the qualified set.
DECIDED = "decided"      # a human ruled on it since the previous snapshot
EXCLUDED = "excluded"    # a standing "I don't trade this" in cfg/exclusions.yaml
UNKNOWN = "unknown"      # not knowable from what is recorded — say so, do not guess

# What moved on the trigger timeframe.
TAGGED = "tagged"        # price reached the zone; there is a setup to watch now
TRIGGERED = "triggered"  # broke and displaced — armed, or already fired

# States that mean the trigger pass ran AND price had reached the zone. ``UNREADABLE`` is
# absent on purpose: it is a thin instrument with no computable structure, which ``core.trigger``
# keeps distinct from ``NO_TRIGGER`` precisely so it cannot be read as a healthy setup waiting.
_LIVE = (NO_TRIGGER, ARMED, FIRED)
_FIRING = (ARMED, FIRED)


@dataclass(frozen=True, slots=True)
class TriggerMove:
    """One candidate whose trigger state changed."""
    row: dict
    was: str | None
    now: str
    kind: str
    #: The candidate had no previous snapshot at all — it appeared tonight already armed.
    #:
    #: Stated rather than inferred from ``was is None``. That ``None`` means something DIFFERENT
    #: on the source row, where it means "the trigger pass never ran", and the two only stop
    #: colliding because ``_arrivals`` filters the second case out first. A renderer in another
    #: module should not have to know that to read this correctly.
    new_tonight: bool = False

    @property
    def key(self) -> str:
        return self.row["key"]

    @property
    def asset(self) -> str:
        return self.row["asset"]


@dataclass(frozen=True, slots=True)
class Departure:
    """A candidate that qualified last night and does not tonight.

    ``detail`` is populated only where the cause is genuinely knowable. ``None`` beside
    ``UNKNOWN`` is the honest rendering and is not a gap to fill in later.
    """
    row: dict
    reason: str
    detail: str | None = None

    def __post_init__(self) -> None:
        # A named cause with no text, or text sitting beside ``UNKNOWN``, both put a confident
        # wrong explanation next to a real event — the failure this module exists to avoid. It
        # is refused at construction rather than left for the renderer to notice, because the
        # renderer used to re-derive the classification from whether ``detail`` was truthy, and
        # that only worked because ``oracle.exclusions.load`` happens to reject a blank reason.
        # A rule holding by coincidence in another package is not a rule.
        if (self.reason == UNKNOWN) != (not self.detail):
            raise ValueError(f"{self.reason} departure with detail={self.detail!r}")

    @property
    def explanation(self) -> str:
        """What to print. ``UNKNOWN`` states plain absence: the corpus-wide rejection counter
        cannot be attributed to a row, so there is nothing truthful to put here."""
        return self.detail or "no longer qualifies"


@dataclass(frozen=True, slots=True)
class QueueDelta:
    current_run: str
    previous_run: str | None
    population: dict[str, int]
    #: The ``as_of`` of the snapshot this describes. Carried so the renderer can stamp it and
    #: so a caller can tell whether it is actually tonight's — see ``stale_as_of`` on the render.
    as_of: str | None = None
    arrived: tuple[TriggerMove, ...] = ()
    entered: tuple[dict, ...] = ()
    departed: tuple[Departure, ...] = ()
    rejection_delta: dict[str, int] = field(default_factory=dict)
    filters_changed: dict[str, tuple] = field(default_factory=dict)
    score_version_changed: tuple[int | None, int | None] | None = None
    #: No previous snapshot to compare against — the first ever run. Not a failure.
    bootstrap: bool = False

    @property
    def qualified(self) -> int:
        return self.population.get("qualified", 0)

    @property
    def is_quiet(self) -> bool:
        """Nothing a reader has to act on. The subject line still gets written; it just says so."""
        return not (self.arrived or self.entered or self.departed)


def current(snapshots) -> dict | None:
    """The most recent snapshot written."""
    return snapshots[-1] if snapshots else None


def previous_nightly(snapshots) -> dict | None:
    """The most recent snapshot from an *earlier* day than the latest one.

    Not simply ``snapshots[-2]``. A day of manual ``setups`` runs leaves several rows sharing
    one ``as_of``, and comparing against the last of those answers "what changed in the past ten
    minutes" — a real question, but not the one the digest asks.

    **Strictly earlier, not merely different.** ``setups --as-of`` is refused a snapshot at the
    source now, but this log is append-only and may already hold a replay row from before that
    guard existed. A row stamped with a future or out-of-order date would otherwise be selected
    as "last night" and every arrival and departure computed against it would be fiction.
    """
    if len(snapshots) < 2:
        return None
    today = _as_of(snapshots[-1])
    if today is None:
        return None
    for snapshot in reversed(snapshots[:-1]):
        stamp = _as_of(snapshot)
        if stamp is not None and stamp < today:
            return snapshot
    return None


def _as_of(snapshot: dict):
    return parse_date(snapshot.get("as_of"))


def compare(previous: dict | None, current: dict, *,
            decided: dict | None = None, excluded: dict | None = None) -> QueueDelta:
    """What moved between two snapshots.

    ``previous`` is ``None`` on the first ever run, which yields an empty delta flagged
    ``bootstrap`` rather than an error — there is simply nothing to compare yet.

    ``decided`` is ``candidate_key -> decision record`` (``oracle.decisions.load_decisions``).
    ``excluded`` is ``asset -> reason`` (``oracle.exclusions.load``).
    """
    population = current.get("population", {})
    if previous is None:
        return QueueDelta(current_run=current.get("run", ""), previous_run=None,
                          population=population, as_of=current.get("as_of"), bootstrap=True)

    before = {row["key"]: row for row in previous.get("rows", [])}
    after = {row["key"]: row for row in current.get("rows", [])}

    entered = tuple(row for key, row in after.items() if key not in before)
    departed = tuple(
        _classify(row, decided=decided or {}, excluded=excluded or {},
                  since=previous.get("run"))
        for key, row in before.items() if key not in after
    )

    return QueueDelta(
        current_run=current.get("run", ""),
        previous_run=previous.get("run"),
        population=population,
        as_of=current.get("as_of"),
        arrived=_arrivals(before, after),
        entered=entered,
        departed=departed,
        rejection_delta=_counter_delta(previous.get("rejections", {}),
                                       current.get("rejections", {})),
        filters_changed=_changed(previous.get("filters", {}), current.get("filters", {})),
        score_version_changed=(
            (previous.get("score_version"), current.get("score_version"))
            if previous.get("score_version") != current.get("score_version") else None
        ),
    )


def _arrivals(before: dict, after: dict) -> tuple[TriggerMove, ...]:
    """Candidates whose trigger state became meaningful tonight.

    Two distinct events, and the stronger one wins when both apply. ``TAGGED`` is price
    reaching the zone at all — step 1 of the trigger, and the one without which there is no
    setup. ``TRIGGERED`` is a structure break with displacement behind it.
    """
    moves = []
    for key, row in after.items():
        now = row.get("trigger_state")
        # A pass that did not run tonight says nothing about tonight.
        if now is None:
            continue
        was = before[key].get("trigger_state") if key in before else None
        # A pass that did not run last night gives nothing to compare against — reporting it
        # would announce an arrival for every candidate the first night triggers came back on.
        if key in before and was is None:
            continue
        fresh = key not in before
        if now in _FIRING and was not in _FIRING:
            moves.append(TriggerMove(row=row, was=was, now=now, kind=TRIGGERED,
                                     new_tonight=fresh))
        elif was == NO_ZONE_TAG and now in _LIVE:
            moves.append(TriggerMove(row=row, was=was, now=now, kind=TAGGED, new_tonight=fresh))
    return tuple(moves)


def _classify(row: dict, *, decided: dict, excluded: dict, since: str | None) -> Departure:
    """Name a departure's cause, or admit there isn't one available.

    Order matters: a decision is the most specific fact available and an exclusion the next,
    and a row can satisfy both (you rejected it, then excluded the asset). The decision is the
    thing that actually removed it from the queue first.
    """
    # **Without a previous run stamp nothing can be attributed.** This used to default to the
    # empty string, against which every decision ever recorded compares as newer — so a single
    # snapshot missing its ``run`` field made every departure claim a stale decision as its
    # cause. That is the exact "confident, wrong cause beside a real event" this module exists
    # to refuse, arriving through the guard's own default.
    boundary = instant(since)
    if boundary is None:
        return _excluded_or_unknown(row, excluded)

    record = decided.get(row["key"])
    # **Only a decision made since the previous snapshot explains tonight.** A row decided last
    # month and still qualifying last night was removed by something else, and reusing the old
    # decision would mask a real departure behind a stale, plausible one.
    #
    # Parsed, not string-compared. ``decided_at`` is written ``+00:00`` and ``run`` is written
    # ``Z``, and those do not sort against each other — see ``book._instant`` for the byte
    # values. The prefixes diverge first in almost every case, so a text compare is right until
    # the two land in the same second, at which point it silently reports the stale cause.
    if record is not None:
        decided_at = instant(record.get("decided_at"))
        if decided_at is not None and decided_at > boundary:
            verdict = record.get("decision", "?")
            return Departure(row=row, reason=DECIDED, detail=f"you marked it {verdict}")

    return _excluded_or_unknown(row, excluded)


def _excluded_or_unknown(row: dict, excluded: dict) -> Departure:
    """The remaining knowable cause, or none. Split out so the no-boundary path above and the
    no-recent-decision path below reach it identically."""
    reason = excluded.get(row.get("asset"))
    if reason is not None:
        return Departure(row=row, reason=EXCLUDED, detail=reason)

    return Departure(row=row, reason=UNKNOWN)


def _counter_delta(before: dict, after: dict) -> dict:
    """Movement in the corpus-wide rejection counters, dropping anything that held still.

    The absolute counts carry no information for a reader — ``weekly_disagrees`` in the
    thousands is a statement about corpus size, not about tonight. The change is the news.
    """
    keys = set(before) | set(after)
    moved = {k: after.get(k, 0) - before.get(k, 0) for k in keys}
    return {k: v for k, v in sorted(moved.items()) if v}


def _changed(before: dict, after: dict) -> dict:
    """Fields that differ, as ``name -> (was, now)``."""
    keys = set(before) | set(after)
    return {k: (before.get(k), after.get(k)) for k in sorted(keys)
            if before.get(k) != after.get(k)}
