"""``fetch-prices`` — backfill the price cache for every asset the corpus mentions.

Resumable and idempotent: bars merge into whatever is cached, so an interrupted run picks
up where it stopped. ``--dry-run`` prints the full routing plan and coverage before
spending a request.
"""
from __future__ import annotations

import argparse
import collections
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.canon import load_registry
from core.identity import DIFFERS

from oracle import cache, confirm, corpus, listings, trigger_feed
from oracle import marks as marks_mod
from oracle.plan import FetchJob, plan_fetches
from oracle.route import load_routing_table
from oracle.series import PriceSeries
from oracle.sources import coinbase, kraken, yahoo

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = REPO_ROOT / "cfg"

_SOURCES = {"coinbase": coinbase, "kraken": kraken, "yahoo": yahoo}

# Minimum daily history per series, back from today. Two years, chosen against the source
# ceilings rather than against a structure requirement — Kraken hard-caps at 720 candles, so
# past this the deepest source simply stops answering, and the corpus itself only starts
# 2024-07. Yahoo serves multi-year spans in one request and Coinbase tiles 300-candle pages,
# so asking for the maximum costs the same as asking for less.
#
# It is deliberately NOT derived from a warmup constant. How much lookback structure actually
# needs is a measurement nobody can take until the deep series exist — see
# ``scripts/probe_price_cache.py``, which sweeps it. Fetch the most that is available, then
# measure what of it was needed.
DEFAULT_HISTORY_DAYS = 730

# Yahoo will resolve a bare ticker to *some* instrument; only these types are plausibly
# the tradeable thing a transcript meant. Indices/futures/FX must be curated explicitly —
# bare `GOLD` resolves to an EQUITY (Gold.com, Inc.), which is why type alone isn't enough.
AUTO_OK_TYPES = {"EQUITY", "ETF"}

# Sessions of the freshly-fetched band a venue's mark may sit inside without reading as a
# fault. Matches ``setups_cli._CONFIRM_SESSIONS`` — a venue oracle can run a session behind
# ours, and on a volatile name that reads as a collision.
_CONFIRM_SESSIONS = 5


def _cached_spans(root: Path) -> dict[tuple[str, str], tuple]:
    spans = {}
    for source_dir in Path(root).glob("*"):
        if not source_dir.is_dir():
            continue
        for path in source_dir.glob("*.json"):
            series = cache.load(source_dir.name, _decode(path.stem), root=root)
            if series and series.span:
                spans[(series.source, series.symbol)] = series.span
    return spans


def _decode(stem: str) -> str:
    from urllib.parse import unquote

    return unquote(stem)


def _run_job(job: FetchJob, *, root: Path, marks=None) -> tuple[FetchJob, str, int]:
    """-> (job, status, bar_count). Never raises: one dead symbol must not abort a
    several-hundred-symbol backfill."""
    ref = job.ref
    try:
        if ref.needs_validation:
            kind = yahoo.probe(ref.symbol)
            if kind not in AUTO_OK_TYPES:
                return job, f"rejected:{kind or 'unknown'}", 0
        bars = _SOURCES[ref.source].fetch_daily(ref.symbol, job.start, job.end)
        if not bars:
            return job, "empty", 0
        # The type probe above clears `WTI` because W&T Offshore genuinely is an EQUITY — it
        # asks what kind of thing the symbol is, never whether it is the asset the corpus
        # meant. Comparing the bars we just pulled against an independent venue's mark is the
        # question it does not ask, and it runs *before* the merge so a contradicted series
        # never reaches disk to be graded or drawn on. Nothing to compare against passes.
        recent = bars[-_CONFIRM_SESSIONS:]
        verdict = confirm.check(
            ref, close=bars[-1].close, marks=marks or {},
            low=min(b.low for b in recent), high=max(b.high for b in recent),
        )
        if verdict is not None and verdict.verdict == DIFFERS:
            return job, f"rejected:wrong_instrument({verdict.ratio:.4g}x)", 0
        cache.merge(PriceSeries(symbol=ref.symbol, source=ref.source, bars=tuple(bars)), root=root)
        return job, "ok", len(bars)
    except Exception as exc:  # noqa: BLE001 - report and continue
        return job, f"error:{type(exc).__name__}", 0


def intraday_targets(jobs) -> list:
    """The refs whose hourly bars are worth holding, one per instrument an order can reach.

    Deduped on ``(source, trade_symbol)`` because a proxied asset plans two daily jobs — the
    priced leg (``^DJI``) and the traded leg (``DIA``) — and both resolve to the same
    ``trade_symbol``. The zone, the stop and the trigger are all quoted on that one, and
    ``score-roster`` (the only consumer of the priced leg) grades on daily closes and has no
    use for an hourly bar. So a proxied asset warms one hourly series, not two.
    """
    seen = {}
    for job in jobs:
        ref = job.ref
        symbol = getattr(ref, "trade_symbol", None)
        source = getattr(ref, "source", None)
        if symbol is None or source is None:
            continue
        seen.setdefault((source, symbol), ref)
    return list(seen.values())


def _warm_intraday(refs, *, root: Path, concurrency: int) -> tuple[int, int]:
    """-> (symbols cached, bars written). Never raises, exactly like ``_run_job``.

    ``setups`` reads this cache rather than filling it: choosing the setup rung for ~300 assets
    at queue time would be ~300 round trips before the first candidate printed. An asset with
    nothing here takes the daily rung, which is correct but loses H12 on anything that trades
    around the clock — so the nightly warming this is what makes the rung right.
    """
    def one(ref):
        try:
            series = trigger_feed.load_or_fetch(ref, root=root)
        except Exception:  # noqa: BLE001 - one dead symbol must not abort the pass
            return 0
        return 0 if series is None else len(series.bars)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        counts = list(pool.map(one, refs))
    return sum(1 for n in counts if n), sum(counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill the price cache for the corpus.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the routing plan and coverage without fetching")
    parser.add_argument("--refresh-listings", action="store_true",
                        help="refetch which symbols each exchange carries")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--only", help="fetch a single canonical asset (debugging)")
    parser.add_argument("--no-intraday", action="store_true",
                        help="skip the hourly pass; `setups` then falls back to daily zones "
                             "for everything and shows no entry trigger")
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS,
                        help="minimum daily history per series, in days back from today "
                             f"(default {DEFAULT_HISTORY_DAYS}). Structure needs lookback that "
                             "predates an asset's first mention; 0 restores the old "
                             "mention-windowed behaviour")
    args = parser.parse_args(argv)

    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    if not rows:
        print("no theses found — run distillation first", file=sys.stderr)
        return 1

    listings_path = cache.DATA_ROOT / "_listings.json"
    if args.refresh_listings and listings_path.exists():
        listings_path.unlink()
    known = listings.load_or_fetch(listings_path)

    table = load_routing_table(CONFIG_DIR, [(r.asset, r.domain) for r in rows], listings=known)
    today = datetime.now(UTC).date()
    floor_start = (today - timedelta(days=args.history_days)
                   if args.history_days > 0 else None)
    jobs, skipped = plan_fetches(
        rows, table, today=today, cached_spans=_cached_spans(cache.DATA_ROOT),
        floor_start=floor_start,
    )
    if args.only:
        jobs = [j for j in jobs if j.ref.asset == args.only]

    counts = collections.Counter(r.asset for r in rows)
    priced = sum(counts[j.ref.asset] for j in jobs)
    unpriceable = sum(counts[s.asset] for s in skipped)

    print(f"{len(rows)} theses · {len(counts)} distinct assets")
    print(f"  {len(jobs)} fetch jobs ({priced} theses)")
    print(f"  {len(skipped)} unpriceable assets ({unpriceable} theses, "
          f"{unpriceable / len(rows):.1%})")
    by_reason = collections.Counter(s.reason for s in skipped)
    for reason, n in by_reason.most_common():
        theses = sum(counts[s.asset] for s in skipped if s.reason == reason)
        print(f"      {reason:<18} {n:>3} assets / {theses:>4} theses")

    if args.dry_run:
        print("\nplanned fetches:")
        for job in jobs:
            flag = " (needs validation)" if job.ref.needs_validation else ""
            print(f"  {job.ref.asset:<14} -> {job.ref.source}:{job.ref.symbol:<12} "
                  f"{job.start} .. {job.end}{flag}")
        return 0

    # Read once for the whole run rather than per job: it is three requests against endpoints
    # that answer for every market at once, and a per-symbol fetch would turn an identity check
    # into a rate-limit problem.
    mark_index = marks_mod.index_marks(marks_mod.fetch_all())
    print(f"\nfetching with concurrency={args.concurrency} "
          f"({sum(len(v) for v in mark_index.values())} venue marks for cross-checking) ...")
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for job, status, n in pool.map(
            lambda j: _run_job(j, root=cache.DATA_ROOT, marks=mark_index), jobs
        ):
            results.append((job, status, n))
            if status != "ok":
                print(f"  {status:<22} {job.ref.asset} ({job.ref.source}:{job.ref.symbol})")

    if not args.no_intraday:
        targets = intraday_targets(jobs)
        print(f"\nwarming {len(targets)} hourly series for the entry trigger ...")
        symbols, bars = _warm_intraday(
            targets, root=cache.DATA_ROOT, concurrency=args.concurrency)
        print(f"  {symbols}/{len(targets)} symbols, {bars} hourly bars")

    ok = [r for r in results if r[1] == "ok"]
    print(f"\ncached {len(ok)}/{len(jobs)} symbols, {sum(n for _, _, n in ok)} bars")
    failed = [(j, s) for j, s, _ in results if s != "ok"]
    if failed:
        lost = sum(counts[j.ref.asset] for j, _ in failed)
        print(f"unfetched: {len(failed)} symbols covering {lost} theses "
              f"({lost / len(rows):.1%}) — these grade as unpriceable, not as zero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
