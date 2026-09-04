"""`review` — what to do about the positions you already hold.

Reads down into both halves of the pipeline and writes to neither. The roster's current
stances come from ``brain``; price, routing and weekly structure come from ``oracle``;
``core.review`` pairs them. Nothing here decides anything — it fetches the two readings and
hands them to the grid.

**Nothing in this command spends money and nothing places an order.** By default it reads the
price cache and refuses rather than fetching, so a holding nobody has warmed comes back as a
row saying so. ``--refresh`` is the one exception and it is opt-in for that reason: a review
that went to the network on every run would make "run it again" an unpredictable wait.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

from brain.retrieve import fold_stances
from brain.stance_store import load_all_stances
from core.canon import load_registry, resolve_asset
from core.nearby import levels_near
from core.review import mark_disagrees, review
from core.setups import build_context
from oracle import altsignal_config, cache, corpus, fetch_cli, listings, portfolios
from oracle.assemble import load_daily
from oracle.resample import to_weekly
from oracle.route import Priceable, load_routing_table, route

from review import altsignal
from review.levels import SHOWN, shortlist
from review.render import render, render_altsignal, render_levels

CONFIG_DIR = Path(__file__).resolve().parents[4] / "cfg"


class Read(NamedTuple):
    """One reading per position, plus the structure each was drawn from.

    The contexts ride along because scanning for levels needs them and rebuilding structure is
    the expensive half of the loop. They are **not** folded onto ``Reading``: a verdict is a
    conclusion, and hanging a bar series off it would make the pure grid in ``core.review``
    carry the chart it was derived from.
    """
    readings: list
    contexts: tuple


def canonical_rows(book, registry) -> list[tuple[str, str]]:
    """``(canonical asset, domain)`` per position, for the routing table.

    Canonicalised here and not in ``oracle.portfolios``: the file reader deliberately holds no
    opinion about asset names, so that the registry stays the only place aliases are resolved.
    ``build_readings`` resolves the same way, which is what keeps the domain a holding
    contributes and the asset it later routes as from being two different strings.
    """
    return [(resolve_asset(p.holding.ticker, registry)[0], p.domain) for p in book.positions]


def build_readings(book, *, registry, table, folded_by_asset, as_of: date,
                   series_cache=None):
    """One ``Reading`` per position, **in file order and never fewer**.

    A holding that cannot be routed, or that nothing has fetched yet, still comes back — with
    no price and an ``UNREADABLE`` location. Dropping it would leave a position you own out of
    a review of what you own, which is the one answer this command must never give by accident.

    The ``Holding`` keeps the ticker as you wrote it while routing and the roster lookup both
    use the canonical asset. You need to find your own row; they need the registry's name.

    Ranking is deliberately left to the renderer. Sorting here as well would put two places in
    charge of what you look at first, and they would drift.
    """
    series_cache = {} if series_cache is None else series_cache
    readings = []
    contexts = []
    for position in book.positions:
        holding = position.holding
        asset = resolve_asset(holding.ticker, registry)[0]
        resolved = route(asset, table)
        context = None
        if isinstance(resolved, Priceable):
            daily = load_daily(resolved, table=table, series_cache=series_cache)
            if daily is not None:
                context = build_context(daily.bars, to_weekly(daily).bars, as_of=as_of)
        readings.append(review(
            holding, context,
            folded=folded_by_asset.get(asset, ()), as_of=as_of,
        ))
        contexts.append(context)
    return Read(readings=readings, contexts=tuple(contexts))


def mismatched(book, readings) -> tuple[tuple[str, float, float], ...]:
    """``(ticker, our price, the broker's mark)`` wherever the two disagree.

    Zipped strictly, which is safe because ``build_readings`` promises one reading per position
    in file order and never fewer. A silent misalignment here would pair one holding's price
    with another's mark and invent a mismatch on two correct rows.

    Public because ``digest`` should eventually say this too: a wrong instrument makes every
    verdict about that row wrong, and the nightly is where a person actually looks.
    """
    return tuple(
        (p.holding.ticker, r.price, p.mark)
        for p, r in zip(book.positions, readings, strict=True)
        if mark_disagrees(r.price, p.mark)
    )


def _fold_by_asset(registry) -> dict[str, list]:
    """Every person's current view, grouped by the asset it is about.

    Folded once for the whole run rather than per holding: folding is O(corpus) and a
    portfolio asks the same question of it a dozen times.
    """
    stances = load_all_stances()
    grouped: dict[str, list] = defaultdict(list)
    for item in fold_stances(stances, registry):
        grouped[item.asset_canonical].append(item)
    return grouped


def readings_for(books, *, as_of: date, registry=None):
    """``[(Portfolio, [Reading, ...]), ...]`` for several accounts at once.

    **One routing table and one stance fold for all of them.** Both are O(corpus) — a full walk
    of ``data/theses/`` and a full fold of ``data/stances/`` — and doing either per portfolio
    would multiply the most expensive part of this command by the number of accounts you keep.
    The price cache is shared for the same reason: two accounts holding NVDA read its bars once.

    This is the seam ``digest`` calls. It exists so the path from a portfolio name to a reading
    lives in exactly one place; a second copy in the nightly would be free to drift from what
    ``uv run review`` prints, and the two would quietly stop agreeing.
    """
    books = list(books)
    if not books:
        return []
    registry = load_registry(CONFIG_DIR) if registry is None else registry

    # The corpus supplies domain consensus for everything the roster discusses; the files
    # supply it for everything else. Both, never one — see `Portfolio.domain_rows` for why a
    # file's single row cannot outvote a discussed asset's hundreds.
    rows = [(r.asset, r.domain) for r in corpus.iter_rows(registry)]
    for book in books:
        rows += canonical_rows(book, registry)
    table = load_routing_table(
        CONFIG_DIR, rows,
        listings=listings.load_or_fetch(cache.DATA_ROOT / "_listings.json"),
    )

    folded = _fold_by_asset(registry)
    series_cache: dict = {}
    return [
        (book, *build_readings(book, registry=registry, table=table,
                               folded_by_asset=folded, as_of=as_of,
                               series_cache=series_cache))
        for book in books
    ]


def refresh_argv(portfolio: str) -> list[str]:
    """What ``--refresh`` asks ``fetch-prices`` for.

    A separate function so the two narrowings that make a midday check quick are visible and
    testable, rather than buried in a literal. Both are load-bearing: ``--held-only`` skips a
    ~300-asset corpus pass you do not own, and ``--no-intraday`` skips the hourly warm that
    only ``setups``' entry trigger reads. ``review`` draws on daily and weekly bars alone.
    """
    return ["--portfolio", portfolio, "--held-only", "--no-intraday"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check what you hold against where the roster stands and where the "
                    "weekly sits. Reads only; places nothing.")
    parser.add_argument("portfolio", nargs="?", default="retirement",
                        help="which file under data/portfolios/ to read (default: retirement)")
    parser.add_argument("--list", action="store_true",
                        help="name the portfolios on disk and stop")
    parser.add_argument("--refresh", action="store_true",
                        help="warm this account's prices before reading them, for a check "
                             "during the session. Free, and takes seconds rather than the "
                             "minutes a full `fetch-prices` does. Off by default: every other "
                             "run of this command answers from cache and touches no network.")
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="review as at a past date (YYYY-MM-DD), for replay")
    parser.add_argument("--levels", action="store_true",
                        help="print every level near every holding, not the shortlist. "
                             "The section is capped by default because the raw scan finds "
                             "~230 levels on a 77-position account.")
    args = parser.parse_args(argv)

    if args.list:
        known = portfolios.available()
        print("\n".join(known) if known else
              f"no portfolios yet — write one at {portfolios.DATA_ROOT}/<name>.yaml")
        return 0

    if args.refresh and args.as_of:
        # A replay reads the cache as at a past date. Fetching would write today's bars into
        # it, which is the one thing that makes the replay untrue.
        print("--refresh warms today's bars and cannot serve a replay of a past date",
              file=sys.stderr)
        return 2

    try:
        book = portfolios.load(args.portfolio)
    except portfolios.PortfolioError as exc:
        print(exc, file=sys.stderr)
        return 1

    # After the file loads, so a typo in the name fails in a second rather than after a fetch.
    if args.refresh and fetch_cli.main(refresh_argv(args.portfolio)) != 0:
        # Reported, then carried on with. A failed warm is a reason to distrust how current the
        # rows are, never a reason to refuse to show you what you hold.
        print("  refresh failed — the rows below are the cache as it already stood",
              file=sys.stderr)

    as_of = args.as_of or datetime.now(UTC).date()
    [(book, readings, contexts)] = readings_for([book], as_of=as_of)
    print(render(readings, portfolio=book.name, as_of=as_of,
                 age_days=book.age_days(on=as_of), stale=book.is_stale(on=as_of),
                 cash=book.cash, cash_by=book.cash_by_account,
                 mismatched=mismatched(book, readings)))

    pairs = [
        (reading, levels_near(context, kinds=book.level_kinds) if context is not None else ())
        for reading, context in zip(readings, contexts, strict=True)
    ]
    standing, closing, suppressed = shortlist(pairs, limit=None if args.levels else SHOWN)
    print()
    print(render_levels(standing, closing, suppressed, kinds=book.level_kinds))

    # Recomputed here rather than threaded through `readings_for`'s `Read` — widening that
    # namedtuple broke `digest`'s `for book, readings, contexts in readings_for(...)` unpack,
    # caught in this feature's own plan review. Cheap: a registry load and a dict lookup per
    # position, not the O(corpus) work `readings_for` already paid for once.
    registry = load_registry(CONFIG_DIR)
    assets = [asset for asset, _domain in canonical_rows(book, registry)]
    altsignal_cfg = altsignal_config.load(CONFIG_DIR)
    chains = altsignal.chain_lines(readings, assets, altsignal_cfg=altsignal_cfg)
    macro = altsignal.macro_block(altsignal_cfg=altsignal_cfg)
    print()
    print(render_altsignal(chains, macro))

    missing = [r.holding.ticker for r in readings if r.price is None]
    if missing:
        # Pointed at the exact command that fixes it. A portfolio holds things the roster has
        # never mentioned, so `fetch-prices` on its own will not have warmed them.
        print(f"\n  no cached price for {', '.join(missing)} — "
              f"run `uv run fetch-prices --portfolio {args.portfolio}`", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
