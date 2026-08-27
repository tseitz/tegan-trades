"""What qualified on a given run — the only record of the queue as it stood that night.

``decisions.jsonl`` holds candidates a human *ruled on*. Nothing recorded the population those
rulings were drawn from, so "what is new tonight" and "what quietly fell out" were both
unanswerable. This is that record, and it exists for the nightly digest to diff against.

**It snapshots the qualified population, never the shown queue.** ``setups_cli`` builds the
displayed queue with ``rng=queue.rng_for(as_of)`` — the stratified sample is seeded on the
date, so it is stable within a day and *deliberately different every day*. Diffing two sampled
queues would manufacture "new" and "fell out" rows every single night, and that churn would be
indistinguishable from real movement. ``qualified`` is the population; the sample is display.

**This is ore, not ground truth — no vault mirror.** ``oracle.decisions`` mirrors because
hand-entered judgement cannot be reconstructed. These rows are machine output, and the digest's
vault note is the durable surface for anything worth keeping. Losing this file costs one
night's diff, not a record of what anyone thought.

## What a diff of two snapshots can and cannot say

It can say a key is new, a key is gone, or a key's ``trigger_state`` moved. Those are exact.

**It cannot attribute *why* a key is gone.** ``BuildStats.rejections`` counts theses refused at
``cross_reference`` across the whole corpus; it is not per-candidate and cannot be made so by
reading it more carefully. A reader classifying a departure has to work from what is knowable
elsewhere — the decisions sidecar says it was ruled on, and ``cfg/exclusions.yaml`` says the asset
was excluded. A third is knowable in principle — the row's own recorded ``score`` against
tonight's ``--min-score`` — but is NOT implemented: ``digest.diff.compare`` is never handed the
run's floor, so a candidate dropped by it renders as unexplained.

Everything else is "no longer qualifies", and the honest rendering says exactly that rather than
borrowing a reason from the corpus-wide counter. The counter is still recorded, because its
*delta* between nights is real information — it just belongs to the corpus, not to any one row.

## Two snapshots minutes apart are not identical, and that is not a bug

Measured over the four consecutive ``setups --list`` runs stamped ``2026-08-21T01:45-01:47Z``
in this file: 7 of the 42 keys common to both recorded a different ``price`` between runs, and
one candidate (``ALT`` short) left the qualified set between the first and second because its
live price crossed the stop. Runs 2, 3 and 4 were identical. So the population is stable in
practice, but a live-priced asset can genuinely change state inside a minute.

The consequence for a reader: **diff the nightly run against the previous nightly run, not the
last two rows in this file.** ``latest`` returns the most recent row, which during a day of
manual re-runs is another row from the same day. Anything comparing "last night to tonight" has
to select on the run date itself. A same-day comparison is not wrong, it just answers a
different question than the digest is asking.

## Reading these rows

The do-not-backfill rule ``oracle.decisions`` states applies here for the same reason: every
number is decision-time state, and a re-run yields today's value. A snapshot with a field
filled in after the fact is worse than one missing it.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.setups import SCORE_VERSION

# Beside the decisions sidecar: the population and the rulings drawn from it are two halves of
# one question and reading either without the other is what this file exists to stop.
DEFAULT_PATH = Path(__file__).resolve().parents[4] / "data" / "setups" / "queue.jsonl"

# What a candidate row is useless without: identity for the diff, and the two labels every
# renderer prints unconditionally. Everything else may legitimately be absent on an older row
# and is read with a default. Checked at ``load`` so readers downstream can index these three
# directly instead of each guessing which fields are safe.
REQUIRED_FIELDS = ("key", "asset", "direction")


def row(candidates, stats, *, as_of, run_at: str,
        min_score: float | None = None, tiers=None) -> dict:
    """One JSON-serializable snapshot of ``candidates`` — the qualified population.

    ``stats`` is the ``BuildStats`` the same run produced. Only the fields a later diff needs
    are projected out of it; the full audit trail stays where it was computed.

    ``min_score`` and ``tiers`` are the filters that *produced* this population. They are
    recorded because a candidate can leave the set because the gate moved rather than because
    anything about the candidate did, and a diff with no record of the gate would report a
    tightened filter as the corpus going quiet.
    """
    triggers = getattr(stats, "triggers", {}) or {}
    return {
        "run": run_at,
        "as_of": as_of.isoformat(),
        "filters": {
            "min_score": min_score,
            "tiers": list(tiers) if tiers else None,
        },
        # Which scale ``score`` is on, for the same reason the decisions sidecar carries it:
        # comparing scores across a re-weighting compares two different scales.
        "score_version": SCORE_VERSION,
        "population": {
            "qualified": len(candidates),
            "candidates": getattr(stats, "candidate_count", 0),
            "assets_priced": getattr(stats, "assets_priced", 0),
            "assets_unpriced": getattr(stats, "assets_unpriced", 0),
            "duplicate_zones": getattr(stats, "duplicate_zones", 0),
        },
        # Corpus-wide, not per-row. See the module docstring on what a diff may conclude.
        "rejections": dict(getattr(stats, "rejections", {}) or {}),
        "rows": [_entry(c, triggers.get(c.key)) for c in candidates],
    }


def _entry(candidate, trigger) -> dict:
    """One candidate, flattened to what the digest renders.

    Everything the digest would otherwise recompute is carried, because recomputing it
    tomorrow yields tomorrow's market, not the one this row was drawn from.
    """
    return {
        "key": candidate.key,
        "asset": candidate.asset,
        "direction": candidate.direction,
        "score": candidate.score,
        "entry": candidate.entry,
        "stop": candidate.stop,
        "target": candidate.target,
        "price": candidate.price,
        "reward_risk": candidate.reward_risk,
        "zone_timeframe": candidate.zone_timeframe,
        "agreement": candidate.agreement,
        # ``agreement`` is a bare count, so three people who last spoke in March render exactly
        # like three who spoke yesterday. The digest cannot recompute this: the dated views live
        # on the ``Candidate``, which is gone by the time it reads the snapshot.
        #
        # Newest rather than oldest, matching ``core.setups.collapse``'s own reasoning about
        # freshness — a zone one person called yesterday and three called last year is a live
        # idea with old corroboration, not a stale one.
        #
        # ``None`` and not ``""``. ``Candidate.newest_at`` returns an empty string when there
        # are no views, and an empty date read as an age renders as "today", which is the one
        # answer that is certainly wrong.
        "newest_at": candidate.newest_at or None,
        # **None means the trigger pass never ran for this candidate** — `--no-triggers`, or an
        # asset with no intraday series. It is NOT `no_zone_tag`, which is the pass running and
        # finding price never reached the zone. Collapsing the two would make the digest
        # announce an arrival for every candidate the first night triggers came back on.
        "trigger_state": trigger.state if trigger is not None else None,
    }


def append(path, snapshot: dict, warn=None) -> bool:
    """Add one snapshot. ``True`` if it was written.

    **Creates the parent directory**, unlike the vault mirror in ``execution.journal``. That
    refusal exists so an unmounted vault cannot be faked; this path is under ``data/``, a tree
    this repo owns and regenerates freely.

    Never raises. It runs inside ``setups``, which the nightly calls: losing a snapshot costs
    tomorrow's digest one section, while an exception here would cost the whole queue listing.
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snapshot) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        if warn is not None:
            warn(f"warning: could not record the queue snapshot to {target}: {exc}")
        return False
    return True


def load(path, warn=None) -> list[dict]:
    """Every snapshot on disk, oldest first.

    A missing file is the normal state before the first run, not an error. A corrupt line is
    skipped *loudly* — quietly dropping one shrinks a population count with no trace, which is
    the argument ``brain.stance_store.load_all_stances`` makes for the same choice.

    Rows are checked against ``REQUIRED_FIELDS`` here so every reader downstream may index them
    directly. Without this a *well-formed* row missing one field raised ``KeyError`` out of the
    middle of ``digest.diff.compare`` — a hard failure in a package whose whole contract is to
    degrade — while the malformed-line path beside it was carefully handled.
    """
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict] = []
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            snapshot = json.loads(line)
        except json.JSONDecodeError as exc:
            if warn is not None:
                warn(f"warning: skipped unreadable queue snapshot at {target.name}:{number}: "
                     f"{exc}")
            continue
        rows.append(_checked(snapshot, where=f"{target.name}:{number}", warn=warn))
    return rows


def _checked(snapshot: dict, *, where: str, warn=None) -> dict:
    """Drop candidate rows that cannot be read, keeping the snapshot itself.

    A row is dropped rather than the whole night, because one unreadable candidate should not
    cost the other forty. Dropping is reported: a silently shorter population reads as the
    corpus going quiet, which is a real event this must not imitate.
    """
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        return {**snapshot, "rows": []}
    keep = [r for r in rows if isinstance(r, dict) and all(r.get(f) for f in REQUIRED_FIELDS)]
    dropped = len(rows) - len(keep)
    if dropped and warn is not None:
        warn(f"warning: {dropped} candidate row(s) in {where} are missing one of "
             f"{', '.join(REQUIRED_FIELDS)} and were dropped")
    return {**snapshot, "rows": keep}


def latest(path, warn=None) -> dict | None:
    """The most recent snapshot, or ``None`` before the first run.

    ``None`` is the digest's bootstrap case and is not a failure — there is simply nothing to
    diff against yet.
    """
    rows = load(path, warn=warn)
    return rows[-1] if rows else None
