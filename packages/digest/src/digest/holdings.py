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
    standing: dict[str, int] = field(default_factory=dict)
    positions: int = 0
    # Named rather than counted. A holding with no price gets no verdict, and a section that
    # merely omitted it would describe a portfolio you do not own.
    unpriced: tuple[str, ...] = ()
    bootstrap: bool = False

    @property
    def is_quiet(self) -> bool:
        return not self.changed


def _loud(verdict: str | None) -> str | None:
    """A verdict reduced to what a diff cares about. Everything quiet collapses to None, so
    ``HOLD`` -> ``WATCH`` and ``WATCH`` -> ``NO_VIEW`` compare equal and never report."""
    return verdict if verdict in LOUD else None


def delta(portfolio: str, readings, remembered: dict[str, str]) -> HoldingsDelta:
    """Tonight's movement for one account.

    ``remembered`` is last night's ``{ticker: verdict}``. Empty means a first run — every
    action today is reported, and ``bootstrap`` says why so the reader can tell "eight new
    calls" from "we have never looked before".
    """
    changed: list[Change] = []
    standing: dict[str, int] = {}
    unpriced: list[str] = []

    for reading in readings:
        ticker = reading.holding.ticker
        standing[reading.verdict] = standing.get(reading.verdict, 0) + 1
        if reading.price is None:
            unpriced.append(ticker)
        before = remembered.get(ticker)
        if _loud(before) != _loud(reading.verdict):
            changed.append(Change(ticker=ticker, before=before, reading=reading))

    return HoldingsDelta(
        portfolio=portfolio,
        changed=tuple(changed),
        standing=standing,
        positions=len(readings),
        unpriced=tuple(unpriced),
        bootstrap=not remembered,
    )


def remember(readings) -> dict[str, str]:
    """Tonight's answer for **every** ticker, not only the loud ones.

    Storing actions alone would leave a quiet position with no entry, so tomorrow it would
    read as new to the file — and every routine HOLD-to-ADD move would print as an arrival
    rather than as the turn it actually is.
    """
    return {r.holding.ticker: r.verdict for r in readings}
