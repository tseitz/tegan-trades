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

# Reward-to-risk at which the score's RR term saturates. TUNE.
RR_SATURATION = 3.0

# How far from a zone, as a fraction of price, proximity decays to zero. Measured as a
# fraction so BTC and SOL are comparable. TUNE.
PROXIMITY_SPAN = 0.10

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

    ``proximity`` and ``depth`` are complementary halves of one continuum: proximity rises as
    price approaches the zone and saturates on arrival, depth takes over once inside. Both are
    needed because a live zone 1% away and one 30% away would otherwise score identically —
    measured on real data, every candidate sat outside its zone with ``depth`` pinned at 0.

    ``trend_alignment`` carries a deliberately small weight. A ranging weekly is genuinely
    worse than an aligned one, and we have no idea how much worse; a small weight says that
    honestly instead of pretending to a precision we don't have.
    """
    proximity: float = 0.25
    depth: float = 0.15
    reward_risk: float = 0.20
    agreement: float = 0.20
    freshness: float = 0.15
    trend_alignment: float = 0.05


DEFAULT_WEIGHTS = SetupWeights()

# Scoring generation, recorded alongside every decision. Bumped whenever the terms or their
# weights change, because the decisions sidecar stores the score a candidate carried when it
# was judged, and correlating decisions against scores across a re-weighting would silently
# compare two different scales. 1 = proximity/depth/RR/agreement; 2 = + freshness/alignment.
SCORE_VERSION = 2

# Weekly trend agrees with the thesis, versus has no opinion at all. There is no third value:
# a weekly that genuinely contradicts is still refused outright, so it never reaches scoring.
ALIGNED = 1.0
UNALIGNED = 0.0


def proximity_to(block: OrderBlock, price: float, *, span: float = PROXIMITY_SPAN) -> float:
    """1.0 inside the zone, decaying to 0.0 ``span`` away from its near edge."""
    if block.bottom <= price <= block.top:
        return 1.0
    if price <= 0 or span <= 0:
        return 0.0
    return max(0.0, 1.0 - (abs(price - block.near_edge) / price) / span)


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


def _score(weights: SetupWeights, *, proximity: float, depth: float, reward_risk: float,
           agreement_count: int, freshness: float, trend_alignment: float) -> float:
    return (
        weights.proximity * proximity
        + weights.depth * depth
        + weights.reward_risk * min(reward_risk / RR_SATURATION, 1.0)
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
    stop: float           # just past the far edge: where this trade is wrong
    invalidation: float   # the origin swing: where the zone itself dies
    target: float
    target_source: str    # STATED | NEAREST | STRUCTURAL
    reward_risk: float
    depth: float
    proximity: float
    freshness: float         # 1.0 the day it was said, halving every ``HalfLife`` days
    trend_alignment: float   # ALIGNED, or UNALIGNED when the weekly is merely ranging
    weekly_trend: str
    daily_trend: str
    zone: str             # DISCOUNT | PREMIUM
    tier: str
    score: float


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
    depth: float
    proximity: float
    weekly_trend: str
    daily_trend: str
    zone: str
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
        """
        raw = f"{self.asset}\x1f{self.direction}\x1f{self.block.date.isoformat()}" \
              f"\x1f{self.block.top}\x1f{self.block.bottom}"
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
    """
    groups: dict[tuple, list[Setup]] = defaultdict(list)
    for outcome in outcomes:
        if isinstance(outcome, Setup):
            key = (outcome.asset, outcome.direction,
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
            reward_risk=rep.reward_risk, depth=rep.depth, proximity=rep.proximity,
            weekly_trend=rep.weekly_trend, daily_trend=rep.daily_trend,
            zone=rep.zone, tier=rep.tier,
            freshness=freshness, trend_alignment=rep.trend_alignment,
            views=views,
            thesis_ids=tuple(sorted(s.thesis_id for s in members)),
            score=_score(weights, proximity=rep.proximity, depth=rep.depth,
                         reward_risk=rep.reward_risk, agreement_count=len(views),
                         freshness=freshness, trend_alignment=rep.trend_alignment),
        ))
    return tuple(sorted(candidates, key=lambda c: (-c.score, c.asset, c.direction)))


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
    manifesto bounds it on the macro timeframe; order blocks come from **daily**, the structure
    timeframe the break is set on.
    """
    upto = tuple(bar for bar in daily if bar.date <= as_of)
    if not upto:
        return None

    live = tuple(
        block for block in order_blocks(daily, as_of=as_of, width=width)
        if invalidated_on(block, upto) is None
    )
    return Context(
        as_of=as_of,
        price=upto[-1].close,
        weekly_trend=trend_state(weekly, as_of=as_of, width=width),
        daily_trend=trend_state(daily, as_of=as_of, width=width),
        dealing_range=resolve_dealing_range(weekly, as_of=as_of, width=width),
        zones=tuple(
            Zone(block=block, structural_target=_extreme_since(upto, block))
            for block in live
        ),
        atr=atr(upto, len(upto) - 1),
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
    zone = _newest_zone(context.zones, family)
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

    # ── target: theirs if reasonable, else structure ──
    stated = read_target(row, published_close)
    sign = 1 if family == BULLISH else -1
    target, target_source = None, ""
    if not stated.abstained and _reasonable(
        stated.target, entry=entry, risk=risk, sign=sign,
        atr_now=context.atr,
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
    if reward_risk < min_reward_risk:
        return refuse("reward_risk_too_low")

    depth = block.depth_at(context.price) or 0.0
    proximity = proximity_to(block, context.price)
    return Setup(
        thesis_id=ident, asset=asset, person=person, direction=direction,
        timeframe=timeframe, published_at=published.isoformat(), block=block,
        entry=entry, entry_top=block.top, entry_bottom=block.bottom,
        stop=block.stop, invalidation=block.invalidation,
        target=target, target_source=target_source,
        reward_risk=reward_risk, depth=depth, proximity=proximity,
        freshness=freshness, trend_alignment=trend_alignment,
        weekly_trend=context.weekly_trend, daily_trend=context.daily_trend,
        # Domain comes off the row so a cross-domain ticker collision can't leak a crypto
        # market-cap rank onto a stock or a macro instrument. See tier_for.
        zone=zone_label or "",
        tier=tier_for(asset_rank, domain=getattr(row, "domain", "crypto")),
        score=_score(weights, proximity=proximity, depth=depth,
                     reward_risk=reward_risk, agreement_count=agreement_count,
                     freshness=freshness, trend_alignment=trend_alignment),
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


def _newest_zone(zones, family: str) -> Zone | None:
    """The most recently confirmed live zone on the given side.

    Selected by ``confirmed_at`` rather than sequence position so the caller's ordering can
    never change the answer — structure is defined by the most recent level.
    """
    matching = [zone for zone in zones if zone.block.kind == family]
    if not matching:
        return None
    return max(matching, key=lambda zone: (zone.block.confirmed_at, zone.block.index))


def _reasonable(target, *, entry: float, risk: float, sign: int,
                atr_now: float | None, min_reward_risk: float,
                max_target_atr: float) -> bool:
    """Whether a stated target is worth believing over the structural one.

    Three ways to fail: it's behind the entry (so it isn't a target any more, whatever it was
    when stated), it doesn't clear the stop by enough to be worth taking, or it's further away
    than the instrument plausibly travels.

    An unknown ATR skips the distance check rather than failing it — inability to judge must
    not read as a verdict, the same rule ``imbalance.is_displacement`` follows.
    """
    if (target - entry) * sign <= 0:
        return False
    if abs(target - entry) / risk < min_reward_risk:
        return False
    if atr_now and abs(target - entry) > max_target_atr * atr_now:
        return False
    return True
