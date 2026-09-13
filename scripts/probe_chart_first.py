"""What would a chart-first card look like? A spike, not a feature.

`setups` builds candidates from roster theses and lets structure only veto, so an asset nobody
called is an asset with no card. This inverts it: every live level near price, no gates, with
the roster and the outside data hung off each one as data points rather than as gatekeepers.

    uv run python scripts/probe_chart_first.py GOLD SOL

Free — reads the price cache, the thesis and stance stores, and `data/altsignal/`. No network,
no LLM, no order. Nothing imports this module and nothing in `scripts/nightly.sh` runs it.

**What it showed on 2026-09-12.** GOLD carries five live bullish H12 order blocks. `_newest_zone`
(`core.setups`) takes one zone per timeframe — the newest — which is 4373.9-4421.5, the one price
is standing in. The other four are 4163.8, 4078.1, 4047.4 and 4030.2, and all four already print
on the live queue card as the *short's* take-profit ladder, labelled `opposing_zone`. The engine
locates the buy zones and will only sell into them.

**Zones are read on the setup rung, the way `oracle.assemble` does it, not the way `review` does.**
`review.cli.build_readings` passes daily bars with no `setup_timeframe`, so it reads GOLD on the
daily; `oracle.assemble` measures the rung and reads it on H12. Those are different bands with
different risk. A spike that disagreed with the queue about which zones exist could not inform a
decision about replacing the queue. (That the two production paths disagree is a real finding
about the repo, and is not this probe's to fix.)

**Read the header before the table.** Four silent failures all print a clean, plausible card: a
non-canonical asset name makes a live roster look silent, the wrong rung makes different zones
look like the same ones, a cold price cache makes a busy chart look quiet, and an R:R matched to
the wrong block is indistinguishable from a real one. The header states the canonical name, the
rung, the price, the level count and the folded-stance count so each of those is visible rather
than inferred.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from brain.retrieve import fold_stances
from brain.stance_store import load_all_stances
from core.canon import load_registry, resolve_asset
from core.dealing_range import dealing_range
from core.nearby import ALL_KINDS, GAP, RANGE_EDGE, REACH, SUPPORT, Level, levels_near
from core.review import roster_lean
from core.setups import (
    STOP_PAD_ATR,
    Context,
    build_context,
)
from core.structure import BULLISH as BLOCK_BULLISH
from oracle import (
    altsignal_config,
    altsignal_store,
    cache,
    carry,
    corpus,
    listings,
    trigger_feed,
)
from oracle.resample import to_weekly
from oracle.route import Priceable, load_routing_table, route
from review.altsignal import macro_block
from review.levels import _rank

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "cfg"

# How far back the "calls tier" tally reaches. 30 days is the window a reader means by "lately",
# and it is the contrast that matters here: the stance tier already folds to a current view,
# while the thesis tier keeps every call ever made and needs a window to say anything about now.
RECENT_DAYS = 30


def build(asset: str, *, as_of: date, registry, table):
    """``(canonical, rung, context, ranges)`` on the setup rung, mirroring ``oracle.assemble``.

    Returns ``context=None`` when the asset does not route or has no cached bars — two
    different reasons a card would be empty, and the caller reports which.

    ``ranges`` is ``[(label, DealingRange | None), ...]`` — the same calculation run on each
    timeframe separately. ``Context`` carries only one, the weekly, because that is the one the
    gate reads; see ``range_block`` for why the others are worth printing beside it.
    """
    canonical = resolve_asset(asset, registry)[0]
    resolved = route(canonical, table)
    if not isinstance(resolved, Priceable):
        return canonical, None, None, ()
    from oracle.assemble import load_daily

    daily = load_daily(resolved, table=table, series_cache={})
    if daily is None:
        return canonical, None, None, ()
    weekly = to_weekly(daily)
    rung, rung_series = trigger_feed.setup_rung(trigger_feed.load_cached(resolved))
    bars = daily.bars if rung_series is None else rung_series.bars
    context = build_context(bars, weekly.bars, as_of=as_of, setup_timeframe=rung)

    # Keyed by label rather than appended blindly: a non-straddling asset gets ``DAILY`` as its
    # rung, and printing "daily" twice would read as two measurements agreeing.
    by_label = {"weekly (gated)": weekly.bars, "daily": daily.bars}
    if rung_series is not None:
        by_label[rung] = rung_series.bars
    ranges = [(label, dealing_range(tf_bars, as_of=as_of))
              for label, tf_bars in by_label.items()]
    return canonical, rung, context, ranges


def block_for(level: Level, context: Context):
    """The ``OrderBlock`` a zone level was drawn from, or None for a gap or a range edge.

    Matched on the exact edges ``core.nearby._level`` copied off the block, so this is an
    identity lookup rather than a proximity guess — the one place a heuristic here could put a
    believable R:R against the wrong zone.
    """
    if level.kind in (GAP, RANGE_EDGE):
        return None
    for zone in context.zones:
        if (zone.timeframe == level.timeframe
                and zone.block.top == level.top and zone.block.bottom == level.bottom):
            return zone.block
    return None


def opposing(entry: float, context: Context, *, sign: int, reach: float) -> float | None:
    """The nearest block in the other direction, beyond ``entry``. The exit, approximately.

    **Approximate on purpose, and not a substitute for ``core.exits``.** That module picks a
    target from a full ladder — stated levels, extremes, the range bound — and reproducing it
    here would be a second copy free to disagree with the queue. What this answers is only "what
    stands in the way", which is enough to tell a 3R level from a 0.5R one.

    **Bounded by the same window the table is.** "Nearest opposing" alone is unbounded backwards
    through history: the nearest bullish block below GOLD's 4018 zone is a 2024 block at 2670.9,
    which priced that short at 10.59R on the first run. A target the card would not itself list
    as a level cannot be the target of a trade the card is offering, so it reports no ratio
    rather than a large one.
    """
    want = BLOCK_BULLISH if sign < 0 else "bearish"
    beyond = [
        zone.block.near_edge for zone in context.zones
        if zone.block.kind == want
        and (zone.block.near_edge > entry if sign > 0 else zone.block.near_edge < entry)
        and abs(zone.block.near_edge - context.price) / context.price <= reach
    ]
    if not beyond:
        return None
    return min(beyond) if sign > 0 else max(beyond)


def reward_risk(level: Level, context: Context, *, sign: int, reach: float):
    """``(entry, stop, target, r:r)`` for a trade off ``level``'s band, or None if it has none.

    **Priced from the band, not from the block, and that is the whole point of the spike.**
    ``OrderBlock.near_edge`` is the top for a bullish block and the bottom for a bearish one, so
    it answers "which edge does price meet first" only for the direction the block was drawn
    for. Asking it for the other direction returns the far edge and produced an 11.67R long off
    a bearish zone on the first run — the broken denominator ``core.setups`` warns about, wearing
    a plausible number. A band read direction-lessly has no such asymmetry: a long fills at the
    top and is wrong below the bottom, a short fills at the bottom and is wrong above the top.

    A range edge is a single price, so it has no band and therefore no stop. It gets no ratio.
    Padding alone would still yield a positive risk — on the first run that priced the 3962.5
    range low at 19.24R — so the refusal is on the band being degenerate, not on the arithmetic
    failing. Same reasoning as ``core.setups``' ``degenerate_zone``.
    """
    if level.top == level.bottom:
        return None
    entry, far = (level.top, level.bottom) if sign > 0 else (level.bottom, level.top)
    # The same cushion ``core.setups._padded_stop`` applies, recomputed rather than called: that
    # helper takes an ``OrderBlock`` and reads ``block.stop``, which carries the same
    # direction-baked-in asymmetry as ``near_edge``.
    pad = 0.0 if context.atr is None else STOP_PAD_ATR * context.atr
    stop = far - sign * pad
    risk = abs(entry - stop)
    target = opposing(entry, context, sign=sign, reach=reach)
    if not risk or target is None:
        return None
    return entry, stop, target, abs(target - entry) / risk


def roster_block(canonical: str, *, as_of: date, registry, folded_by_asset) -> list[str]:
    """The two roster tiers, side by side, because they can and do disagree.

    The stance tier (`data/stances/`) folds to each person's *current* view and is what `review`
    and `digest` read. The thesis tier (`data/theses/`) is what `setups` reads, and it is the only
    one the queue has ever seen. On GOLD they pointed opposite ways.
    """
    folded = folded_by_asset.get(canonical, ())
    lean = roster_lean(folded, as_of=as_of)
    thin = " · THIN" if lean.thin else ""
    age = "never" if lean.age_days is None else f"{lean.age_days}d old"
    out = [f"stance tier   {lean.lean:8} {lean.bulls} bulls / {lean.bears} bears "
           f"· {lean.people} people · {age}{thin}"]

    cutoff = (as_of - date.resolution * RECENT_DAYS).isoformat()
    recent = [r for r in corpus.iter_rows(registry)
              if r.asset == canonical and r.published_at >= cutoff]
    longs = sum(1 for r in recent if r.direction == "long")
    shorts = sum(1 for r in recent if r.direction == "short")
    out.append(f"calls tier    last {RECENT_DAYS}d: {longs} long / {shorts} short "
               f"· {len(recent)} theses")
    return out


def context_block(context: Context) -> list[str]:
    """Structure — the same facts the queue gates on, reported rather than ranked."""
    return [f"structure     weekly {context.weekly_trend} · daily {context.daily_trend}"]


def range_block(context: Context, ranges) -> list[str]:
    """The same range calculation on every timeframe, so the gated one can be checked.

    **Only the weekly row decides anything today.** ``core.setups`` draws each asset's *zones*
    on the setup rung and its *range* on the weekly (`setups.py:959`), so one decision reads two
    timeframes. Whether that is wrong is the open question this probe exists to inform, and
    printing the alternatives is how it gets answered from a week of real readings instead of
    from an argument. Nothing here gates.

    Measured 2026-09-13, and the reason the row is worth having:

    - SOL's weekly range confirmed 2026-08-16 and price has since left it entirely, so
      ``permits`` returns False for **both** directions and 53 rows produced no candidate. Its
      H12 range confirmed 2026-09-10 and puts price at 0.34 — discount.
    - GOLD sits at 0.53 of its weekly range, three hundredths into premium, on bounds confirmed
      2026-07-17. Every long is refused on that. Its H12 range puts price at 0.09.

    ``position_at`` returns None outside the range rather than clamping — a long below the low
    would otherwise read as the deepest possible discount, which is the strongest signal drawn
    from the weakest evidence. That refusal is repeated here rather than re-derived.
    """
    out = []
    for label, dealing in ranges:
        if dealing is None:
            out.append(f"range {label:14} none — fewer than two confirmed swings")
            continue
        position = dealing.position_at(context.price)
        age = f"confirmed {str(dealing.confirmed_at)[:10]}"
        where = ("OUTSIDE — price has left this range, it is due to be redrawn"
                 if position is None else
                 f"{position:.2f} → {dealing.zone_at(context.price)}")
        out.append(f"range {label:14} {dealing.low:g}-{dealing.high:g} · {age} · {where}")
    return out


def outside_block(canonical: str, *, altsignal_cfg) -> list[str]:
    """Carry, chain usage and macro odds. Every one of them reported, none of them scored."""
    out = []
    outlook = carry.outlooks_for([canonical]).get(canonical)
    out.append("carry         none observed on the default venue" if outlook is None else
               f"carry         {outlook.venue} median {outlook.median:+.4%} "
               f"· p90 {outlook.p90:+.4%} · n={outlook.n}")

    chain = next((c.chain for c in altsignal_cfg.chains if c.asset == canonical), None)
    if chain is None:
        out.append(f"chain         no `chains:` entry for {canonical} in cfg/altsignal.yaml")
    else:
        stored = altsignal_store.read(source="defillama", key=chain)
        out.append(f"chain         {chain}: no stored readings yet" if not stored else
                   f"chain         {chain} · {len(stored)} readings · "
                   f"latest {stored[-1].kind}={stored[-1].value}")

    macro = macro_block(altsignal_cfg=altsignal_cfg)
    if not macro:
        out.append("macro         no stored market readings")
    out += [f"macro         {row.why[:60]} · " +
            ", ".join(f"{name}={value:.0%}" for name, value in row.top)
            for row in macro]
    return out


_HEADERS = ("kind", "tf", "band", "side", "dist", "long", "short", "from")


def level_rows(context: Context, *, reach: float) -> list[tuple[str, ...]]:
    """One row per level, both directions priced. Never capped, never collapsed.

    ``review.levels.shortlist`` is the production shortener and is deliberately not used: it
    takes one level per holding and reports the rest as a count, which is right for a portfolio
    section and wrong for the question here. Ranking is still ``review.levels._rank`` so the
    ordering cannot drift from the one `review` prints.

    ``kind`` drops ``nearby``'s ``_zone`` suffix because the timeframe already has its own
    column, and the two disagreed out loud: an H12 block printed as ``daily_zone`` beside
    ``h12``, since ``nearby`` maps everything that is not weekly onto ``DAILY_ZONE``.
    """
    rows = []
    for level in sorted(levels_near(context, kinds=ALL_KINDS, reach=reach), key=_rank):
        block = block_for(level, context)
        band = (f"{level.bottom:g}" if level.top == level.bottom
                else f"{level.bottom:g}-{level.top:g}")
        priced = []
        for sign in (1, -1):
            trade = reward_risk(level, context, sign=sign, reach=reach)
            priced.append("—" if trade is None else
                          f"{trade[3]:.2f}R @{trade[0]:g}→{trade[2]:g}")
        rows.append((
            level.kind.removesuffix("_zone"), level.timeframe or "—", band,
            "support" if level.side == SUPPORT else "resistance",
            "inside" if level.inside else f"{level.distance:.2%}",
            *priced,
            "—" if block is None else str(block.date)[:10],
        ))
    return rows


def render_table(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(cell) for cell in column)
              for column in zip(_HEADERS, *rows, strict=True)]
    return ["    " + "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip()
            for row in (_HEADERS, *rows)]


def card(asset: str, *, as_of: date, registry, table, folded_by_asset, altsignal_cfg,
         reach: float) -> str:
    canonical, rung, context, ranges = build(
        asset, as_of=as_of, registry=registry, table=table)
    named = canonical if canonical == asset else f"{asset} → {canonical}"
    if context is None:
        return (f"\n{named} · no context — it did not route, or has no cached bars. "
                f"Warm it: uv run fetch-prices\n")

    folded = folded_by_asset.get(canonical, ())
    rows = level_rows(context, reach=reach)
    # ``nearby.REACH`` drops anything past 5% of price, and on the first run that silently hid
    # the four GOLD zones this probe exists to surface — they sit 7-8% below. A window that
    # discards without saying so is the failure this whole spike is arguing against, so the
    # count beyond it is always reported. ``--reach`` widens it.
    beyond = len(levels_near(context, kinds=ALL_KINDS, reach=1.0)) - len(rows)
    head = (f"\n{named} · rung {rung} · price {context.price:g} · as of {context.as_of} "
            f"· {len(rows)} levels within {reach:.0%} ({beyond} beyond) "
            f"· {len(folded)} stances folded")

    body = ["", "  WHAT THE DATA POINTS SAY"]
    body += [f"    {line}" for line in roster_block(
        canonical, as_of=as_of, registry=registry, folded_by_asset=folded_by_asset)]
    body += [f"    {line}" for line in context_block(context)]
    body += [f"    {line}" for line in range_block(context, ranges)]
    body += [f"    {line}" for line in outside_block(canonical, altsignal_cfg=altsignal_cfg)]

    body += ["", f"  EVERY LEVEL WITHIN {reach:.0%} OF PRICE — no gates, nothing collapsed"]
    body += (["    nothing within reach of price"] if not rows else render_table(rows))
    return "\n".join([head, *body, ""])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A chart-first card: every live level, with the roster and the outside "
                    "data hung off it as data points rather than as gates. Reads only.")
    parser.add_argument("assets", nargs="+", help="canonical or alias tickers, e.g. GOLD SOL")
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="read as at a past date (YYYY-MM-DD), for replay")
    parser.add_argument("--reach", type=float, default=REACH,
                        help=f"how far from price a level still counts, as a fraction "
                             f"(default {REACH}, i.e. {REACH:.0%}). The count beyond the "
                             f"window is always reported whatever this is set to.")
    args = parser.parse_args(argv)
    as_of = args.as_of or datetime.now(UTC).date()

    registry = load_registry(CONFIG_DIR)
    rows = [(r.asset, r.domain) for r in corpus.iter_rows(registry)]
    table = load_routing_table(
        CONFIG_DIR, rows, listings=listings.load_or_fetch(cache.DATA_ROOT / "_listings.json"))

    # Folded once for the run, like `review.cli._fold_by_asset`: folding walks the whole stance
    # store, and asking it per asset would repeat the most expensive read in the probe.
    folded_by_asset: dict[str, list] = {}
    for item in fold_stances(load_all_stances(), registry):
        folded_by_asset.setdefault(item.asset_canonical, []).append(item)

    altsignal_cfg = altsignal_config.load(CONFIG_DIR)
    for asset in args.assets:
        print(card(asset, as_of=as_of, registry=registry, table=table,
                   folded_by_asset=folded_by_asset, altsignal_cfg=altsignal_cfg,
                   reach=args.reach))
    return 0


if __name__ == "__main__":
    sys.exit(main())
