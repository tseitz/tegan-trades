"""What changed about the positions you already hold. Pure.

``review`` answers the standing question — here is your whole account and what to do about
each line. This package cannot print that: a digest of 77 rows every morning is a report, and
a report nobody reads is worse than no section at all. So this reduces the same readings to
the one thing a nightly can honestly carry: **which verdicts moved since last night.**

The reduction is deliberately harsh. Only ``ADD`` and ``TRIM`` count as movement, because they
are the only two that ask you to move money. ``HOLD``, ``WATCH`` and ``NO_VIEW`` all mean "do
nothing", and a position drifting between them asks nothing of you — reporting those would put
three rows a night above the one that mattered. ``WATCH`` still shows in the standing counts,
which is where "keep an eye on this" belongs.

A verdict that has not moved is silent, however loud it is. A ``TRIM`` standing for its fifth
night is not news, and reprinting it nightly is exactly what trains the eye to skip the
section. The standing count is what keeps it visible after its paragraph stops.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.review import ADD, TRIM

#: The verdicts that ask you to move money, and therefore the only ones a diff reports on.
LOUD = (ADD, TRIM)


@dataclass(frozen=True, slots=True)
class Change:
    """One position whose verdict moved into, out of, or between the loud pair.

    ``before`` is None for a position that was not in last night's memory at all — a row you
    added to the file today. Distinct from a verdict that changed, and it has to stay that
    way: "new to the file" and "the roster turned" are different reasons to look.
    """
    ticker: str
    before: str | None
    reading: object            # core.review.Reading; duck-typed so this module stays pure


@dataclass(frozen=True, slots=True)
class HoldingsDelta:
    portfolio: str
    changed: tuple[Change, ...] = ()
    # Positions that started standing on a level since last night, and ones that stopped.
    # Kept apart from ``changed`` because they answer a different question: that one is "has
    # the advice moved", these are "has price arrived somewhere". A verdict can sit still for
    # weeks while price walks onto a weekly block, and vice versa.
    arrived: tuple = ()
    left: tuple[tuple[str, str], ...] = ()
    standing: dict[str, int] = field(default_factory=dict)
    positions: int = 0
    # Named rather than counted. A holding with no price gets no verdict, and a section that
    # merely omitted it would describe a portfolio you do not own.
    unpriced: tuple[str, ...] = ()
    bootstrap: bool = False
    # How old the hand-kept file is, and whether that is past what the account tolerates.
    # Carried on the delta rather than looked up at render time because this module is the
    # only one that has the `Portfolio` in scope, and the renderer stays pure.
    stale: bool = False
    age_days: int | None = None

    @property
    def is_quiet(self) -> bool:
        return not self.changed and not self.arrived and not self.left


def _loud(verdict: str | None) -> str | None:
    """A verdict reduced to what a diff cares about. Everything quiet collapses to None, so
    ``HOLD`` -> ``WATCH`` and ``WATCH`` -> ``NO_VIEW`` compare equal and never report."""
    return verdict if verdict in LOUD else None


def level_key(level) -> str:
    """A level's identity across nights: what kind it is and which side of price it is on.

    **Deliberately not its prices.** A zone's edges are redrawn as new bars close, so keying on
    them would report an arrival every single night for a position that has not moved an inch.
    Kind-and-side is the granularity a nightly can honestly carry: "it is on weekly resistance"
    is either true or it is not, and it changes when something actually happened.
    """
    return f"{level.kind}:{level.side}"


def remember_levels(on_levels) -> dict[str, str]:
    """``{ticker: level_key}`` for everything price is standing on tonight."""
    return {spot.reading.holding.ticker: level_key(spot.level) for spot in on_levels}


def delta(portfolio: str, readings, remembered: dict[str, str], *,
          on_levels=(), remembered_levels: dict[str, str] | None = None,
          stale: bool = False, age_days: int | None = None) -> HoldingsDelta:
    """Tonight's movement for one account.

    ``remembered`` is last night's ``{ticker: verdict}``. Empty means a first run — every
    action today is reported, and ``bootstrap`` says why so the reader can tell "eight new
    calls" from "we have never looked before".

    ``on_levels`` is the **uncapped** standing group from ``review.levels.shortlist``. It has
    to be uncapped: the display cap is about how much fits on a screen, and a level arrival
    that happened to rank thirteenth still happened.
    """
    changed: list[Change] = []
    counts: dict[str, int] = {}
    unpriced: list[str] = []

    for reading in readings:
        ticker = reading.holding.ticker
        counts[reading.verdict] = counts.get(reading.verdict, 0) + 1
        if reading.price is None:
            unpriced.append(ticker)
        before = remembered.get(ticker)
        if _loud(before) != _loud(reading.verdict):
            changed.append(Change(ticker=ticker, before=before, reading=reading))

    # None means no memory at all — a first run. Every holding is standing on something on
    # night one, and reporting all of them as arrivals would make a quiet Tuesday read as the
    # busiest night on record. An empty dict is different: it is a memory that happens to be
    # empty, and an arrival against it is real.
    now = {spot.reading.holding.ticker: spot for spot in on_levels}
    if remembered_levels is None:
        arrived, left = (), ()
    else:
        arrived = tuple(spot for ticker, spot in now.items()
                        if remembered_levels.get(ticker) != level_key(spot.level))
        left = tuple((ticker, was) for ticker, was in sorted(remembered_levels.items())
                     if ticker not in now)

    return HoldingsDelta(
        portfolio=portfolio,
        changed=tuple(changed),
        arrived=arrived,
        left=left,
        standing=counts,
        positions=len(readings),
        unpriced=tuple(unpriced),
        bootstrap=not remembered,
        stale=stale,
        age_days=age_days,
    )


def remember(readings) -> dict[str, str]:
    """Tonight's answer for **every** ticker, not only the loud ones.

    Storing actions alone would leave a quiet position with no entry, so tomorrow it would
    read as new to the file — and every routine HOLD-to-ADD move would print as an arrival
    rather than as the turn it actually is.
    """
    return {r.holding.ticker: r.verdict for r in readings}
