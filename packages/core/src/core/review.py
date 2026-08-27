"""What to do about a position you already hold.

``core.setups`` answers *should I open this*. This answers *should I keep it*, and the two
are not the same question asked twice. Opening a trade is optional, so that engine refuses
everything it cannot fully justify — an unaligned weekly, a premium entry, a thin
reward-to-risk. A position you already own has no such luxury: doing nothing is itself a
decision, and it gets made whether or not the evidence is tidy. So this module reports where
the evidence is thin instead of dropping the row.

Two readings meet here and neither one dominates:

- **The roster** — where the people you follow currently stand on the asset. Counted from
  folded stances, so it is one vote per person, not per statement.
- **The chart** — where price sits on the weekly. Cheap end, expensive end, or neither.

A bearish roster at weekly support is not a sell; it is a fact to hold in mind. The same
roster at weekly resistance is. That asymmetry is the whole point of pairing them, and it
lives in ``VERDICTS`` where it can be read in one glance.

Pure and duck-typed like the rest of ``core``: no I/O, no network, nothing mutated. The
caller supplies a ``Context`` (from ``core.setups.build_context``) and folded stances (from
``brain.retrieve.fold_stances``); this module never learns where either came from.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from core.rank import parse_date
from core.setups import ARRIVAL, PROXIMITY_SPAN, WEEKLY, Context, Zone, approach_to
from core.structure import BULLISH

# Where price sits on the weekly.
AT_SUPPORT = "at_support"
AT_RESISTANCE = "at_resistance"
MID = "mid"
# Price has left the range entirely. Kept as two locations rather than folded into
# `UNREADABLE`, because which side it left on is the fact — and the common one. Measured over
# the cached corpus: 105 of 360 assets had no position *in* their range, and 81 of those were
# above it. That is not an edge case, it is what a holding in an uptrend looks like, and
# refusing to read it would leave a long-horizon account silent about most of itself.
ABOVE_RANGE = "above_range"
BELOW_RANGE = "below_range"
# No range at all — never enough structure to draw one. Genuinely no reading, and distinct
# from the two above: there the range is stale and wants redrawing, here there is none.
UNREADABLE = "unreadable"

# Where the roster sits. These are the *stance* vocabulary (`core.stance.Lean`), and they
# deliberately keep their own names even though "bullish" and "bearish" are spelled the same
# way in `core.structure`. Over there the word describes an order block's direction; here it
# describes a person's opinion. Sharing one constant would make a future rename of either
# silently retype the other.
BULLISH_ROSTER = "bullish"
BEARISH_ROSTER = "bearish"
MIXED = "mixed"
SILENT = "silent"

# What to do about it. `NO_READ` is the verdict twin of the `UNREADABLE` *location* above,
# and they are two constants on purpose: one describes the chart, the other describes the
# advice. Reusing one string for both let a location value print in the verdict column, in
# its own casing, looking like a fourth kind of answer.
NO_READ = "NO_READ"
ADD = "ADD"
HOLD = "HOLD"
TRIM = "TRIM"
WATCH = "WATCH"
NO_VIEW = "NO_VIEW"

# The outer quarter of the weekly range at each end. Deliberately stricter than
# `DealingRange.zone_at`, which splits at 0.5: that split answers the manifesto's permission
# question ("is a long even allowed here"), and "price is at resistance" is a much stronger
# claim than "price is not cheap". At 0.5 every holding one tick above the midpoint would
# read as a place to sell.
DISCOUNT_EDGE = 0.25
PREMIUM_EDGE = 0.75

# The grid. Read a row as "the roster says X"; read a column as "price is at Y".
#
# The two HOLDs on the bullish row are not the same HOLD as the bearish one, and that is
# intentional rather than a shortcut. A bullish roster into resistance is a real tension —
# but trimming a position your people still like, because price is doing exactly what they
# said it would, is selling the thesis you are being paid for. Only a bearish roster turns
# resistance into an exit.
VERDICTS: dict[tuple[str, str], str] = {
    (BULLISH_ROSTER, AT_SUPPORT): ADD,
    (BULLISH_ROSTER, MID): HOLD,
    (BULLISH_ROSTER, AT_RESISTANCE): HOLD,
    (BEARISH_ROSTER, AT_SUPPORT): HOLD,
    (BEARISH_ROSTER, MID): WATCH,
    (BEARISH_ROSTER, AT_RESISTANCE): TRIM,
    (MIXED, AT_SUPPORT): WATCH,
    (MIXED, MID): WATCH,
    (MIXED, AT_RESISTANCE): WATCH,
    # Above the range there is nothing overhead and the old range is stale. A roster turning
    # bearish while you sit on an extended gain is the strongest trim there is; a roster still
    # bullish is simply being proved right, so it holds.
    (BULLISH_ROSTER, ABOVE_RANGE): HOLD,
    (BEARISH_ROSTER, ABOVE_RANGE): TRIM,
    (MIXED, ABOVE_RANGE): WATCH,
    # Below the range the structure that held the position is gone. **Never ADD here**, not
    # even on a bullish roster: buying more of a position whose range has just broken is
    # averaging into a thesis the chart is actively arguing with. That refusal is the only
    # reason this column is not simply a mirror of the one above.
    (BULLISH_ROSTER, BELOW_RANGE): WATCH,
    (BEARISH_ROSTER, BELOW_RANGE): WATCH,
    (MIXED, BELOW_RANGE): WATCH,
}

# How many people must be on the winning side before a verdict is allowed to ask you to move
# money. Measured on the real 77-position account: 5 of 11 actionable calls rested on a single
# voice, and one of those — a TRIM — was a single 30-day-old statement. A grid with no floor
# lets one person outvote an empty room, and the roster's whole value is that it is a roster.
#
# **This is a gate, not a score.** How many people agree is a continuum and already scored
# elsewhere (`core.rank.agreement_signal`); "do not act on one opinion" is a rule, so it is
# gated. The thin view is still counted, still shown, and still explained — it just comes back
# as WATCH instead of ADD or TRIM.
MIN_VOICES = 2

# Leans that pick a side. `neutral` ("it goes sideways") and `uncertain` ("I have no idea")
# both exist in the corpus and neither one tells you what to do, so they count toward how
# many people spoke without counting toward which way.
DIRECTIONAL = (BULLISH_ROSTER, BEARISH_ROSTER)


class _StanceLike(Protocol):
    lean: str
    source: object          # needs `.published_at`


class _FoldedLike(Protocol):
    """``brain.retrieve.FoldedStance``, structurally. Duck-typed rather than imported
    because ``brain`` imports ``core`` and not the other way round."""
    person_canonical: str
    current: _StanceLike


@dataclass(frozen=True, slots=True)
class Holding:
    """One line of a portfolio. ``cost`` is the average price paid, and it is optional —
    profit and loss is reporting, not input to any verdict, so a portfolio file with share
    counts alone is fully usable."""
    ticker: str
    shares: float
    cost: float | None = None


@dataclass(frozen=True, slots=True)
class RosterLean:
    """Where the people stand, and how much that reading is worth.

    ``age_days`` is reported rather than gated on. A six-month-old bullish call is weaker
    than a fresh one, but *how much* weaker depends on the speaker's horizon, and silently
    discarding it would turn a stale view into an absent one — the exact conflation
    ``SILENT`` exists to prevent.
    """
    lean: str
    bulls: int
    bears: int
    people: int
    newest: date | None
    age_days: int | None
    voices: tuple[str, ...]        # who is on the winning side, sorted
    # Fewer than ``MIN_VOICES`` people behind a directional read. Carried rather than folded
    # into ``lean``, because the lean is genuinely bullish or bearish and saying otherwise
    # would be a lie about what the corpus holds. Only the *verdict* softens.
    thin: bool = False


@dataclass(frozen=True, slots=True)
class Location:
    """Where price sits on the weekly, and which reading said so.

    ``basis`` is carried because the two paths mean different things to a human: a zone is a
    specific level someone can go look at, while a range position is a statement about the
    whole leg. A renderer that printed only ``where`` would flatten them.
    """
    where: str
    basis: str                     # "zone" | "range" | "outside" | "none"
    zone: Zone | None = None
    position: float | None = None  # 0.0 at the range low, 1.0 at the high


@dataclass(frozen=True, slots=True)
class Reading:
    """One holding, both readings, and the call."""
    holding: Holding
    roster: RosterLean
    location: Location
    verdict: str
    price: float | None
    weekly_trend: str | None

    @property
    def market_value(self) -> float | None:
        if self.price is None:
            return None
        return self.price * self.holding.shares

    @property
    def pnl(self) -> float | None:
        if self.price is None or self.holding.cost is None:
            return None
        return (self.price - self.holding.cost) * self.holding.shares


def roster_lean(folded, *, as_of: date) -> RosterLean:
    """Fold a person's current stances into one directional reading for the asset.

    Counts **people, not statements** — mirroring ``brain.retrieve.summarize_split``, and for
    the same reason: one voice restating a view twenty times is still one voice, and counting
    statements would let a single prolific feed outvote everyone else.

    One person can land on both sides, which is real and not a bug: folding keys on
    ``(person, asset, horizon)``, so someone bullish on the swing and bearish on the macro
    contributes to both tallies. Flattening that would invent a conviction they never had.
    """
    by_lean: dict[str, set[str]] = {}
    everyone: set[str] = set()
    dates: list[date] = []
    for item in folded:
        person = item.person_canonical
        everyone.add(person)
        by_lean.setdefault(item.current.lean, set()).add(person)
        when = parse_date(getattr(item.current.source, "published_at", None))
        if when is not None:
            dates.append(when)

    bulls = len(by_lean.get(BULLISH_ROSTER, ()))
    bears = len(by_lean.get(BEARISH_ROSTER, ()))
    if bulls > bears:
        lean, winners = BULLISH_ROSTER, by_lean.get(BULLISH_ROSTER, set())
    elif bears > bulls:
        lean, winners = BEARISH_ROSTER, by_lean.get(BEARISH_ROSTER, set())
    elif bulls == 0:
        lean, winners = SILENT, set()
    else:
        lean, winners = MIXED, by_lean.get(BULLISH_ROSTER, set()) | by_lean.get(
            BEARISH_ROSTER, set())

    newest = max(dates) if dates else None
    return RosterLean(
        lean=lean,
        bulls=bulls,
        bears=bears,
        people=len(everyone),
        newest=newest,
        age_days=None if newest is None else (as_of - newest).days,
        voices=tuple(sorted(winners)),
        thin=lean in DIRECTIONAL and len(winners) < MIN_VOICES,
    )


def locate(context: Context, *, span: float = PROXIMITY_SPAN) -> Location:
    """Where price sits on the weekly: at an edge, or in between.

    A **live weekly order block price has actually reached** outranks the range position,
    because it is the more specific fact — "price is sitting on the block that created this
    leg" beats "price is somewhere in the lower quarter". Arrival uses ``ARRIVAL`` on
    ``approach_to``, the same threshold the setups queue uses to decide price has come to a
    zone, so the two can never drift into disagreeing about what "reached" means.

    **Weekly zones only.** A daily block price happens to sit in says nothing about whether
    the weekly is cheap, and letting it vote would make a long-horizon review react to
    exactly the noise it exists to ignore.
    """
    reached: list[tuple[float, Zone]] = []
    for zone in context.zones:
        if zone.timeframe != WEEKLY:
            continue
        approach = approach_to(zone.block, context.price, span=span)
        if approach >= ARRIVAL:
            reached.append((approach, zone))

    if reached:
        _, zone = max(reached, key=lambda pair: pair[0])
        where = AT_SUPPORT if zone.block.kind == BULLISH else AT_RESISTANCE
        return Location(where=where, basis="zone", zone=zone)

    span_read = context.dealing_range
    if span_read is None:
        return Location(where=UNREADABLE, basis="none")

    # `position_at` refuses outside the range rather than clamping, because a clamp would
    # report a price below the low as a perfect discount. That refusal is right; discarding
    # the row is not. Which side price left on is carried, and no percentage is invented for
    # it — there is no honest position *in* a range price is no longer inside.
    position = span_read.position_at(context.price)
    if position is None:
        side = ABOVE_RANGE if context.price > span_read.high else BELOW_RANGE
        return Location(where=side, basis="outside")
    if position <= DISCOUNT_EDGE:
        where = AT_SUPPORT
    elif position >= PREMIUM_EDGE:
        where = AT_RESISTANCE
    else:
        where = MID
    return Location(where=where, basis="range", position=position)


def verdict_for(lean: str, where: str, *, thin: bool = False) -> str:
    """The grid, plus the two refusals that sit in front of it.

    Order matters. ``SILENT`` is checked first because it is the more useful thing to print:
    with no view from the roster there is nothing to act on however clean the chart is, so
    reporting a chart problem instead would answer a question nobody asked.

    An unreadable chart returns ``UNREADABLE`` rather than falling back to ``HOLD``. They
    look alike — you end up doing nothing either way — but ``HOLD`` is advice and this is a
    gap in the evidence. Dressing one as the other is how a stale price feed comes out
    looking like a considered decision.
    """
    if lean == SILENT:
        return NO_VIEW
    if where == UNREADABLE:
        return NO_READ
    verdict = VERDICTS.get((lean, where), WATCH)
    # Only the two that ask you to move money soften. HOLD and WATCH already ask for nothing,
    # and downgrading them would add noise without removing a single risk.
    if thin and verdict in (ADD, TRIM):
        return WATCH
    return verdict


def review(holding: Holding, context: Context | None, *, folded, as_of: date) -> Reading:
    """One holding's full reading. ``context is None`` when the asset could not be priced —
    reported as ``UNREADABLE`` rather than skipped, because a holding that silently vanishes
    from a portfolio review is worse than one you cannot value."""
    roster = roster_lean(folded, as_of=as_of)
    location = Location(where=UNREADABLE, basis="none") if context is None else locate(context)
    return Reading(
        holding=holding,
        roster=roster,
        location=location,
        verdict=verdict_for(roster.lean, location.where, thin=roster.thin),
        price=None if context is None else context.price,
        weekly_trend=None if context is None else context.weekly_trend,
    )
