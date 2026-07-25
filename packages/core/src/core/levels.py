"""Reading a stated target out of a thesis's unlabeled bag of levels — or declining to.

``Thesis.key_levels`` is a list of floats with nothing distinguishing a trigger from a target
from a stop. A real corpus row: a HYPE long with ``[40, 50, 30, 21, 19, 20]``, where 40 is a
trigger, 50 a target and the rest downside. ``core.grade`` sidesteps this entirely by grading
on direction and horizon; this module extracts what it safely can.

**Only the target is read here, and that is a design consequence, not an omission.** Price
structure supplies the entry (the order-block retest zone) and the stop (the structural
invalidation level), so the only number a thesis can usefully contribute is where its author
thought price was going. Asking for less makes the problem tractable without a model.

**Abstention is a designed outcome, not a failure.** The rule is "if they call something,
listen; otherwise just go direction and conviction." A reading that declines is fully
successful — the caller falls back to structure for the target too. That is what keeps this
module deterministic, free, and honest.

**Several levels beyond entry resolve to the nearest, reported as ``NEAREST``.** Strict
abstention was tried first and measured: on the live corpus it forfeited **390 of 1,621 rows**,
holding stated coverage at 15% where taking the nearest reaches 39%. The nearest level is also
the most conservative reading — the smallest claim the author's own numbers support — and the
distinct ``NEAREST`` source keeps it auditable, so "did inferred targets do as well as clean
ones" stays a measurable question rather than a buried assumption.

The reference price is what turns an unordered bag into a signed one. At a reference of 45 the
HYPE bag has exactly one level above it and resolves cleanly; at 35 it has two and cannot.
Same data, different answer — which is why the reference price is a required argument rather
than something inferred here.
"""
from __future__ import annotations

from dataclasses import dataclass

# Successful reads. Two sources, kept distinct so the inferred ones stay auditable.
STATED = "stated"      # exactly one level beyond entry — unambiguous
NEAREST = "nearest"    # several beyond entry; the closest one, conservatively

# Abstention reasons. Distinct rather than a single "unknown" so a corpus-wide tally shows
# *why* coverage is what it is — whether levels are missing, one-sided, or undirected tells
# you different things about the roster.
NO_LEVELS = "no_levels"
NO_REFERENCE = "no_reference_price"
NO_TARGET_SIDE = "no_level_beyond_entry"
UNDIRECTED = "undirected"

_PROFIT_SIDE = {"long": 1, "short": -1}


@dataclass(frozen=True, slots=True)
class TargetReading:
    """A stated target, or the reason there isn't one.

    ``abstained`` is derived rather than stored: two fields that can contradict each other
    are a bug waiting to happen, and the absence of a target *is* the abstention.
    """
    target: float | None
    source: str

    @property
    def abstained(self) -> bool:
        return self.target is None


def read_target(row, reference_price: float | None) -> TargetReading:
    """The target ``row``'s author stated, if exactly one level lies beyond entry.

    ``row`` is duck-typed on ``direction`` and ``key_levels``, like everything else in
    ``core``. ``reference_price`` is the asset's price when the call was published.

    A level *at* the reference price is not a target — it is where price already is, so it
    states no expectation of movement.
    """
    levels = sorted({float(level) for level in getattr(row, "key_levels", None) or ()})
    if not levels:
        return TargetReading(target=None, source=NO_LEVELS)

    # Checked after the levels so a thesis with no levels reports that rather than blaming a
    # missing price — the two say different things about why coverage is short.
    if reference_price is None:
        return TargetReading(target=None, source=NO_REFERENCE)

    sign = _PROFIT_SIDE.get(getattr(row, "direction", ""))
    if sign is None:
        # "neutral" has no profit side by definition, and an unrecognised direction must fail
        # closed rather than default to long.
        return TargetReading(target=None, source=UNDIRECTED)

    beyond = [level for level in levels if (level - reference_price) * sign > 0]
    if not beyond:
        return TargetReading(target=None, source=NO_TARGET_SIDE)
    if len(beyond) == 1:
        return TargetReading(target=beyond[0], source=STATED)

    # Several candidates — typically a trigger and a target, or a ladder. The nearest is the
    # smallest claim their own numbers support, so it is the conservative pick, and NEAREST
    # marks it as inferred rather than clean.
    nearest = min(beyond, key=lambda level: abs(level - reference_price))
    return TargetReading(target=nearest, source=NEAREST)
