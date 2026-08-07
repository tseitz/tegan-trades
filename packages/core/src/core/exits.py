"""Where a trade gets out — every level between entry and the far side, nearest first.

**A target has to be somewhere price can actually get to.** The engine gated entries on
structure from the start and then let the target ignore structure entirely: it took whichever
number an author happened to say, or failing that the post-break extreme, and asked neither
one what stood in the way. On the live queue that put **31 of 49 candidates** on a target
sitting past a live *opposing* order block, 22 of them with structural targets — so this was
never an "authors are optimistic" problem. See ``scripts/probe_target_reachability.py``.

The case that produced this module: a LINK long with a stated target of 11.70 (R:R 9.29) read
from one thesis's level bag ``[6.31, 11.7, 15.0, 20.0]``, while its own zone's post-break
extreme sat at 8.907 (R:R 2.31), a live weekly *bearish* block spanned 9.090–10.867 directly
in the path, and the weekly dealing range topped out at 10.867. The trade was being offered a
target above the entire range, on the far side of supply the engine had already found.

**Three structural sources, plus the author's number only when it is the nearest of them.**

- ``OPPOSING`` — the near edge of a live order block on the other side. Price runs *into* a
  block, so for a long that is its ``bottom``: the first place the move meets sellers. This is
  what the roster describes as a breaker or a "breakdown level" — the level price broke down
  from and returns to — and it is a place to be out, not a place to hold through.
- ``EXTREME`` — the zone's own post-break extreme, i.e. how far price already ran. Usually the
  nearest cluster of prior highs, which is what makes it a liquidity level and not just a
  number.
- ``RANGE_BOUND`` — the dealing range's boundary on the trade's side: the external liquidity
  the whole range is oscillating between, and the last honest level before a target becomes a
  forecast.

An authored target is kept **only when it is nearer than every structural level**. It is not
a better source, it is a *different* one — measured against structure it is not systematically
further, it is unrelated, landing further 9 times in 12 with a median gap of +1.73 R and a
worst of +8.61 R. Letting it override the nearest obstacle is what put 11.70 on that card;
letting it come *in front of* one is fine, because a shorter claim costs nothing.

**Nearest first is the whole ordering**, and which of those levels is the *target* is a
separate question answered by ``select_target``. Nothing here decides how much to take at
each; that is a sizing question and this module does no arithmetic beyond distance.

**Where you entered decides where you exit.** The roster's methodology is explicit and it is
the inverse of picking the nearest level. TraderMayne, ``NSTmMdnQg7Y``: "An order block itself
is internal range liquidity, so generally we're going to be targeting external range
liquidity." And ``v2CY0WvFj8o`` names taking the nearest level as the mistake — "it's going to
take out this buy-side liquidity here *that's still within the range*, and they're going to
close their trade… and they're going to go, 'What the heck? I closed early.'" The internal
levels are pit stops; the range boundary is the destination.

So the rule is a fork on where the entry rests, and ``v2CY0WvFj8o`` gives the test verbatim:
"if it's outside of the current dealing range, that is your external range liquidity."

- entry **inside** the range → internal entry → target the boundary, everything nearer is a
  partial.
- entry **outside** it → external sweep → "if you're entering based on external, ultimately
  you're targeting internal", i.e. the nearest level, which is the pre-existing behaviour.

Measured on the live queue before this existed: 36 of 52 candidates were internal entries
being quoted against the nearest obstruction, median R:R understated by 0.89 and worst by
7.13. See ``scripts/probe_external_target.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.structure import BEARISH, BULLISH

# Structural sources. Alongside ``core.levels.STATED``/``NEAREST``, which arrive as the
# ``authored_source`` and are passed straight through so provenance survives.
OPPOSING = "opposing_zone"
EXTREME = "extreme"
RANGE_BOUND = "range_bound"

# Which reason wins when two sources land on one price — the most specific first. An opposing
# block states *why* price would stop there; a range boundary only says where the range ends.
_SPECIFICITY = (OPPOSING, RANGE_BOUND, EXTREME)

# Everything price structure supplies on its own. The complement is what a person said, which
# is the distinction ``core.setups.collapse`` picks a representative on — kept here beside the
# constants so a fourth structural source can never be added without that predicate following.
STRUCTURAL_KINDS = frozenset(_SPECIFICITY)

# How far from entry an opposing block must sit to be an obstruction rather than the far side
# of the entry's own congestion, in risk units so it scales with the zone.
#
# Measured, not chosen: with no floor, 48 of 49 live candidates reported an obstruction, most
# of them a bearish block a fifth of an R above a bullish zone — the two halves of one piece of
# congestion, which is where a zone sits by construction. At 1.0 R it is 31 of 49, and the
# survivors are levels a chart reader would actually mark. TUNE.
MATERIAL_R = 1.0

# Which side of the dealing range the entry rests on — see the module docstring.
INTERNAL = "internal"
EXTERNAL = "external"

# How far past the boundary an entry may sit and still be a *sweep* rather than evidence the
# range is the wrong one, in range-widths.
#
# A sweep is price poking just beyond the boundary and reversing, which is a small fraction of
# a width. An entry a whole width out is not that: it is a zone from a different price regime
# than the range it is being measured against, and filling it would require the breakout that
# redraws the range — "as price breaks out of this range, it forms a new range". The target
# would be computed from a range that no longer exists by the time the order fills.
#
# Chosen, not discovered: the live distribution runs continuously from 0.00 to 3.92 widths with
# no natural break. At 0.25 it splits the 11 live external entries 6/5 — keeping ENA (0.00),
# AR (0.03), OIL (0.03), ORCL (0.11), CHINA (0.24) and STABLE (0.24), refusing SMH (0.35),
# CLSK (0.41), TSM (0.48), AVAX (1.19) and SOL (3.92, a short whose entry sat almost four
# widths above a range price was trading inside). TUNE.
MAX_SWEEP_WIDTHS = 0.25


@dataclass(frozen=True, slots=True)
class EntryLiquidity:
    """Which kind of liquidity the entry rests on, and how far outside the range it sits.

    ``widths`` is signed and always meaningful: negative inside the range, 0.0 exactly on the
    boundary, positive outside. Carried alongside ``kind`` rather than recomputed because the
    magnitude is what separates a sweep from a stale range, and the caller records it so the
    threshold can be re-tuned against outcomes rather than re-argued.
    """
    kind: str        # INTERNAL | EXTERNAL
    widths: float


def entry_liquidity(*, entry: float, dealing_range, sign: int) -> EntryLiquidity:
    """Whether ``entry`` rests inside the dealing range or beyond its boundary.

    Anchored on the entry rather than on price deliberately. ``DealingRange.permits`` tests
    where price *is*; this asks where the resting order *sits*, and the two disagree on 11 of
    52 live candidates — price inside its range with the zone beyond the boundary.

    ``dealing_range`` is duck-typed on ``low``/``high``, matching ``exit_levels``.
    """
    width = dealing_range.high - dealing_range.low
    if width <= 0:
        # A degenerate range cannot place anything relative to itself. Reporting INTERNAL
        # would be a fabricated reading of exactly the kind this module is here to remove;
        # callers refuse such a range long before this, so the answer only has to be honest.
        return EntryLiquidity(kind=EXTERNAL, widths=0.0)
    beyond = (dealing_range.low - entry) if sign > 0 else (entry - dealing_range.high)
    widths = beyond / width
    # Exactly on the boundary is external: ENA measured 0.00 widths out, and sitting *on* the
    # external liquidity is the purest sweep there is, not an interior entry.
    return EntryLiquidity(kind=EXTERNAL if widths >= 0 else INTERNAL, widths=widths)


def is_sweep(liquidity: EntryLiquidity, *,
             max_sweep_widths: float = MAX_SWEEP_WIDTHS) -> bool:
    """Whether an external entry is close enough to the boundary to be a genuine sweep."""
    return liquidity.kind == EXTERNAL and liquidity.widths <= max_sweep_widths


def select_target(levels, liquidity: EntryLiquidity):
    """Split ``levels`` into the target and everything else, per the entry's liquidity.

    Returns ``(target, ladder)``, or ``None`` when no target exists — which for an internal
    entry means no external range liquidity lies beyond it, and is a refusal rather than a
    reason to fall back to an internal level: "if there's no clear liquidity, there is no
    trade" (``Tv6DJTNobJ4``, disqualifier #3).

    The returned ``ladder`` means different things on the two branches, and deliberately so:
    partials *below* an internal entry's boundary target, runners *beyond* an external entry's
    nearest target. Both are "every other level this trade could come off at", which is what
    the renderer and the decisions sidecar want; neither needs to know which branch produced
    it, because each rung's own price says which side of the target it is on.
    """
    if not levels:
        return None
    if liquidity.kind == EXTERNAL:
        return levels[0], tuple(levels[1:])
    # ``_truncate_at_range`` drops everything past the boundary and keeps the boundary itself,
    # so when a RANGE_BOUND rung exists it is the furthest one. Found by kind rather than by
    # taking the last element: without a range there is no boundary rung at all, and the last
    # level would then be some opposing block masquerading as the destination.
    boundary = next((lv for lv in levels if lv.kind == RANGE_BOUND), None)
    if boundary is None:
        return None
    return boundary, tuple(lv for lv in levels if lv.price != boundary.price)


@dataclass(frozen=True, slots=True)
class ExitLevel:
    """One place the trade could come off, and why.

    ``reward_risk`` is carried rather than derived on read because every consumer wants it and
    none of them should have to re-derive risk from a stop they may not be holding.
    """
    price: float
    kind: str            # OPPOSING | EXTREME | RANGE_BOUND | STATED | NEAREST
    reward_risk: float


def exit_levels(
    *,
    entry: float,
    stop: float,
    sign: int,
    zones=(),
    dealing_range=None,
    structural_target: float | None = None,
    authored: float | None = None,
    authored_source: str = "",
    material_r: float = MATERIAL_R,
) -> tuple[ExitLevel, ...]:
    """Every exit level beyond ``entry`` on the trade's side, ordered nearest first.

    ``sign`` is +1 for a long and -1 for a short, matching ``core.setups``' ``family``-derived
    sign rather than a raw direction label. ``zones`` is duck-typed on ``zone.block`` exposing
    ``kind``/``top``/``bottom`` — deliberately not importing ``core.setups.Zone``, which would
    make the dependency circular and buys nothing.

    Empty is a legitimate answer and means the caller has no target: everything structure knows
    about lies behind the entry. The caller refuses on that, exactly as it did before.
    """
    risk = abs(entry - stop)
    if not risk:
        # A zero-height risk makes every ratio here infinite. ``core.setups`` already refuses
        # a degenerate zone before reaching this point; raising keeps a future caller from
        # silently manufacturing one.
        raise ValueError("exit_levels needs a non-zero risk: entry and stop coincide")

    beyond = lambda price: (price - entry) * sign > 0   # noqa: E731 — one predicate, used 4x

    found: list[tuple[float, str]] = []

    opposing_kind = BEARISH if sign > 0 else BULLISH
    for zone in zones:
        block = zone.block
        if block.kind != opposing_kind:
            continue
        # The edge price meets first on the way out: a long runs up into the block's bottom.
        edge = block.bottom if sign > 0 else block.top
        if not beyond(edge) or abs(edge - entry) / risk < material_r:
            continue
        found.append((edge, OPPOSING))

    if dealing_range is not None:
        bound = dealing_range.high if sign > 0 else dealing_range.low
        if beyond(bound):
            found.append((bound, RANGE_BOUND))

    if structural_target is not None and beyond(structural_target):
        found.append((structural_target, EXTREME))

    if authored is not None and beyond(authored):
        nearest = min((abs(price - entry) for price, _ in found), default=None)
        if nearest is None or abs(authored - entry) < nearest:
            found.append((authored, authored_source))

    return tuple(
        ExitLevel(price=price, kind=kind, reward_risk=abs(price - entry) / risk)
        for price, kind in sorted(_truncate_at_range(found, sign=sign).items(),
                                  key=lambda item: abs(item[0] - entry))
    )


def _truncate_at_range(found: list[tuple[float, str]], *, sign: int) -> dict[float, str]:
    """Drop every level past the dealing range's boundary, which is where this trade ends.

    The boundary is the external liquidity the range oscillates between; beyond it the range
    has broken and a new one forms, at which point the setup is re-derived from scratch rather
    than run further. Without this, a long on LINK listed five runners between +61% and +178% —
    the near edges of old weekly supply blocks stacked above a range that tops out at +36% —
    which is the far-target problem this module exists to remove, wearing a different label.

    A realized extreme sitting past the boundary is dropped by the same rule. That can only
    happen while the range lags the swing that would redraw it, and deferring to the range is
    the conservative side of that disagreement.
    """
    boundary = next((price for price, kind in found if kind == RANGE_BOUND), None)
    if boundary is None:
        return _dedupe(found)
    return _dedupe([(price, kind) for price, kind in found
                    if (price - boundary) * sign <= 0])


def _dedupe(found: list[tuple[float, str]]) -> dict[float, str]:
    """One rung per price, labelled by the most specific reason that landed on it.

    Keyed on the float exactly: two sources agreeing to the cent are the same level (a block
    edge cut from the swing that bounds the range), while two that merely sit close are two
    levels and collapsing them would hide one.
    """
    def rank(kind: str) -> int:
        return _SPECIFICITY.index(kind) if kind in _SPECIFICITY else len(_SPECIFICITY)

    best: dict[float, str] = {}
    for price, kind in found:
        if price not in best or rank(kind) < rank(best[price]):
            best[price] = kind
    return best
