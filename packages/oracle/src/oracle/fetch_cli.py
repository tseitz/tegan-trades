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
from datetime import UTC, datetime
from pathlib import Path

from core.canon import load_registry

from oracle import cache, corpus, listings
from oracle.plan import FetchJob, plan_fetches
from oracle.route import load_routing_table
from oracle.series import PriceSeries
from oracle.sources import coinbase, kraken, yahoo

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = REPO_ROOT / "cfg"

_SOURCES = {"coinbase": coinbase, "kraken": kraken, "yahoo": yahoo}

# Yahoo will resolve a bare ticker to *some* instrument; only these types are plausibly
# the tradeable thing a transcript meant. Indices/futures/FX must be curated explicitly —
# bare `GOLD` resolves to an EQUITY (Gold.com, Inc.), which is why type alone isn't enough.
AUTO_OK_TYPES = {"EQUITY", "ETF"}


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


def _run_job(job: FetchJob, *, root: Path) -> tuple[FetchJob, str, int]:
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
        cache.merge(PriceSeries(symbol=ref.symbol, source=ref.source, bars=tuple(bars)), root=root)
        return job, "ok", len(bars)
    except Exception as exc:  # noqa: BLE001 - report and continue
        return job, f"error:{type(exc).__name__}", 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill the price cache for the corpus.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the routing plan and coverage without fetching")
    parser.add_argument("--refresh-listings", action="store_true",
                        help="refetch which symbols each exchange carries")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--only", help="fetch a single canonical asset (debugging)")
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
    jobs, skipped = plan_fetches(
        rows, table, today=today, cached_spans=_cached_spans(cache.DATA_ROOT)
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

    print(f"\nfetching with concurrency={args.concurrency} ...")
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for job, status, n in pool.map(lambda j: _run_job(j, root=cache.DATA_ROOT), jobs):
            results.append((job, status, n))
            if status != "ok":
                print(f"  {status:<22} {job.ref.asset} ({job.ref.source}:{job.ref.symbol})")

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
