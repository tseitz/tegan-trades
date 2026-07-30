"""Which venue should this trade go to, and is the answer trustworthy? Pure.

Ranks venues for one candidate on one number — expected cost as a fraction of notional over the
hold — assembled from ``core.funding`` (carry) and ``core.gaps`` (overnight gap). Those two
modules exist so that this comparison is possible at all: before them the engine charged carry to
Hyperliquid and nothing to Alpaca, so every comparison pitted a priced venue against a free one
and the free one always won.

**THE MEDIAN TRADE IS A COIN FLIP, AND SAYING SO IS THIS MODULE'S MAIN JOB.** Measured 2026-07-29:
median gap cost over 21 sessions is 0.595% of notional against a median Hyperliquid long carry of
0.53%. Routing therefore earns nothing on the average candidate and a great deal on the tails —
``PLTR`` short is paid 0.32% on a perp against a 5.23% gap cost on Alpaca, and ``BE`` is free to
hold on Alpaca against 2.35% to carry on Hyperliquid. A router that reports a winner without
reporting the *margin* would present those two cases identically, which is worse than not routing.

**Cost ranks; capability gates.** Per the house rule, a continuum is scored and a rule or a
missing fact is a gate: cost decides the ordering, while "this venue does not list it", "spot
cannot short" and "this account cannot short" remove a venue from the ordering entirely. A gate is
never expressed as a large cost — that would let a big enough saving elsewhere buy past it.

**An unpriced term is not a free one**, which is the same discipline ``core.gaps`` enforces
internally. ``VenueQuote`` records which applicable terms had no number, ``total`` sums only what
is known, and a near-tie is broken by *evidence first* — the venue with fewer unknowns wins before
taste is consulted. Reading an unpriced term as zero is precisely the bug this module was built to
remove, so it must not reappear here one level up.

WHY THE TERMS ARE A TABLE. A term absent for a venue is not zero there, it does not exist as a
quantity: a perpetual has no overnight gap because it never closes, and an equity has no funding
because it is not a perpetual. Quoting one is refused rather than ignored — accepting a gap on a
24-hour instrument is exactly how ``YM`` came to be charged 6.33% of notional (see
``scripts/probe_gap_cost.py``).
"""
from __future__ import annotations

from dataclasses import dataclass

ALPACA = "alpaca"
HYPERLIQUID = "hyperliquid"
KRAKEN = "kraken"

# Every cost term, in the order a quote reports them.
ALL_TERMS = ("carry", "gap", "crossing", "fee")

# Which terms exist at each venue. One table rather than a condition repeated in four places —
# the same reason ``execution/venues.py`` exists.
#
#   alpaca       cash equity/ETF. Gaps overnight; commission-free, so no fee term.
#   hyperliquid  perpetual. Funds continuously and never closes, so it cannot gap.
#   kraken       crypto spot. 24/7 so no gap, not a perpetual so no funding, but a real taker fee.
TERMS: dict[str, frozenset[str]] = {
    ALPACA: frozenset({"gap", "crossing"}),
    HYPERLIQUID: frozenset({"carry", "crossing"}),
    KRAKEN: frozenset({"crossing", "fee"}),
}

# Venues that can only be long. Kraken spot holds the asset, and US margin is closed to retail
# (§30), so a short is not a worse trade there — it is not a trade there.
LONG_ONLY = frozenset({KRAKEN})

# How large a cost advantage has to be before the ordering is worth believing, as a fraction of
# notional over the hold.
#
# 10bp, and it is a statement about measurement error rather than about preference.
# ``scripts/probe_book_depth.py`` records slippage rankings surviving a re-measure while the
# *magnitudes* moved several bp within minutes; gap estimates are noisier still, since 13 of 18
# Alpaca-listed assets lean more than half on the pooled rate. 10bp is a few multiples of that
# instrument noise and still an order of magnitude under the ~0.55% median total cost, so it
# catches genuine ties without swallowing the tail differences that are the whole point.
NOISE_FLOOR = 0.001

# The order to prefer when cost cannot decide. Hyperliquid last, and the reason is the one term
# that never appears in any quote: its §1.5 Restricted-Persons exposure is real, unpriced, and
# therefore can only ever act as a tie-break. On a genuine tie the venue carrying an unpriced
# legal risk is the worse of two equals; when it is actually cheaper by more than NOISE_FLOOR it
# still wins, which is the decision that was taken.
TIE_PREFERENCE = (ALPACA, KRAKEN, HYPERLIQUID)

REFUSAL_LONG_ONLY = "long_only"
REFUSAL_CANNOT_SHORT = "cannot_short"
REFUSAL_UNPRICED = "unpriced"

# Distinct from ``REFUSAL_CANNOT_SHORT`` on purpose, and both still refuse. "This account cannot
# short" is a measured fact; "nobody has asked the account yet" is an absence, and it resolves the
# moment a session exists. The queue runs free and local with no credentials, so it can only ever
# be in the second state — printing the first there would assert a stale measurement as current,
# and Alpaca has already been seen reporting `no_shorting: false` while `shorting_enabled` was
# false, so the two genuinely disagree and the difference is worth a word.
REFUSAL_SHORT_UNKNOWN = "short_unknown"

SIDES = ("long", "short")


@dataclass(frozen=True, slots=True)
class Refused:
    """A venue removed from the ordering, and why. Carried rather than dropped so the gap stays
    visible in the queue — §30's one surviving recommendation."""

    venue: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class VenueQuote:
    """What one venue would charge to hold this trade for the hold, in fractions of notional.

    Every term is ``None`` when unmeasured and never 0.0, because those mean different things and
    conflating them is the defect this whole slice exists to remove. Negative is a credit — a
    short on a perp is *paid* funding, and has to be able to rank below free.
    """

    venue: str
    symbol: str
    carry: float | None = None
    gap: float | None = None
    crossing: float | None = None
    fee: float | None = None
    borrowed: tuple[str, ...] = ()

    def _applicable(self) -> tuple[str, ...]:
        return tuple(t for t in ALL_TERMS if t in TERMS[self.venue])

    @property
    def priced(self) -> tuple[str, ...]:
        return tuple(t for t in self._applicable() if getattr(self, t) is not None)

    @property
    def unpriced(self) -> tuple[str, ...]:
        """Applicable terms with no number. Never empty-by-omission — see ``decide``, which
        prefers the better-evidenced venue on a near-tie."""
        return tuple(t for t in self._applicable() if getattr(self, t) is None)

    @property
    def is_priced(self) -> bool:
        """Whether anything at all is known about this venue's cost.

        A quote with nothing priced totals 0.0, which would make it the cheapest venue on the
        board — "unmeasured" beating "measured and cheap" is the precise bug this slice exists to
        remove, and it reappears here unless ``decide`` gates on this.
        """
        return bool(self.priced)

    @property
    def total(self) -> float:
        """Sum of the known terms — a LOWER BOUND whenever ``unpriced`` is non-empty.

        Which is why ``Decision.decisive`` refuses to trust a margin whose winner is less
        well-evidenced than its runner-up: an unpriced term can only add cost, so a thinly
        quoted venue's advantage may be an artefact of what was not counted.
        """
        return sum(getattr(self, t) for t in self.priced)

    @property
    def evidence(self) -> int:
        """How poorly evidenced this quote is, lower being better. Zero is fully measured.

        One ordinal rather than two counts, so ``Decision.decisive`` compares quotes on a single
        scale and a new kind of weak evidence can join it without another branch there. An
        unpriced term scores worse than a borrowed one because it is unbounded — a missing number
        can be anything, while a pooled one is at least the right order of magnitude.
        """
        return 2 * len(self.unpriced) + len([t for t in self.borrowed if t in self.priced])

    @property
    def dominant(self) -> str | None:
        """The term that decided this quote, by magnitude so a large credit can dominate.

        ``None`` when nothing is priced *and* when everything priced is zero — a free venue was
        not decided by any term, and naming one ("alpaca costs 0.00% · gap") reads as though a
        gap cost were being charged when the measurement is that there is none.
        """
        priced = self.priced
        if not priced:
            return None
        largest = max(priced, key=lambda t: abs(getattr(self, t)))
        return largest if getattr(self, largest) != 0 else None


@dataclass(frozen=True, slots=True)
class Decision:
    """The ordering, what it cost, and how much of it to believe."""

    asset: str
    direction: str
    ranked: tuple[VenueQuote, ...]
    refused: tuple[Refused, ...]
    tie_break: str | None

    @property
    def winner(self) -> VenueQuote | None:
        return self.ranked[0] if self.ranked else None

    @property
    def runner_up(self) -> VenueQuote | None:
        return self.ranked[1] if len(self.ranked) > 1 else None

    @property
    def margin(self) -> float | None:
        """What the winner saves against the runner-up. ``None`` when there is no contest."""
        if self.runner_up is None or self.winner is None:
            return None
        return self.runner_up.total - self.winner.total

    @property
    def decisive(self) -> bool:
        """Whether the margin is large enough to be worth believing — see ``NOISE_FLOOR``.

        A sole reachable venue is decisive: there is nothing to be wrong about in the ordering.

        **Evidence parity is required as well as size**, on the scale in ``VenueQuote.evidence``.
        ``total`` is a lower bound when terms are unpriced, so a winner carrying more unknowns than
        its runner-up may be ahead only because less of its cost was counted — its missing term can
        only add. A mostly-pooled term is the same problem in a weaker form: the number is real but
        it is the cohort's, so it can move when the asset's own history fills in. No margin is
        trustworthy while the winner is the less-evidenced side, however wide it looks.
        """
        if self.winner is None:
            return False
        if self.runner_up is None:
            return True
        margin = self.margin
        if margin is None or margin < NOISE_FLOOR:
            return False
        return self.winner.evidence <= self.runner_up.evidence


def quote(venue: str, symbol: str, *, borrowed: tuple[str, ...] = (),
          **terms: float | None) -> VenueQuote:
    """A quote for ``venue``, refusing any term that venue does not have.

    Refuses rather than ignores: a caller passing ``gap`` for a perpetual has a broken cost model,
    and silently dropping it would hide that while producing a plausible number. Validation is on
    the term *names* offered, whether or not their value is None — a caller naming a term the
    venue does not have is confused either way, and only checking valued ones made the guard
    weaker than this docstring claims.

    ``borrowed`` names priced terms whose number is mostly a cohort's rather than this
    instrument's — see ``core.gaps.GapCost.borrowed``. It is not a lesser kind of ``None``: the
    number is real and usable, it just carries less evidence, and ``Decision.decisive`` weighs it
    accordingly rather than ignoring it.
    """
    if venue not in TERMS:
        raise ValueError(f"unknown venue {venue!r}; expected one of {sorted(TERMS)}")
    named = set(terms)
    if unknown := named - set(ALL_TERMS):
        raise ValueError(f"unknown cost term(s) {sorted(unknown)}")
    if wrong := named - TERMS[venue]:
        raise ValueError(
            f"{venue} has no {sorted(wrong)} term — it does not exist as a quantity there, "
            f"which is not the same as being zero"
        )
    if stray := set(borrowed) - TERMS[venue]:
        raise ValueError(f"{venue} has no {sorted(stray)} term to borrow")
    return VenueQuote(venue=venue, symbol=symbol, borrowed=tuple(borrowed), **terms)


def direction_refusal(venue: str, direction: str, *,
                      can_short: bool | None = None) -> Refused | None:
    """Whether ``venue`` can take this direction at all. A gate, so it never becomes a cost.

    ``can_short`` is the account's own answer and defaults to refusing. An unknown capability is
    a missing fact, and the venue rejects those at the open hours after the order was logged as
    placed — see the Alpaca hazards on ``shorting_enabled``. Refusing locally is the cheap half.
    """
    if direction not in SIDES:
        raise ValueError(f"direction must be one of {SIDES}, got {direction!r}")
    if direction == "long":
        return None
    if venue in LONG_ONLY:
        return Refused(venue, REFUSAL_LONG_ONLY,
                       f"{venue} is spot and long only — a short is not a trade there")
    if venue == ALPACA and not can_short:
        if can_short is False:
            return Refused(venue, REFUSAL_CANNOT_SHORT,
                           "account reports shorting_enabled false")
        return Refused(venue, REFUSAL_SHORT_UNKNOWN,
                       "account not checked for shorting_enabled, which is not a yes")
    return None


def decide(asset: str, direction: str, quotes, *,
           can_short: bool | None = None, floor: float = NOISE_FLOOR) -> Decision:
    """Rank ``quotes`` for one candidate, gating on direction first.

    Only venues that were quoted are considered — a venue absent from ``quotes`` is one the
    caller found no listing for, and absence is a real answer (``cfg/venue_map.yaml``'s rule).
    """
    if direction not in SIDES:
        raise ValueError(f"direction must be one of {SIDES}, got {direction!r}")

    allowed: list[VenueQuote] = []
    refused: list[Refused] = []
    for candidate in quotes:
        blocked = direction_refusal(candidate.venue, direction, can_short=can_short)
        if blocked:
            refused.append(blocked)
        elif not candidate.is_priced:
            # A missing fact is a gate, not a cheap score. Ranking an entirely unpriced venue
            # would put it first at a total of 0.0 — measured live on 2026-07-29, a funding
            # lookup miss sent 11 candidates to Hyperliquid "for free".
            refused.append(Refused(
                candidate.venue, REFUSAL_UNPRICED,
                f"nothing priced for {candidate.venue} "
                f"({', '.join(candidate.unpriced)} unmeasured), so it cannot be ranked"))
        else:
            allowed.append(candidate)

    # Cost first, then evidence, then the stated preference. Sorting on the full key rather than
    # sorting by cost and patching ties afterwards keeps the tie-break honest when three venues
    # are involved and only two of them tie.
    ranked = tuple(sorted(
        allowed,
        key=lambda q: (q.total, q.evidence, TIE_PREFERENCE.index(q.venue)),
    ))

    return Decision(
        asset=asset,
        direction=direction,
        ranked=ranked,
        refused=tuple(refused),
        tie_break=_tie_break(ranked, floor),
    )


def _tie_break(ranked: tuple[VenueQuote, ...], floor: float) -> str | None:
    """Which rule actually chose the winner, when cost did not.

    Reported because "Alpaca, by 5% of notional" and "Alpaca, because Hyperliquid carries an
    unpriced legal risk and the costs were level" are different sentences to a person deciding,
    and only one of them is a reason to change anything.
    """
    if len(ranked) < 2:
        return None
    winner, runner_up = ranked[0], ranked[1]
    if runner_up.total - winner.total >= floor:
        return None
    if winner.evidence != runner_up.evidence:
        return "evidence"
    return "preference"
