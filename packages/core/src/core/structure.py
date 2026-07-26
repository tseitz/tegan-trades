"""Market structure primitives — the computable half of the trading manifesto.

Pure and duck-typed exactly like ``core.grade``: the caller supplies any *sequence* of bars
exposing ``date``/``open``/``high``/``low``/``close`` (``oracle.series.Bar`` satisfies it),
and nothing here does I/O or mutates anything. ``core`` deliberately does not depend on
``oracle``, which is why these functions never name a concrete bar type.

**Swings are the foundation.** The dealing range, the trend-state machine and break of
structure are all defined in terms of them, so an error here propagates into every construct
downstream. That is the whole reason this module starts with them rather than with the
constructs anyone actually wants.

**Why every swing carries ``confirmed_at``.** A swing needs ``width`` bars on *both* sides to
exist, so on the bar that forms it a swing is not merely unconfirmed — it is unknowable.
``date`` is when it formed; ``confirmed_at`` is the earliest date a caller could legitimately
have acted on it. Using a swing anywhere between those two dates is look-ahead bias: the same
failure ``oracle.series.close_on`` guards against, and just as quiet, because it inflates
results instead of raising.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Bars either side that a bar must dominate to count as a swing. 2 is the stated default;
# widening it finds fewer, more significant swings. Tunable, like ``core.rank``'s weights.
SWING_WIDTH = 2

# How many swings back ``trend_state`` reaches for its comparison. Depth 1 — the last two
# swings of each kind — reads a single leg, so any counter-trend bounce inverts the verdict.
# Measured on the cached corpus at 2026-07-25: it called ETH weekly an ``uptrend`` off +3.4%
# on highs and +0.3% on lows while highs were down 50% and lows 57%, and that verdict is what
# permits a direction in ``core.setups``. Depth 2 spans the bounce; it fixed BTC, ETH and SOL
# and cost 3 points of decisiveness across 217 assets.
TREND_DEPTH = 2

# Fractional move a swing must clear against its anchor to count as higher or lower. Without
# it, 12% of decisive verdicts on the cached corpus rested on a move under 1% — noise wearing
# a direction. Deliberately the junior partner to depth: a floor alone downgrades a wrong
# verdict to ``RANGING``, only depth makes it right. TUNE once decisions accumulate.
TREND_NOISE_FLOOR = 0.01

SWING_HIGH = "high"
SWING_LOW = "low"

BULLISH = "bullish"
BEARISH = "bearish"

UPTREND = "uptrend"
DOWNTREND = "downtrend"
RANGING = "ranging"
UPTREND_FAILED_BREAKOUT = "uptrend_failed_breakout"
DOWNTREND_FAILED_BREAKDOWN = "downtrend_failed_breakdown"


@dataclass(frozen=True, slots=True)
class Swing:
    """A confirmed swing point.

    ``index`` is the position in the bar sequence this swing was derived from — carried so
    the BOS and order-block passes can walk the bars between a swing and a later break
    without re-searching by date.
    """
    date: date
    price: float
    kind: str          # SWING_HIGH | SWING_LOW
    confirmed_at: date
    index: int


def swings(bars, *, width: int = SWING_WIDTH) -> tuple[Swing, ...]:
    """Every confirmed swing in ``bars``, ascending by date.

    ``bars`` must be an indexable sequence in ascending date order with unique dates —
    ``oracle.series.PriceSeries`` guarantees both on construction.

    Comparison is **strict**, so a plateau of equal highs yields no swing rather than several
    adjacent ones: on a flat top no single bar dominates its window, and inventing a winner
    would put a structural level on an arbitrary bar.

    A bar can legitimately be both a swing high and a swing low (an outside bar that
    engulfs its neighbours on both sides), in which case both are returned.
    """
    found: list[Swing] = []
    # Bounds enforce the invariant directly: the first and last `width` bars lack a full
    # window, so they can never be swings. This is what makes look-ahead structurally
    # impossible rather than merely discouraged.
    for i in range(width, len(bars) - width):
        bar = bars[i]
        window = bars[i - width: i + width + 1]
        others = [b for j, b in enumerate(window) if j != width]
        confirmed_at = bars[i + width].date

        if all(bar.high > other.high for other in others):
            found.append(Swing(date=bar.date, price=bar.high, kind=SWING_HIGH,
                               confirmed_at=confirmed_at, index=i))
        if all(bar.low < other.low for other in others):
            found.append(Swing(date=bar.date, price=bar.low, kind=SWING_LOW,
                               confirmed_at=confirmed_at, index=i))

    return tuple(sorted(found, key=lambda s: (s.date, s.kind)))


def confirmed_by(found, when: date) -> tuple[Swing, ...]:
    """The subset of ``found`` that was knowable on ``when``.

    Any as-of query — "what did structure look like when this thesis was published" — must
    go through this. Filtering on ``Swing.date`` instead would admit swings that had not yet
    formed their confirming bars.
    """
    return tuple(swing for swing in found if swing.confirmed_at <= when)


def position_in_range(bottom: float, top: float, price: float) -> float | None:
    """Where ``price`` sits in ``[bottom, top]`` — 0.0 at the bottom, 1.0 at the top.

    Clamped, because a price outside the range is still meaningfully "at the extreme" for
    scoring purposes. Returns None on a degenerate range: a zero-width or inverted range has
    no interior, and answering 0.5 would inject a fabricated midpoint into a score.

    One primitive serves two constructs — order-block zone depth and premium/discount — which
    are the same measurement read with different orientations. The caller orients it.
    """
    if top <= bottom:
        return None
    return max(0.0, min(1.0, (price - bottom) / (top - bottom)))


# ── trend state ─────────────────────────────────────────────────────────────

def _swing_direction(prices: list[float], depth: int, noise_floor: float) -> int:
    """+1 / -1 / 0 for the newest swing measured against the one ``depth`` swings back.

    Fractional rather than absolute so one floor serves every asset — a 1% move means the
    same thing on BTC and on a sub-cent memecoin, which an absolute threshold could not.
    Returns 0 on a non-positive anchor: no honest fraction exists, and guessing a sign there
    would put a direction on a price that cannot support one.
    """
    newest, anchor = prices[-1], prices[-1 - depth]
    if anchor <= 0:
        return 0
    change = (newest - anchor) / anchor
    if change > noise_floor:
        return 1
    if change < -noise_floor:
        return -1
    return 0


def trend_state(bars, *, as_of: date | None = None, width: int = SWING_WIDTH,
                depth: int = TREND_DEPTH,
                noise_floor: float = TREND_NOISE_FLOOR) -> str:
    """The market-structure state implied by the swing sequence.

    A trend requires agreement: higher highs *and* higher lows. Highs rising while lows fall
    is an expanding range, and reporting that as a trend would hand the cross-ref engine a
    directional bias the chart doesn't support — so it degrades to ``RANGING`` rather than
    picking a side.

    **Each side is measured against the swing ``depth`` back, not its immediate predecessor**,
    and must clear ``noise_floor`` to count. Comparing adjacent swings makes the verdict a
    function of the newest leg alone, which inverts on any bounce — see ``TREND_DEPTH``. The
    two knobs are separable on purpose: depth decides *which* structure is read, the floor
    only decides whether a move is big enough to be structure at all.

    Fewer than ``depth + 1`` swings of either kind cannot reach the anchor, so the state is
    ``RANGING``. That is an abstention, not a finding of balance — the caller treats ranging
    as permitting neither direction, which is the safe reading of "not enough structure yet".

    The failed-break states are the other half of the same idea: an attempt beyond the level
    that couldn't close there. A break that *holds* is a break of structure (see ``breaks``);
    a break that fails is the counter-trend setup.
    """
    found = swings(bars, width=width)
    if as_of is not None:
        found = confirmed_by(found, as_of)

    highs = [s for s in found if s.kind == SWING_HIGH]
    lows = [s for s in found if s.kind == SWING_LOW]
    if len(highs) <= depth or len(lows) <= depth:
        return RANGING

    high_dir = _swing_direction([s.price for s in highs], depth, noise_floor)
    low_dir = _swing_direction([s.price for s in lows], depth, noise_floor)

    if high_dir > 0 and low_dir > 0:
        failed = _attempt_failed(bars, highs[-1], BULLISH, as_of)
        return UPTREND_FAILED_BREAKOUT if failed else UPTREND
    if high_dir < 0 and low_dir < 0:
        failed = _attempt_failed(bars, lows[-1], BEARISH, as_of)
        return DOWNTREND_FAILED_BREAKDOWN if failed else DOWNTREND
    return RANGING


def _attempt_failed(bars, swing: Swing, kind: str, as_of: date | None) -> bool:
    """Did price reach beyond ``swing`` without ever closing beyond it?

    Only bars after the swing formed are considered. Bars *within* the confirmation window
    can't qualify anyway — a confirmed swing high dominates the highs either side of it — but
    scanning from the swing forward states the intent rather than relying on that.
    """
    exceeded = False
    for bar in bars[swing.index + 1:]:
        if as_of is not None and bar.date > as_of:
            break
        if kind == BULLISH:
            if bar.close > swing.price:
                return False  # it closed through: a real break, not a failed one
            exceeded = exceeded or bar.high > swing.price
        else:
            if bar.close < swing.price:
                return False
            exceeded = exceeded or bar.low < swing.price
    return exceeded


# ── break of structure ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Break:
    """A close beyond the most recent opposing swing — structure broken, not merely tested.

    ``origin`` is the swing the breaking move started from, and it is what makes a break
    tradeable: it supplies the invalidation level and therefore the stop. It is optional
    because a break can occur before any opposing swing has confirmed, and inventing a level
    there would be worse than reporting none — the cross-ref engine refuses candidates
    without a stop, which is the guardrail for "I stay in way too long with losers".
    """
    kind: str          # BULLISH | BEARISH
    level: float       # the swing price that was closed through
    swing: Swing       # the swing that was broken
    origin: Swing | None
    date: date
    index: int


def breaks(bars, *, as_of: date | None = None, width: int = SWING_WIDTH) -> tuple[Break, ...]:
    """Every break of structure in ``bars``, ascending by index.

    A break is **close**-based, per the manifesto: "a close above previous range highs". A
    wick through the level is a failed breakout, and counting it here would invert the signal.

    Only the *most recent* confirmed swing is live as a target at any moment. Without that,
    one strong close through a cluster of old swing highs would emit a break per high, and
    each would mint its own duplicate order block.
    """
    found = swings(bars, width=width)
    highs = tuple(s for s in found if s.kind == SWING_HIGH)
    lows = tuple(s for s in found if s.kind == SWING_LOW)

    result = _scan_breaks(bars, highs, lows, BULLISH) + _scan_breaks(bars, lows, highs, BEARISH)
    if as_of is not None:
        result = tuple(b for b in result if b.date <= as_of)
    return tuple(sorted(result, key=lambda b: (b.index, b.kind)))


def _scan_breaks(bars, targets, origins, kind: str) -> tuple[Break, ...]:
    out: list[Break] = []
    pending: Swing | None = None
    next_target = 0

    for i in range(len(bars)):
        bar = bars[i]
        # Advance to the newest target knowable on this bar. Older unbroken swings are
        # deliberately dropped: structure is defined by the most recent level, not every
        # historical one.
        while next_target < len(targets) and targets[next_target].confirmed_at <= bar.date:
            pending = targets[next_target]
            next_target += 1
        if pending is None:
            continue

        broke = bar.close > pending.price if kind == BULLISH else bar.close < pending.price
        if not broke:
            continue

        out.append(Break(
            kind=kind, level=pending.price, swing=pending,
            origin=_origin_before(origins, i, bar.date),
            date=bar.date, index=i,
        ))
        pending = None

    return tuple(out)


def _origin_before(origins, index: int, when: date) -> Swing | None:
    """The latest opposing swing knowable at the break — the low the up-move came from."""
    eligible = [s for s in origins if s.index < index and s.confirmed_at <= when]
    return eligible[-1] if eligible else None


# ── order blocks ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class OrderBlock:
    """The candle price is expected to return to, anchored to the break that created it.

    Not merely "the down candle before an up move" — the down candle before the up move that
    *broke structure*. The qualifier is structural, not magnitude: "did it close beyond the
    previous range extreme" is binary and self-limiting, where a size threshold is neither.
    Without any qualifier the construct is meaningless, since most up moves on any chart are
    preceded by a down candle.

    ``confirmed_at`` is the break's date, not the candle's. The candle is unremarkable until
    the break happens, so acting on it earlier is look-ahead — the same discipline
    ``Swing.confirmed_at`` enforces.
    """
    kind: str          # BULLISH | BEARISH
    top: float         # the full candle, high to low — not the body, not a half
    bottom: float
    date: date         # the order-block candle
    index: int
    confirmed_at: date  # when the break made it knowable
    bos: Break
    invalidation: float | None

    @property
    def near_edge(self) -> float:
        """The edge price arrives at first — the shallowest possible fill."""
        return self.top if self.kind == BULLISH else self.bottom

    @property
    def stop(self) -> float:
        """Where a *trade* on this zone is wrong: the far edge, exactly.

        Deliberately distinct from ``invalidation``, which is where the *zone* dies. Conflating
        them was measured and rejected: pricing risk all the way down to the origin swing low
        put 17 of 18 live candidates under 1.0 reward-to-risk. The zone surviving a stop-out is
        the correct behaviour — structure hasn't broken just because one entry failed.

        **There is no buffer, and the omission is real rather than intended.** This returns the
        edge itself, so ``Setup.stop`` equals ``Setup.entry_bottom`` on every long: a wick one
        tick through the zone is a stop-out, and a fill at the far edge is a zero-risk trade
        whose reward-to-risk divides by ~0.

        A buffer does not belong here. Padding by a fraction of the zone's own height would give
        the *tightest* zones the smallest cushion, which is backwards — a 5.51-wide daily block
        needs relatively more protection from noise than a 32-wide weekly one, not less. The
        yardstick has to be ATR, and ATR is not knowable from a block; it lives on
        ``core.setups.Context``. So the fix belongs in ``cross_reference``, where both are in
        scope and ``Setup.stop`` is already a separate field from this one. Tracked as §7 in
        ``docs/IMPROVEMENTS.md``, gated on measuring the multiplier rather than guessing it —
        padding widens risk, so it lowers every reward-to-risk and silently drops candidates
        through ``MIN_REWARD_RISK``.
        """
        return self.bottom if self.kind == BULLISH else self.top

    def depth_at(self, price: float) -> float | None:
        """How far into the zone ``price`` has travelled — 0 at the near edge, 1 at the far.

        A bullish zone is approached from above, so depth grows downward; bearish is the
        mirror. Deliberately not a boolean: "more confidence as you dive deeper into that
        candle", and a graded depth is where fib levels land later without redefining
        anything.
        """
        position = position_in_range(self.bottom, self.top, price)
        if position is None:
            return None
        return 1.0 - position if self.kind == BULLISH else position

    def touches(self, bar) -> bool:
        """Whether ``bar`` traded into the zone at all — a wick is enough.

        Price only has to reach the resting orders. Note the deliberate asymmetry with
        ``breaks``, which is close-based: a structural break needs commitment, a retest
        does not.
        """
        return bar.low <= self.top and bar.high >= self.bottom


def order_blocks(bars, *, as_of: date | None = None,
                 width: int = SWING_WIDTH) -> tuple[OrderBlock, ...]:
    """The order block created by each break of structure, ascending by break.

    Breaks with no qualifying candle are skipped rather than approximated.
    """
    found = []
    for bos in breaks(bars, width=width):
        block = _block_for(bars, bos)
        if block is not None:
            found.append(block)
    if as_of is not None:
        found = [b for b in found if b.confirmed_at <= as_of]
    return tuple(found)


def _block_for(bars, bos: Break) -> OrderBlock | None:
    """Walk back from the break for the last candle opposing it.

    The search starts one bar *before* the break: the breaking candle is part of the move,
    not the block, and it needn't be an up candle at all (a gap-up bar can close red and
    still close above the level). The floor is the origin swing, since the move cannot have
    begun before the low it came from.
    """
    floor = bos.origin.index if bos.origin is not None else 0
    for i in range(bos.index - 1, floor - 1, -1):
        bar = bars[i]
        opposes = bar.close < bar.open if bos.kind == BULLISH else bar.close > bar.open
        if not opposes:
            continue
        return OrderBlock(
            kind=bos.kind, top=bar.high, bottom=bar.low, date=bar.date, index=i,
            confirmed_at=bos.date, bos=bos,
            invalidation=_invalidation_for(bos, top=bar.high, bottom=bar.low),
        )
    return None


def _invalidation_for(bos: Break, *, top: float, bottom: float) -> float | None:
    """The origin swing, pulled to the zone's far edge when it lands *inside* the zone.

    Usually a no-op: the move began at a low below the down-candle it later left behind, so the
    origin sits beyond the block and is returned untouched.

    It is not a no-op on wick-heavy candles, and the cause is look-ahead avoidance rather than
    bad data. ``_origin_before`` may only use swings already *confirmed* at the break, so when
    the low a move truly began at is confirmed a bar too late, the search falls back to a much
    older swing — on live GOOGL weekly bars, a block spanning 273.95–305.98 whose origin was a
    three-month-old swing at 296.12, inside the block and above its own stop. Left alone the
    zone dies before a trade on it does, inverting the relationship the rest of the module
    assumes: ``invalidation`` is meant to outlive ``stop``, not precede it.

    The honest caveat is that clamping reports a level no swing actually marks. The alternative
    — discarding the block — throws away real structure over an artifact of aggregation, and
    landing the two together at least means the zone and any trade on it die on the same close,
    which is the conservative direction to be wrong in.
    """
    if bos.origin is None:
        return None
    return (min(bos.origin.price, bottom) if bos.kind == BULLISH
            else max(bos.origin.price, top))


def invalidated_on(block: OrderBlock, bars) -> date | None:
    """The date the block died, or None if it is still live in ``bars``.

    Death is a **close** beyond the invalidation level — the swing the move originated from.
    Breaking that low breaks the higher-low sequence, so the trend assumption itself is gone.

    Note what this is *not*: the block cannot be invalidated by price returning below the
    range high it broke, because reaching the block *requires* coming back below that high.
    That reading would kill every setup before it could ever trigger.
    """
    if block.invalidation is None:
        return None
    for bar in bars[block.bos.index + 1:]:
        if block.kind == BULLISH and bar.close < block.invalidation:
            return bar.date
        if block.kind == BEARISH and bar.close > block.invalidation:
            return bar.date
    return None
