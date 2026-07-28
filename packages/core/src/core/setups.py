"""Cross-reference: a roster thesis plus price structure becomes a setup candidate, or doesn't.

This module is the manifesto encoded, and the line between what it **gates** and what it
**scores** is the load-bearing decision here:

- **Gate a rule you wrote, or a fact that is missing.** Rule 8 says "the macro is much
  stronger", so a signal against the higher timeframe is discarded, not ranked lower. A
  candidate with no target or no invalidation cannot be scored down into existence either.
- **Score a measurement on a continuum.** Age is the example: a view one day past its window
  is not categorically different from one a day inside it, and the cliff that pretended
  otherwise was the largest filter in the system (2,913 of 3,459 live rejections) while being
  unfalsifiable, since the gate that would generate evidence about it was the gate under test.

The counter-example is deliberate and worth keeping in mind before softening anything else:
``min_reward_risk`` *was* scored, and it was measured and hardened — a 0.32-RR candidate still
surfaced mid-list, and a queue padded with those feeds the stated leak "I take way too many
trades". Softening is not free; it is right for measurements and wrong for rules.

**Rejections are a sum type, not a filter.** ``NotASetup`` carries the thesis identity and a
reason, mirroring ``core.grade``'s ``Pending``/``Ungradeable`` and the oracle's
explicitly-categorized unpriceable assets. Silently dropping a thesis would make "the gates
produce nothing" indistinguishable from "the gates are too tight", and a corpus-wide tally of
reasons is the only way to tell those apart.

**Why the split into ``Context`` and ``cross_reference``.** Structure is a property of an asset
at a date; a thesis is a claim about it. Computing structure once per asset and passing it in
keeps the gating logic — the part carrying all the judgment — testable without hundred-bar
fixtures, and avoids re-deriving swings for every one of the many theses on the same asset.

**Where each number comes from.** Entry is the order-block retest zone; the stop is the
structural invalidation level, so no candidate can exist without one — that is the guardrail
for the stated leak "I stay in way too long with losers", and it also satisfies "never put on a
trade without knowing where you're exiting in both directions" without inventing a stop rule.
The target prefers what the author actually said and falls back to structure, recording which
was used so "do their targets beat the structural level" stays measurable rather than becoming
a permanent assumption.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from core.dealing_range import DealingRange
from core.dealing_range import dealing_range as resolve_dealing_range
from core.imbalance import atr
from core.levels import STATED, read_target
from core.funding import FundingOutlook, carry_adjusted_rr
from core.rank import agreement_signal, parse_date
from core.structure import (
    BEARISH,
    BULLISH,
    DOWNTREND,
    DOWNTREND_FAILED_BREAKDOWN,
    SWING_WIDTH,
    UPTREND,
    UPTREND_FAILED_BREAKOUT,
    OrderBlock,
    invalidated_on,
    order_blocks,
    trend_state,
)

# Target source, alongside core.levels.STATED.
STRUCTURAL = "structural"

# Which bar series a zone's structure was read from.
#
# Daily-only selection is systematically biased toward tight, near-price zones, and the bias is
# structural rather than incidental: ``SWING_WIDTH`` is 2, so daily breaks fire often, and
# ``_newest_zone`` takes the newest. On live GOOGL that produced a 5.51-wide zone
# (316.32–321.83) carrying a reward:risk of 14.19 — a number that high is a symptom of a broken
# denominator, not a good trade — while the weekly block price was actually drawn to sat at
# 273.95–305.98 and priced at 2.94.
#
# The two are **not competitors for one slot**. A weekly zone and a daily zone on the same asset
# are different setups with different risk, and each is judged and offered on its own.
DAILY = "daily"
WEEKLY = "weekly"
# Weekly first: "the macro is much stronger" already lets the weekly trend veto a direction in
# ``cross_reference``, and the same precedence orders the queue. See ``collapse``.
ZONE_TIMEFRAMES = (WEEKLY, DAILY)

# Refusals that describe a *zone* rather than the thesis that pointed at it.
#
# A thesis is cross-referenced once per zone timeframe, so these can legitimately fire more than
# once for one thesis — "no live weekly zone" and "no live daily zone" are two separate facts.
# Everything above them (direction, trend agreement, dealing range, dating) is decided from the
# thesis and the asset alone, so it can only be true once however many timeframes are tried. A
# tally that doesn't know the difference silently doubles ``weekly_disagrees`` for a reason that
# has nothing to do with zones.
ZONE_LEVEL_REASONS = frozenset({
    "no_live_zone",
    "no_invalidation",
    "degenerate_zone",
    "price_past_stop",
    "no_target",
    "reward_risk_too_low",
    "carry_dominates",
})

TIER_MAJOR = "major"
TIER_LARGE = "large"
TIER_SMALL = "small"
TIER_UNRANKED = "unranked"
TIER_NONCRYPTO = "non-crypto"

# CoinGecko rank boundaries. The revealed preference is "BTC, ETH, SOL, larger cap coins with
# only a few smaller cap bets", so these exist to *rank and label*, never to exclude. TUNE.
MAJOR_RANK_MAX = 10
LARGE_RANK_MAX = 100

# Reward-to-risk floor, used twice: a stated target must clear it to be believed, and a
# candidate must clear it to exist at all. A trade risking more than it stands to make is not
# a setup — "ask yourself what are the chances that I lose money on this trade?"
MIN_REWARD_RISK = 1.0

# A stated target further than this many ATRs from entry is a vibe, not a target.
#
# The first version measured against the *structural* distance instead, and it was measured and
# rejected: a recent break leaves a tiny post-break extreme, so on live ETH data a structural
# distance of ~6% rejected 78 of 135 readings for being "too far". ATR asks the stable question
# — how far does this instrument actually move — instead of one whose answer depends on how
# recently structure broke. TUNE.
MAX_TARGET_ATR = 20.0

# Reward-to-risk at which the score's RR term is worth exactly half. **A half-way point, not
# a clamp** — see ``reward_risk_signal``. It was ``RR_SATURATION`` and it clipped here, which
# made 3.0 and TSLA's 23.24 contribute identically and pinned the term at 1.0 for 12 of 18
# weekly rows (§4). TUNE.
RR_HALF = 3.0

# How long a position is assumed to be held, **for costing carry and nothing else**.
#
# This is deliberately NOT a horizon and must not become one. §2 removed the 7/30/180/365-day
# constants because they were unvalidated and load-bearing in grading and staleness; this one
# is load-bearing in neither. It prices funding and is invisible to `_score`, `HalfLife`, and
# `core.grade`. It does not vary by timeframe — reintroducing a per-label duration here is
# exactly how the deleted horizons would grow back.
#
# 21 days sits inside the measured restatement cadence (swing 11d, position 14d, scalp 28d).
# Carry is linear in time, so a reader can rescale any reported figure by inspection. TUNE.
CARRY_HOLD_DAYS = 21

# How far from a zone, as a fraction of price, ``approach`` is worth **half** of ``ARRIVAL``.
# Measured as a fraction so BTC and SOL are comparable. TUNE.
#
# This named the distance at which the ramp decayed *to zero* until 2026-07-27. The value is
# unchanged; what changed is that the curve no longer terminates there, so the constant is now
# the shape parameter of a decay rather than a cutoff — the same promotion ``HalfLife`` got
# when the staleness cliff became a slope. See ``approach_to``.
PROXIMITY_SPAN = 0.10

# Where on the approach ramp the near edge sits: 0.625 of it is spent reaching the zone, the
# remaining 0.375 traversing it. Not a new knob — it reproduces the old ``proximity`` 0.25 and
# ``depth`` 0.15 split within their 0.40 total exactly, so collapsing the two terms into one
# changed the *shape* of the signal without touching its calibration. That separation is
# deliberate: it keeps the measured effect attributable to the defect that was fixed rather
# than to a silent re-weighting riding along with it. TUNE.
ARRIVAL = 0.625

_BULLISH_STATES = frozenset({UPTREND, UPTREND_FAILED_BREAKOUT})
_BEARISH_STATES = frozenset({DOWNTREND, DOWNTREND_FAILED_BREAKDOWN})

_DIRECTION_FAMILY = {"long": BULLISH, "short": BEARISH}


@dataclass(frozen=True)
class HalfLife:
    """Age, per stated timeframe, at which a view is worth half of what it was worth new.

    Expressed in days but *derived* from candles of the timeframe the view was stated on — a
    swing call ages in weekly bars, a position call in monthly ones. That is what lets one
    rule fit all four horizons instead of four unrelated constants: ~3 candles of the relevant
    length. The counts, not the mechanism, are the knob. TUNE.

    **These were a cliff and are now a slope.** As ``StaleAfter`` they were the age at which a
    view was *discarded*, and that single constant was the largest filter in the system:
    2,913 of 3,459 rejections on the live corpus, killing 95% of the swing calls that are 56%
    of the whole corpus. Nothing justified the exact numbers — the gate that would have
    produced evidence about where the line belongs was the gate under test, so the cliff made
    itself unfalsifiable. Age is a measurement on a continuum; it now scores rather than
    gates, and ``--min-score`` is the one dial that decides how much confidence is enough.
    """
    scalp: int = 3      # ~3 daily candles
    swing: int = 21     # ~3 weekly candles
    position: int = 120  # ~4 monthly candles
    macro: int = 360    # ~4 quarterly candles

    def days_for(self, timeframe: str) -> int | None:
        return getattr(self, timeframe, None) if timeframe in _TIMEFRAMES else None


_TIMEFRAMES = frozenset({"scalp", "swing", "position", "macro"})
DEFAULT_HALF_LIFE = HalfLife()


@dataclass(frozen=True)
class SetupWeights:
    """How a surviving candidate is ordered. v1 best-guess, like ``core.rank.RankWeights``.

    ``approach`` was two terms until 2026-07-27 — ``proximity`` (0.25), rising as price neared
    the zone and saturating on arrival, plus ``depth`` (0.15), taking over once inside. They
    were never independent: their domains are disjoint, so neither ever varied while the other
    did, and together they already formed one continuous monotone ramp. Splitting a single
    ramp across two weights bought nothing and cost the ability to state it coherently — the
    pair could express ``proximity 0.00, depth 1.00``, meaning "no progress toward the zone"
    and "all the way through it" at once. That state is unrepresentable here, which is the
    point: it was the shape of a real defect (see ``OrderBlock.depth_at``), and a single ramp
    could not have been written down wrong the same way.

    ``trend_alignment`` carries a deliberately small weight. A ranging weekly is genuinely
    worse than an aligned one, and we have no idea how much worse; a small weight says that
    honestly instead of pretending to a precision we don't have.
    """
    approach: float = 0.40
    reward_risk: float = 0.20
    agreement: float = 0.20
    freshness: float = 0.15
    trend_alignment: float = 0.05


DEFAULT_WEIGHTS = SetupWeights()

# Scoring generation, recorded alongside every decision. Bumped whenever the terms or their
# weights change, because the decisions sidecar stores the score a candidate carried when it
# was judged, and correlating decisions against scores across a re-weighting would silently
# compare two different scales. 1 = proximity/depth/RR/agreement; 2 = + freshness/alignment;
# 3 = proximity and depth collapsed into ``approach``, and price past the stop refused.
# 4 = a stated target price had already reached is no longer believed.
# 5 = ``approach``'s outward leg decays instead of flooring at zero.
#
# v2 scores are not comparable to v3 ones even where the weights are unchanged: the 13 of 82
# candidates that v2 paid full depth for having traded through the zone are gone from v3
# entirely, so the population being ranked is different, not just the scale.
#
# v4 is the milder case and still warrants the bump. No term, weight, or candidate changed —
# the population is the same 69 — but 5 of them resolve to a different target and therefore a
# different reward:risk, so the same zone scores differently than it did when it was judged.
# Pooling those into the v3 bucket is exactly the silent comparison this constant exists to
# prevent, and §4's revealed-preference work reads the sidecar grouped by this field.
#
# v5 changes the *shape* of a term rather than its weight, and moves 47 of 69 candidates' ranks.
# Note the direction, because it is counterintuitive and was measured rather than assumed: the
# 16 candidates previously pinned at 0.00 move **up** a mean of 1.2 places, not down. A floored
# term cannot be pushed lower, so restoring its tail can only add score. v5 buys resolution,
# not demotion — see ``approach_to`` and §19.
#
# v6 unclips the two terms that could no longer order anything, and re-points the RR term at a
# different input. §4's mining pass found both saturations as facts about the terms rather than
# about the labels, so neither needed a correlation to justify: ``agreement_signal`` clamped at
# 3 while counts ran to 12 (pinned at 1.0 for 12 of 13 daily rows) and the RR term clamped at
# 3.0 while ratios ran to 23.24 (pinned for 12 of 18 weekly rows). Both are now hyperbolas, the
# shape ``freshness_signal`` and ``approach_to`` already used.
#
# The re-pointing is the larger change and is §19(d): ``_score`` now consumes
# ``reward_risk_from_price`` rather than ``reward_risk``, so distance stops inflating the term
# meant to rank the trade. ``reward_risk`` itself is untouched — same value, same gate, same
# headline — so this is a term-input correction, not a re-weight. Both numbers are recorded, so
# which of them predicts a decision better is now a measurement rather than an argument.
SCORE_VERSION = 6

# Weekly trend agrees with the thesis, versus has no opinion at all. There is no third value:
# a weekly that genuinely contradicts is still refused outright, so it never reaches scoring.
ALIGNED = 1.0
UNALIGNED = 0.0


def approach_to(block: OrderBlock, price: float, *, span: float = PROXIMITY_SPAN) -> float:
    """How far price has travelled toward and into ``block``, on one 0.0–1.0 ramp.

    ``ARRIVAL`` at the near edge, 1.0 at the far edge, and decaying toward — never reaching —
    0.0 as price gets further away. 0.0 exactly once price is past the far edge, because a zone
    price has traded clean through is not a near one. That last case is what the old two-term
    form got backwards; ``cross_reference`` also refuses it outright as ``price_past_stop``, so
    within the engine this branch is belt and braces rather than the only guard.

    **The outward leg was a cliff and is now a slope**, the same correction ``freshness_signal``
    already applies to age, for the same reason and in the same shape. It was
    ``ARRIVAL * max(0, 1 - gap/span)``, which hit zero at one span and stayed there. Measured on
    the live queue 2026-07-27: **16 of 69 candidates sat at exactly 0.00**, so SPX 26% from its
    zone, SOL *135%* from its zone, and anything 10.01% away were mutually indistinguishable.
    A term that saturates stops measuring, and this is the only term that expresses whether a
    trade can actually be taken — the queue had no way to say "this needs a 26% drawdown first".

    ``1 / (1 + gap/span)`` introduces **no second constant**: ``span`` stops being the distance
    where the ramp dies and becomes the distance where approach is worth half of ``ARRIVAL``,
    exactly as ``HalfLife`` became the shape parameter of the freshness curve rather than a
    cutoff. It meets the traversing branch at ``ARRIVAL`` with no discontinuity.

    **This does not, on its own, demote a distant candidate** — the term was already floored at
    0.0, so nothing could push it lower, and a continuous tail necessarily scores *above* that
    floor. What it buys is resolution: distance becomes rankable instead of collapsing to one
    value. Demoting them means fixing the reward:risk term, which currently *rises* with the
    same distance. See ``docs/IMPROVEMENTS.md`` §19.
    """
    if block.traded_through(price):
        return 0.0
    depth = block.depth_at(price)
    if depth is not None:
        return ARRIVAL + (1.0 - ARRIVAL) * depth
    if price <= 0 or span <= 0:
        return 0.0
    gap = abs(price - block.near_edge) / price
    return ARRIVAL / (1.0 + gap / span)


def freshness_signal(age_days: int, half_life: int) -> float:
    """How much a view is still worth at ``age_days``, given its timeframe's half-life.

    ``1 / (1 + age/half_life)`` — 1.0 the day it was said, exactly 0.5 at the half-life, 0.33
    at twice it, 0.25 at three times. Chosen over a linear ramp or an exponential because it
    introduces **no second constant**: the existing per-timeframe windows become the curve's
    shape parameter, so nothing new has to be guessed at.

    It never reaches zero, and that is the point. Age alone must not be able to eliminate a
    view — a call from two years ago ranks near the bottom of the queue where it belongs,
    rather than vanishing into a rejection tally nobody reads.
    """
    if half_life <= 0:
        return 0.0
    return 1.0 / (1.0 + max(age_days, 0) / half_life)


def reward_risk_signal(reward_risk: float, *, half: float = RR_HALF) -> float:
    """Reward-to-risk as a 0-1 signal, with diminishing returns and no ceiling.

    ``half`` is the ratio worth exactly 0.5, not a clamp. The old ``min(rr / 3.0, 1.0)`` made
    3.0, SPX's 9.06, GOOGL's 14.19 and TSLA's **23.24** contribute identically, so the term was
    pinned at its maximum for 12 of 18 weekly rows and could not order them at all (§4) — the
    same defect ``agreement_signal`` carried, and the same fix.

    Note this is only about *resolution*. That very high ratios are a symptom of a broken
    denominator rather than a good trade is a separate claim, and §19(d) leaves it open; this
    function still says 23.24 beats 3.0, it merely stops pretending they are equal.
    """
    if half <= 0:
        return 0.0
    return reward_risk / (reward_risk + half)


def _score(weights: SetupWeights, *, approach: float, reward_risk_from_price: float,
           agreement_count: int, freshness: float, trend_alignment: float) -> float:
    """The composite. Note the RR term is fed from **price**, not from entry — see
    ``Setup.reward_risk_from_price`` for why the displayed ratio is a different number."""
    return (
        weights.approach * approach
        + weights.reward_risk * reward_risk_signal(reward_risk_from_price)
        + weights.agreement * agreement_signal(agreement_count)
        + weights.freshness * freshness
        + weights.trend_alignment * trend_alignment
    )


@dataclass(frozen=True, slots=True)
class Zone:
    """A live order block plus the extreme price reached since its break.

    ``structural_target`` is where price already travelled after breaking structure — "that's
    generally where it's going whether they call it that way or not". It lives here rather than
    on ``OrderBlock`` because it depends on the as-of date, not on the block itself.
    """
    block: OrderBlock
    structural_target: float | None
    # Which bar series ``block`` was derived from. Defaults to daily so every caller that
    # predates weekly structure — and every fixture built by hand — keeps its meaning.
    timeframe: str = DAILY


@dataclass(frozen=True, slots=True)
class Context:
    """Structure for one asset as of one date. Reusable across every thesis on that asset.

    ``zones`` holds only *live* blocks — ``build_context`` drops any whose invalidation level
    has already been closed through, so the gates never have to re-check.
    """
    as_of: date
    price: float
    weekly_trend: str
    daily_trend: str
    dealing_range: DealingRange | None
    zones: tuple[Zone, ...]
    # Daily ATR at ``as_of``, the yardstick for whether a stated target is plausible. None when
    # there is too little history to judge, in which case the check is skipped rather than
    # guessed at.
    atr: float | None = None
    # What holding a position on this asset costs, on the venue it would actually trade on.
    # Injected rather than read, because this module does no I/O — ``oracle.setups_cli`` loads
    # ``data/funding/`` and summarises it. None follows ``atr``'s precedent exactly: the
    # adjustment is skipped, never costed at an invented zero. A measured zero (Aster charges
    # nothing on equities) is a different fact and is representable.
    funding: FundingOutlook | None = None


@dataclass(frozen=True, slots=True)
class View:
    """One person's most recent statement backing a candidate, and when they made it.

    A candidate collapses many theses across many people and dates, so "when was this called"
    has no single answer — and a bare count of people hides that one of them last spoke months
    ago. Carrying the date per person is the only honest form.
    """
    person: str
    published_at: str   # normalized to YYYY-MM-DD


@dataclass(frozen=True, slots=True)
class Setup:
    """One thesis that survived every gate. Usually not the unit you want — see ``collapse``."""
    thesis_id: str
    asset: str
    person: str
    direction: str
    timeframe: str
    published_at: str
    block: OrderBlock
    entry: float          # the near edge — the shallowest fill, so RR is conservative
    entry_top: float
    entry_bottom: float
    stop: float           # the far edge: where this trade is wrong
    invalidation: float   # the origin swing: where the zone itself dies
    target: float
    target_source: str    # STATED | NEAREST | STRUCTURAL
    reward_risk: float
    # The same ratio measured from **where price is now** rather than from entry, and the one
    # ``_score`` actually uses. §19(d): with a structural target — the post-break extreme — the
    # numerator of ``reward_risk`` is literally how far price ran away from the zone, so
    # distance inflates the very number meant to rank the trade. SPX scored 9.06 that way, and
    # a chunk of it was earned by being 26% out of reach.
    #
    # Both are kept, and neither is redundant. ``reward_risk`` is what the trade pays **if it
    # fills**, which is what the queue prints and what ``MIN_REWARD_RISK`` gates on — and that
    # gate must stay on it, because "risking more than it stands to make" is a rule, while
    # reachability is a measurement on a continuum. Per the gates-vs-scores split, a rule is
    # gated and a continuum is scored; feeding this number to the gate would quietly convert
    # reachability into one.
    reward_risk_from_price: float
    approach: float       # 0 a span away, ARRIVAL at the near edge, 1 at the far
    # What the asset was actually trading at when this was built. ``approach`` is derived from
    # it but cannot be read back as a price, so without it the queue can state where a trade is
    # wrong without ever stating where the market is.
    price: float
    freshness: float         # 1.0 the day it was said, halving every ``HalfLife`` days
    trend_alignment: float   # ALIGNED, or UNALIGNED when the weekly is merely ranging
    weekly_trend: str
    daily_trend: str
    zone: str             # DISCOUNT | PREMIUM
    zone_timeframe: str   # WEEKLY | DAILY — which series ``block`` was read from
    tier: str
    score: float
    # ── carry: what holding this costs. Reported, never scored — see CARRY_HOLD_DAYS. ──
    # All four are None together when ``Context.funding`` was absent.
    funding_annual: float | None = None      # the median rate used, as a fraction per year
    carry: float | None = None               # fraction of notional over CARRY_HOLD_DAYS;
                                             # positive = paid, negative = collected
    carry_reward_risk: float | None = None       # R:R once carry is charged to both legs
    carry_reward_risk_p90: float | None = None   # the same at the venue's p90 rate


@dataclass(frozen=True, slots=True)
class Candidate:
    """A zone, with every roster view that supports it — the actual unit of a setup.

    A setup is a ``(asset, direction, zone)``; the roster is *evidence for* it, not a multiplier
    of it. Emitting one row per thesis was tried and measured: eight identical ETH longs
    differing only by author, which is exactly the noise behind the stated leak "I take way too
    many trades". Agreement belongs inside a candidate, not spread across duplicates of it.
    """
    asset: str
    direction: str
    block: OrderBlock
    entry: float
    entry_top: float
    entry_bottom: float
    stop: float
    invalidation: float
    target: float
    target_source: str
    reward_risk: float
    reward_risk_from_price: float   # see ``Setup.reward_risk_from_price``
    approach: float       # see ``Setup.approach``
    price: float          # the asset's price when built — see ``Setup.price``
    weekly_trend: str
    daily_trend: str
    zone: str
    zone_timeframe: str   # WEEKLY | DAILY — which series ``block`` was read from
    tier: str
    # The freshest supporting view's, not an average: the question a candidate answers is "is
    # anyone still saying this", so one current voice carries a zone the others have gone
    # quiet on. See ``collapse``.
    freshness: float
    trend_alignment: float
    # Latest statement per person, newest first. Ordered by recency rather than alphabetically
    # because "who said this most recently" is the question being asked.
    views: tuple[View, ...]
    thesis_ids: tuple[str, ...]
    score: float
    # Carry, taken from the representative setup — see ``Setup``. Reported, never scored.
    funding_annual: float | None = None
    carry: float | None = None
    carry_reward_risk: float | None = None
    carry_reward_risk_p90: float | None = None

    @property
    def people(self) -> tuple[str, ...]:
        return tuple(view.person for view in self.views)

    @property
    def agreement(self) -> int:
        return len(self.views)

    @property
    def newest_at(self) -> str:
        return self.views[0].published_at if self.views else ""

    @property
    def oldest_at(self) -> str:
        return self.views[-1].published_at if self.views else ""

    @property
    def key(self) -> str:
        """Content-addressed zone identity, for keying decisions on disk.

        Deliberately built from the zone's *prices and date*, never from ``block.index``. That
        index is a position in a bar sequence, and backfilling earlier price data shifts every
        index — the exact failure that made thesis ids content-addressed in ``core.thesis``
        (positional ids silently re-pointed triage decisions at unrelated theses). A zone is
        the same zone whatever array it happens to live in.

        **Daily zones keep the original three-field form, and that asymmetry is deliberate.**
        Decisions already on disk are keyed by this hash, so mixing the timeframe into every
        input would orphan every approve/reject ever recorded and refill the queue with zones
        already judged. Weekly zones are new, have no history to preserve, and do need the
        discriminator: ``oracle.resample.to_weekly`` dates a weekly bar at the last daily bar it
        aggregates, so a week with a single trading day yields a bar identical to that day's —
        same date, same high, same low — which would otherwise collide onto one key.
        """
        raw = f"{self.asset}\x1f{self.direction}\x1f{self.block.date.isoformat()}" \
              f"\x1f{self.block.top}\x1f{self.block.bottom}"
        if self.zone_timeframe != DAILY:
            raw += f"\x1f{self.zone_timeframe}"
        return sha256(raw.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class NotASetup:
    thesis_id: str
    asset: str
    person: str
    reason: str


Outcome = Setup | NotASetup


def collapse(outcomes, *, weights: SetupWeights = DEFAULT_WEIGHTS) -> tuple[Candidate, ...]:
    """Fold per-thesis setups into one candidate per zone, best score first.

    ``outcomes`` may contain ``NotASetup`` entries; they are ignored, so a caller can pass the
    raw stream straight through.

    Target selection is two-stage, and the order matters. **A target someone actually stated
    beats a structural one** — "if they call something, we listen" — and only among equals does
    the **nearest** win, on the grounds that if they can't agree how far price goes, the
    smallest claim is the one to hold them to.

    Taking the nearest outright was tried first and measured: structural targets are usually
    closer than stated ones, so on live ETH data 7 accepted readings (3 stated, 4 inferred) all
    lost to structure, and the "listen to them" half of the design never fired once.

    Agreement is recomputed from the collapsed group, which is the whole point — six people on
    one zone is one strong candidate, not six weak ones.

    **Ordering is weekly-then-score, not score alone.** "The macro is much stronger" already
    lets the weekly trend veto a direction outright in ``cross_reference``, and the same rule
    decides the queue: a weekly zone outranks a daily one even when it scores lower. It usually
    *will* score lower, because price has typically not reached it — on live GOOGL the weekly
    zone scored ~0.75 against the daily zone's 0.90 precisely because ``proximity`` and
    ``depth`` reward being close, which is the near-price bias in a different coat. Expressing
    the precedence in the sort rather than as a score weight keeps it a rule, per the split this
    module opens with: a rule is gated or ordered, never quietly priced into a continuum.
    """
    groups: dict[tuple, list[Setup]] = defaultdict(list)
    for outcome in outcomes:
        if isinstance(outcome, Setup):
            # ``zone_timeframe`` is part of the identity because ``block.index`` is only unique
            # within the series it indexes — daily bar 42 and weekly bar 42 are unrelated.
            key = (outcome.asset, outcome.direction, outcome.zone_timeframe,
                   outcome.block.index, outcome.block.confirmed_at)
            groups[key].append(outcome)

    candidates = []
    for members in groups.values():
        authored = [s for s in members if s.target_source != STRUCTURAL]
        rep = min(authored or members, key=lambda s: abs(s.target - s.entry))

        # Latest statement per person, newest first. Keeping only the latest means a person who
        # restated ten times doesn't read as ten separate voices, and surfacing the date means a
        # months-old view can't hide behind a healthy-looking agreement count.
        latest: dict[str, str] = {}
        for member in members:
            if member.published_at > latest.get(member.person, ""):
                latest[member.person] = member.published_at
        views = tuple(
            View(person=person, published_at=when)
            for person, when in sorted(latest.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
        )
        # The freshest member's, not the representative's or an average. A zone one person
        # called yesterday and three called last year is a live idea with old corroboration,
        # not a stale one — and averaging would let extra supporters *lower* the score, which
        # would invert what agreement is supposed to mean. Taking the max also keeps ``as_of``
        # out of ``collapse``, which has no business knowing today's date.
        freshness = max(s.freshness for s in members)
        candidates.append(Candidate(
            asset=rep.asset, direction=rep.direction, block=rep.block,
            entry=rep.entry, entry_top=rep.entry_top, entry_bottom=rep.entry_bottom,
            stop=rep.stop, invalidation=rep.invalidation,
            target=rep.target, target_source=rep.target_source,
            reward_risk=rep.reward_risk,
            reward_risk_from_price=rep.reward_risk_from_price, approach=rep.approach,
            price=rep.price,
            weekly_trend=rep.weekly_trend, daily_trend=rep.daily_trend,
            zone=rep.zone, zone_timeframe=rep.zone_timeframe, tier=rep.tier,
            freshness=freshness, trend_alignment=rep.trend_alignment,
            views=views,
            thesis_ids=tuple(sorted(s.thesis_id for s in members)),
            score=_score(weights, approach=rep.approach,
                         reward_risk_from_price=rep.reward_risk_from_price,
                         agreement_count=len(views),
                         freshness=freshness, trend_alignment=rep.trend_alignment),
            # From the representative, like every other price-derived field: carry is a
            # property of the trade (zone, direction, asset), and every member of a group
            # shares all three. Taking a max or mean the way ``freshness`` does would be
            # wrong here — there is nothing to disagree about.
            funding_annual=rep.funding_annual, carry=rep.carry,
            carry_reward_risk=rep.carry_reward_risk,
            carry_reward_risk_p90=rep.carry_reward_risk_p90,
        ))
    # ── ordering: thesis groups by their best zone, weekly first *within* a group ──
    #
    # This was an unconditional weekly-then-score sort, which put every weekly ahead of every
    # daily and therefore separated the two rows that describe one thesis. §27's sitting hit
    # exactly that: SPX6900's weekly was row 1, an unrelated WLD row was 2, and SPX6900's daily
    # was row 3 — so the second was judged against a memory of the first. Judging two
    # expressions of one thesis against each other is the whole point of offering both.
    #
    # A group is ranked by its **best** zone rather than by its weekly one, because the weekly
    # is not reliably the better trade: measured over the 27 assets carrying both on 2026-07-28,
    # the daily scored higher 15 times to the weekly's 12 (mean 0.535 v 0.498). Ranking by the
    # weekly alone would bury a strong daily behind a weak weekly, which is the §19(e) harm that
    # left TSLA at 0.906 sitting in position 29, off the screen entirely.
    #
    # §19(e)'s precedence survives where it is unambiguous — "the macro is much stronger" still
    # decides which expression of *one* thesis is offered first. It no longer reorders unrelated
    # theses against each other, which it was never argued for and which cost the queue its
    # best-scoring row.
    best_in_group: dict[tuple[str, str], float] = {}
    for c in candidates:
        group = (c.asset, c.direction)
        best_in_group[group] = max(best_in_group.get(group, c.score), c.score)

    return tuple(sorted(candidates, key=lambda c: (
        -best_in_group[(c.asset, c.direction)],   # groups, best zone first
        c.asset, c.direction,                     # keep a group contiguous
        ZONE_TIMEFRAMES.index(c.zone_timeframe) if c.zone_timeframe in ZONE_TIMEFRAMES
        else len(ZONE_TIMEFRAMES),                # weekly before daily, within the group
        -c.score,
    )))


def tier_for(rank: int | None, *, domain: str = "crypto") -> str:
    """Label an asset by CoinGecko market-cap rank.

    ``domain`` gates the whole thing, and that guard is load-bearing rather than defensive.
    Market-cap rank is a crypto concept, but ticker namespaces collide across domains: on live
    data ``SPX`` — the S&P 500, correctly priced at ~7147 via the curated routing table —
    resolved to CoinGecko **rank 124** and was labelled a small-cap, because some memecoin
    shares the ticker. Price routing refuses to guess across domains (``cfg/oracle_map.yaml``);
    tiering has to refuse too, or the collision walks straight back in through the ranking.

    ``TIER_NONCRYPTO`` is distinct from ``TIER_UNRANKED`` on purpose: "this is a stock" and
    "this is crypto we couldn't resolve" are different facts, and collapsing them would hide
    resolution failures inside a legitimate category.
    """
    if domain != "crypto":
        return TIER_NONCRYPTO
    if rank is None:
        return TIER_UNRANKED
    if rank <= MAJOR_RANK_MAX:
        return TIER_MAJOR
    if rank <= LARGE_RANK_MAX:
        return TIER_LARGE
    return TIER_SMALL


def build_context(daily, weekly, *, as_of: date,
                  width: int = SWING_WIDTH) -> Context | None:
    """Compute structure for one asset. None when the asset has no price at ``as_of``.

    ``daily`` and ``weekly`` are bar sequences — pass ``series.bars`` and
    ``resample.to_weekly(series).bars``. The dealing range is taken from **weekly**, since the
    manifesto bounds it on the macro timeframe.

    **Order blocks are read from both series and kept side by side**, tagged by the one they
    came from. See ``DAILY``/``WEEKLY`` for why daily alone was not enough. Each series
    validates and measures its own blocks: ``block.bos.index`` indexes the array the block was
    derived from, so an invalidation check or a post-break extreme computed against the other
    array would be reading unrelated bars.

    One honest limitation: ``to_weekly`` excludes the in-progress week, so a weekly zone's
    ``structural_target`` cannot see this week's extreme and will understate it slightly. That
    is the same look-ahead discipline ``oracle.resample`` is built on, and understating a target
    is the safe direction to be wrong in.
    """
    upto = tuple(bar for bar in daily if bar.date <= as_of)
    if not upto:
        return None

    return Context(
        as_of=as_of,
        price=upto[-1].close,
        weekly_trend=trend_state(weekly, as_of=as_of, width=width),
        daily_trend=trend_state(daily, as_of=as_of, width=width),
        dealing_range=resolve_dealing_range(weekly, as_of=as_of, width=width),
        zones=(
            _zones_from(weekly, timeframe=WEEKLY, as_of=as_of, width=width)
            + _zones_from(daily, timeframe=DAILY, as_of=as_of, width=width)
        ),
        atr=atr(upto, len(upto) - 1),
    )


def _zones_from(bars, *, timeframe: str, as_of: date, width: int) -> tuple[Zone, ...]:
    """Live blocks from one bar series, each validated and measured against that same series."""
    upto = tuple(bar for bar in bars if bar.date <= as_of)
    if not upto:
        return ()
    return tuple(
        Zone(block=block, structural_target=_extreme_since(upto, block), timeframe=timeframe)
        for block in order_blocks(bars, as_of=as_of, width=width)
        if invalidated_on(block, upto) is None
    )


def _extreme_since(bars, block: OrderBlock) -> float | None:
    """The furthest price travelled in the break's direction since it happened."""
    window = bars[block.bos.index:]
    if not window:
        return None
    if block.kind == BULLISH:
        return max(bar.high for bar in window)
    return min(bar.low for bar in window)


def cross_reference(
    row,
    context: Context,
    *,
    published_close: float | None,
    zone_timeframe: str = DAILY,
    asset_rank: int | None = None,
    agreement_count: int = 0,
    weights: SetupWeights = DEFAULT_WEIGHTS,
    half_life: HalfLife = DEFAULT_HALF_LIFE,
    min_reward_risk: float = MIN_REWARD_RISK,
    max_target_atr: float = MAX_TARGET_ATR,
) -> Outcome:
    """Gate one thesis against one asset's structure.

    ``row`` is duck-typed on ``id``/``asset``/``person``/``direction``/``timeframe``/
    ``published_at``/``key_levels``, matching ``core.grade``'s flattened corpus row.
    ``published_close`` is the asset's close when the thesis was published — a per-thesis
    input, which is why it isn't on ``Context``. ``core.levels`` needs it to tell a stated
    target from a stated stop.

    ``zone_timeframe`` selects which series' structure to gate against, and one call judges
    exactly one timeframe. Defaulting to ``DAILY`` preserves the original contract — one thesis
    in, one ``Outcome`` out — so a caller wanting both passes asks for both explicitly, and
    every pre-existing caller and fixture keeps its meaning. ``timeframe`` (from ``row``) is the
    unrelated horizon the *person* spoke on: scalp/swing/position/macro. The two are deliberately
    named apart because conflating them would silently gate a swing call against weekly bars.
    """
    ident = getattr(row, "id", "")
    asset = getattr(row, "asset", "")
    person = getattr(row, "person", "")
    direction = getattr(row, "direction", "")
    timeframe = getattr(row, "timeframe", "")

    def refuse(reason: str) -> NotASetup:
        return NotASetup(thesis_id=ident, asset=asset, person=person, reason=reason)

    # ── freshness: scored, not gated. Only the *absence* of a date still refuses. ──
    published = parse_date(getattr(row, "published_at", None))
    if published is None:
        return refuse("undated")
    window = half_life.days_for(timeframe)
    if window is None:
        return refuse("unknown_timeframe")
    freshness = freshness_signal((context.as_of - published).days, window)

    # ── Rule 8: weekly sets direction, and daily may not contradict it ──
    #
    # A weekly that *contradicts* is discarded — "the macro is much stronger". A weekly that
    # is merely **ranging** is a different fact: no macro opinion exists to defer to. Those
    # two shared this refusal until the live corpus showed them to be 1,617 and 630 rows
    # respectively — a fifth of everything reaching this gate thrown away for the absence of
    # an opinion rather than the presence of a contrary one. Ranging now scores unaligned.
    family = _DIRECTION_FAMILY.get(direction)
    if family is None:
        return refuse("unknown_direction")
    weekly_family = _family_of(context.weekly_trend)
    if weekly_family is not None and weekly_family != family:
        return refuse("weekly_disagrees")
    trend_alignment = ALIGNED if weekly_family == family else UNALIGNED

    # ── the daily leg may only *contradict* a weekly that actually has an opinion ──
    #
    # A conflict needs two views. When the weekly is ranging there is only one, so this
    # refusal was reporting the daily leg alone under a name that claims otherwise — the same
    # error the gate above made until §6 split ranging out of ``weekly_disagrees``.
    #
    # Audited 2026-07-28 (§27): of 1,017 refusals, **256 had a ranging weekly**, and releasing
    # those recovers 23 candidates. The remaining 761 are genuine two-timeframe disagreements
    # and are still refused here.
    #
    # Deliberately a release, not a re-score. The daily leg reads a median of **21 days**
    # against the weekly leg's 125, so it is a timing signal wearing a trend signal's name,
    # and whether it belongs in ``_score`` is a live question (§27's option 2) that cannot be
    # answered yet: while this gate refused every contradicting row, the decisions sidecar
    # accumulated *zero* negative examples of one. ``daily_trend`` is now recorded on every
    # decision so that measurement becomes possible. No term and no weight changed, so no
    # candidate's score moves and ``SCORE_VERSION`` stays at 6 — §21's precedent.
    if weekly_family is not None:
        daily_family = _family_of(context.daily_trend)
        if daily_family is not None and daily_family != family:
            return refuse("timeframe_conflict")

    # ── premium/discount ──
    if context.dealing_range is None:
        return refuse("no_dealing_range")
    if not context.dealing_range.permits(direction, context.price):
        return refuse("wrong_side_of_range")
    zone_label = context.dealing_range.zone_at(context.price)

    # ── a live zone pointing the same way ──
    zone = _newest_zone(context.zones, family, zone_timeframe)
    if zone is None:
        return refuse("no_live_zone")
    block = zone.block
    # A zone with no origin swing can never be invalidated, so it would live forever — worse
    # than reporting nothing. The stop is always available (it's the far edge), so this
    # refusal is about the zone's lifetime, not about missing risk.
    if block.invalidation is None:
        return refuse("no_invalidation")

    entry = block.near_edge
    risk = abs(entry - block.stop)
    if risk == 0:
        return refuse("degenerate_zone")

    # ── price has already traded out the far side ──
    #
    # ``block.stop`` is the far edge, so price beyond it means an entry at ``near_edge`` would
    # already have been stopped. Not a judgment about the zone — that lives until
    # ``invalidation``, further out still, and ``build_context`` is right to keep serving it.
    #
    # A gate rather than a score, per the rule at the top of this module: whether the stop has
    # been taken is a *fact* about the trade, not a measurement on a continuum. It is checked
    # after ``degenerate_zone`` because a zero-height zone puts every price on its far side,
    # which would relabel that refusal without changing what it refuses.
    if block.traded_through(context.price):
        return refuse("price_past_stop")

    # ── target: theirs if reasonable, else structure ──
    stated = read_target(row, published_close)
    sign = 1 if family == BULLISH else -1
    target, target_source = None, ""
    if not stated.abstained and _reasonable(
        stated.target, entry=entry, risk=risk, sign=sign,
        price_now=context.price, atr_now=context.atr,
        min_reward_risk=min_reward_risk, max_target_atr=max_target_atr,
    ):
        # Propagate the reading's own source rather than flattening to STATED — a NEAREST
        # target is inferred, and relabelling it clean would destroy the provenance that
        # makes "did inferred targets do as well as clean ones" answerable.
        target, target_source = stated.target, stated.source
    elif zone.structural_target is not None and (zone.structural_target - entry) * sign > 0:
        target, target_source = zone.structural_target, STRUCTURAL
    if target is None:
        return refuse("no_target")

    reward_risk = abs(target - entry) / risk
    # A trade risking more than it stands to make is not a setup, whichever source supplied the
    # target. Left to scoring alone, a 0.32-RR candidate still surfaced mid-list on live data —
    # and a list padded with those is what feeds "I take way too many trades".
    #
    # Gated on the entry-based ratio deliberately, and ``reward_risk_from_price`` must never be
    # substituted here. This gate encodes a rule about the *trade*; the price-based ratio is a
    # statement about the *journey*, and a far zone has price sitting close to its structural
    # target, so gating on it would refuse candidates for being out of reach. Reachability is a
    # continuum and belongs in the score — which is exactly where it now is.
    if reward_risk < min_reward_risk:
        return refuse("reward_risk_too_low")

    # What is left to be made from where the market actually is. See the field's own comment
    # on ``Setup`` for why this, and not ``reward_risk``, is what ``_score`` consumes.
    reward_risk_from_price = abs(target - context.price) / risk

    # ── carry: what holding this costs, once the trade itself is known to be a setup ──
    #
    # Runs last because it is the only term that depends on ``direction`` *and* on both legs:
    # funding is subtracted from reward and added to risk, so a long and a short on the same
    # zone at the same levels do not cost the same. That asymmetry is the whole point.
    #
    # Reported, never scored. `_score` does not see these fields and `score_version` does not
    # move — per §21, the weighting decision waits on a session of decisions recorded with
    # both numbers, so this run cannot change any ranking.
    funding_annual = carry = carry_rr = carry_rr_p90 = None
    if context.funding is not None:
        # Fractions of entry price, matching what ``carry_adjusted_rr`` expects.
        reward_frac = abs(target - entry) / entry
        risk_frac = risk / entry
        # Derived from ``family``, not from ``direction`` verbatim: the corpus's direction
        # vocabulary is wider than long/short, and ``_DIRECTION_FAMILY`` is the mapping that
        # already resolves it. Passing a raw label through would raise on the first synonym.
        side = "long" if family == BULLISH else "short"
        adjusted = carry_adjusted_rr(
            reward_frac, risk_frac, context.funding.median, CARRY_HOLD_DAYS, side
        )
        stressed = carry_adjusted_rr(
            reward_frac, risk_frac, context.funding.p90, CARRY_HOLD_DAYS, side
        )
        funding_annual = context.funding.median
        carry, carry_rr, carry_rr_p90 = adjusted.carry, adjusted.ratio, stressed.ratio

        # A gate, not a score — the trade loses money *at its own target*, which is a fact
        # about the arithmetic rather than a judgement on a continuum. It can only fire when
        # funding was actually measured; an unpriced asset is never refused for an unmeasured
        # cost, mirroring how ``atr`` skips the plausibility ceiling.
        if adjusted.carry_dominates:
            return refuse("carry_dominates")

    approach = approach_to(block, context.price)
    return Setup(
        thesis_id=ident, asset=asset, person=person, direction=direction,
        timeframe=timeframe, published_at=published.isoformat(), block=block,
        entry=entry, entry_top=block.top, entry_bottom=block.bottom,
        stop=block.stop, invalidation=block.invalidation,
        target=target, target_source=target_source,
        reward_risk=reward_risk, reward_risk_from_price=reward_risk_from_price,
        approach=approach, price=context.price,
        freshness=freshness, trend_alignment=trend_alignment,
        weekly_trend=context.weekly_trend, daily_trend=context.daily_trend,
        # Domain comes off the row so a cross-domain ticker collision can't leak a crypto
        # market-cap rank onto a stock or a macro instrument. See tier_for.
        zone=zone_label or "",
        zone_timeframe=zone_timeframe,
        tier=tier_for(asset_rank, domain=getattr(row, "domain", "crypto")),
        score=_score(weights, approach=approach,
                     reward_risk_from_price=reward_risk_from_price,
                     agreement_count=agreement_count,
                     freshness=freshness, trend_alignment=trend_alignment),
        funding_annual=funding_annual, carry=carry,
        carry_reward_risk=carry_rr, carry_reward_risk_p90=carry_rr_p90,
    )


def _family_of(state: str) -> str | None:
    """Which side a trend state permits. Ranging permits neither.

    A *failed* break is permissive, not disqualifying: "assume higher prices until breakout of
    resistance fails — then you can look for a counter-trend move down to a higher low." That
    pullback is the long entry, so it belongs with the uptrend, not against it.
    """
    if state in _BULLISH_STATES:
        return BULLISH
    if state in _BEARISH_STATES:
        return BEARISH
    return None


def _newest_zone(zones, family: str, timeframe: str) -> Zone | None:
    """The most recently confirmed live zone on the given side, *within one timeframe*.

    Selected by ``confirmed_at`` rather than sequence position so the caller's ordering can
    never change the answer — structure is defined by the most recent level.

    Scoped to a single timeframe rather than ranging across all of them because a weekly zone
    and a daily zone are separate candidates, judged separately. Letting them compete here would
    reintroduce exactly the newest-wins bias weekly structure was added to correct: the daily
    block is almost always the newer one, so it would win every contest.
    """
    matching = [zone for zone in zones
                if zone.timeframe == timeframe and zone.block.kind == family]
    if not matching:
        return None
    return max(matching, key=lambda zone: (zone.block.confirmed_at, zone.block.index))


def _reasonable(target, *, entry: float, risk: float, sign: int, price_now: float,
                atr_now: float | None, min_reward_risk: float,
                max_target_atr: float) -> bool:
    """Whether a stated target is worth believing over the structural one.

    Four ways to fail: it's behind the entry (so it isn't a target any more, whatever it was
    when stated), **price has already reached it**, it doesn't clear the stop by enough to be
    worth taking, or it's further away than the instrument plausibly travels.

    An unknown ATR skips the distance check rather than failing it — inability to judge must
    not read as a verdict, the same rule ``imbalance.is_displacement`` follows.
    """
    if (target - entry) * sign <= 0:
        return False
    # A target price has already traded to is a satisfied claim, not a forecast. Checking only
    # against ``entry`` let one through whenever the zone sat below where price had since gone:
    # on the live queue that was 5 of 69 candidates, headed by an SPX long showing a target of
    # 6000 against a price of 7403 — read from a call published at 5842, whose author had been
    # right and finished being right more than a year earlier.
    #
    # It belongs here, as a *believability* test on the author's number, rather than as a gate
    # beside ``price_past_stop``. The two are not the same fact: a taken stop kills the trade,
    # while a reached target kills only that target, and structure still has a live one to
    # supply. Refusing outright would have discarded all five candidates including an OIL long
    # eight people are on, whose structural target sits 43% above price. Note also that a
    # structural target can *never* fail this check by construction — ``_extreme_since`` is a
    # realized high or low — so all this can reject is a stale authored number, which is
    # exactly the failure observed; every one of the five was ``stated`` or ``nearest``.
    if (target - price_now) * sign <= 0:
        return False
    if abs(target - entry) / risk < min_reward_risk:
        return False
    if atr_now and abs(target - entry) > max_target_atr * atr_now:
        return False
    return True
