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
from core.review import LEVELS_LED, mark_disagrees, review
from core.setups import build_context
from core.transactions import TransactionSpan
from oracle import (
    altsignal_config,
    cache,
    corpus,
    fetch_cli,
    listings,
    portfolios,
    transaction_store,
)
from oracle.assemble import load_daily
from oracle.resample import to_weekly
from oracle.route import Priceable, load_routing_table, route

from review import altsignal, yield_note
from review.levels import SHOWN, cap, shortlist
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


class ReviewResult(NamedTuple):
    """Everything the review view can say about one book, from a single ``review_for`` call.

    Bundled rather than left as separate return values so a renderer never has to reassemble
    the same arguments a second time — the failure mode this replaces: widening the old
    ``(book, readings, contexts)`` tuple once already broke a positional unpack in ``digest``,
    caught only by a test built to guard that one seam. A named field can be added here without
    breaking any caller that does not read it.

    ``levels`` is the **uncapped** ``(standing, closing, suppressed=0)`` triple from
    ``review.levels.shortlist`` — capping for a screen is a display decision, not something the
    view should decide on a caller's behalf. See ``review.levels.cap``.

    ``chains``/``macro``/``yield_notes`` come back empty when ``review_for`` was called with no
    ``altsignal_cfg`` — see its docstring for why that is opt-in rather than always assembled.

    ``history`` is the account's cached transaction span, or ``None`` when no
    ``data/transactions/<name>.json`` exists yet — distinct from a cache file with zero rows,
    which is a ``TransactionSpan`` whose ``oldest`` is ``None``. Both existing test-cli.py's
    and digest's ``test_holdings.py``'s constructions of this tuple read every field by
    keyword, so a trailing field with a default is safe to add here.
    """
    book: object               # oracle.portfolios.Portfolio
    readings: list
    contexts: tuple
    mismatched: tuple
    levels: tuple
    chains: tuple
    macro: tuple
    history: TransactionSpan | None = None
    yield_notes: tuple = ()


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
    use the canonical asset. You need to find your own row; they need the registry's name. A
    third name enters only for a wrapper fund in ``table.wraps`` (``HODL`` -> ``BTC``): routing
    and pricing stay on the canonical asset as always, but the roster lookup borrows the wrapped
    asset's fold instead, because the fund's own name has nothing behind it in the corpus.

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
        wrapped = table.wraps.get(asset)
        readings.append(review(
            holding, context,
            folded=folded_by_asset.get(wrapped or asset, ()), as_of=as_of,
            leads_with=book.mandate.leads_with,
            lean_from=wrapped,
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


def review_for(books, *, as_of: date, registry=None, altsignal_cfg=None) -> list[ReviewResult]:
    """One ``ReviewResult`` per account, for several accounts at once.

    **One routing table and one stance fold for all of them.** Both are O(corpus) — a full walk
    of ``data/theses/`` and a full fold of ``data/stances/`` — and doing either per portfolio
    would multiply the most expensive part of this command by the number of accounts you keep.
    The price cache is shared for the same reason: two accounts holding NVDA read its bars once.

    This is the seam ``digest`` calls. It exists so the path from a portfolio name to a full
    review answer lives in exactly one place; a second assembly in the nightly would be free to
    drift from what ``uv run review`` prints, and the two would quietly stop agreeing.

    ``altsignal_cfg`` is ``None`` by default, which skips the DefiLlama/Kalshi/Polymarket read
    entirely (``chains=()``, ``macro=()`` on every result). ``digest`` never renders that
    section today, so asking every caller to pay for it — or to risk a broken
    ``cfg/altsignal.yaml`` costing a section that was never going to be shown — would be a cost
    with no matching benefit. The terminal passes its own loaded config.
    """
    books = list(books)
    if not books:
        return []
    registry = load_registry(CONFIG_DIR) if registry is None else registry

    # Computed once per book and reused for both the routing table below and the alt-signal
    # lookup further down — recomputing it a second time for alt-signal is what `main()` used
    # to do, and it duplicates a registry lookup and a routing resolution per position for no
    # reason beyond the two steps being written apart.
    per_book_assets = [canonical_rows(book, registry) for book in books]

    # The corpus supplies domain consensus for everything the roster discusses; the files
    # supply it for everything else. Both, never one — see `Portfolio.domain_rows` for why a
    # file's single row cannot outvote a discussed asset's hundreds.
    rows = [(r.asset, r.domain) for r in corpus.iter_rows(registry)]
    for pairs in per_book_assets:
        rows += pairs
    table = load_routing_table(
        CONFIG_DIR, rows,
        listings=listings.load_or_fetch(cache.DATA_ROOT / "_listings.json"),
    )

    folded = _fold_by_asset(registry)
    series_cache: dict = {}
    results = []
    for book, pairs in zip(books, per_book_assets, strict=True):
        readings, contexts = build_readings(
            book, registry=registry, table=table, folded_by_asset=folded, as_of=as_of,
            series_cache=series_cache)
        level_pairs = [
            (reading, levels_near(context, kinds=book.level_kinds) if context is not None else ())
            for reading, context in zip(readings, contexts, strict=True)
        ]
        # Uncapped — see `ReviewResult.levels`. A caller printing to a screen caps at render
        # time with `review.levels.cap`.
        levels = shortlist(level_pairs, limit=None)

        chains, macro, notes = (), (), ()
        if altsignal_cfg is not None:
            assets = [asset for asset, _domain in pairs]
            chains = altsignal.chain_lines(readings, assets, altsignal_cfg=altsignal_cfg)
            macro = altsignal.macro_block(altsignal_cfg=altsignal_cfg)
            notes = yield_note.yield_notes(
                readings, assets, book.positions, altsignal_cfg=altsignal_cfg, as_of=as_of
            )

        cached = transaction_store.load(book.name)
        history = TransactionSpan.of(cached[0]) if cached is not None else None

        results.append(ReviewResult(
            book=book, readings=readings, contexts=contexts,
            mismatched=mismatched(book, readings),
            levels=levels, chains=chains, macro=macro, history=history,
            yield_notes=notes,
        ))
    return results


def load_books(names=None, *, warn=None) -> list:
    """Every named portfolio, or every one on disk when ``names`` is omitted. Skips a bad file
    and reports it through ``warn`` rather than failing the whole batch.

    This is the seam a surface calls instead of reaching into ``oracle.portfolios`` directly —
    see ADR-0004. A hand-kept file with a typo in it is exactly the kind of thing one account
    should not be able to take down every other account's review.
    """
    names = portfolios.available() if names is None else names
    books = []
    for name in names:
        try:
            books.append(portfolios.load(name))
        except portfolios.PortfolioError as exc:
            if warn is not None:
                warn(f"portfolio {name!r} was skipped — {exc}")
    return books


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
    parser.add_argument("--by-size", action="store_true",
                        help="sort strictly by weight, largest first, for surveying "
                             "allocation shape rather than triaging action. Default "
                             "ordering (urgency, then size) is unchanged without it.")
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
    [result] = review_for([book], as_of=as_of, altsignal_cfg=altsignal_config.load(CONFIG_DIR))
    readings = result.readings
    print(render(readings, portfolio=book.name, as_of=as_of,
                 age_days=book.age_days(on=as_of), stale=book.is_stale(on=as_of),
                 cash=book.cash, cash_by=book.cash_by_account,
                 mismatched=result.mismatched, mandate=book.mandate, history=result.history,
                 by_size=args.by_size, yield_notes=result.yield_notes))

    # The view hands back every level, uncapped — this is the one place that decides how much
    # fits on a screen. See `ReviewResult.levels` and `review.levels.cap`.
    raw_standing, raw_closing, _ = result.levels
    # On a levels-led mandate the verdict already carries the location, so the standing group
    # is folded into it — ADR-0002. `--levels` means "show me everything" and keeps meaning
    # that, so it still prints the group; dropping it before `cap()` keeps `suppressed` honest
    # about what this run is actually withholding.
    if book.mandate.leads_with == LEVELS_LED and not args.levels:
        raw_standing = ()
    standing, closing, suppressed = cap(raw_standing, raw_closing,
                                        limit=None if args.levels else SHOWN)
    print()
    print(render_levels(standing, closing, suppressed, kinds=book.level_kinds))

    print()
    print(render_altsignal(result.chains, result.macro))

    missing = [r.holding.ticker for r in readings if r.price is None]
    if missing:
        # Pointed at the exact command that fixes it. A portfolio holds things the roster has
        # never mentioned, so `fetch-prices` on its own will not have warmed them.
        print(f"\n  no cached price for {', '.join(missing)} — "
              f"run `uv run fetch-prices --portfolio {args.portfolio}`", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
