"""``fetch-funding`` — log what it costs to hold a position, across venues.

Free on every venue: three public read-only endpoints, no keys, no money. Safe to run from
the nightly job.

Two modes, because they answer different questions:

    fetch-funding                 snapshot every venue's current rate (nightly)
    fetch-funding --backfill 30   pull 30 days of REALISED settlements for approved assets

Backfill is what makes the log useful on day one rather than in three weeks. It covers
Hyperliquid and Aster only — Lighter serves a history feed whose unit does not reconcile
with its snapshot feed, and shipping an unverified 8x conversion into a carry model is the
exact failure this whole subsystem is built to avoid.

    fetch-funding --report        summarise what has been logged, per asset per venue
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core.funding import FundingRate, summarize
from oracle import funding_store, http, venue_map
from oracle.http import FetchError
from oracle.sources import aster, hyperliquid, lighter

# Assets to backfill. The venue map is the source of truth for what is tradeable, so the
# backfill set is derived from it rather than restated -- one place to add an asset.
BACKFILL_VENUES = ("hyperliquid", "aster")


def _snapshot(verbose: bool = True) -> list[FundingRate]:
    at = datetime.now(UTC)
    rates: list[FundingRate] = []

    for name, thunk in (
        ("hyperliquid", lambda: hyperliquid.fetch(observed_at=at)),
        ("lighter", lambda: lighter.fetch(observed_at=at)),
    ):
        try:
            got = thunk()
        except FetchError as exc:
            # One venue being unreachable must not cost the other two their snapshot --
            # a gap in the log is unrecoverable, so partial beats nothing.
            print(f"  ! {name}: {exc}")
            continue
        rates.extend(got)
        if verbose:
            print(f"  {name}: {len(got)} markets")

    try:
        got, defaulted = aster.fetch(observed_at=at)
        rates.extend(got)
        if verbose:
            note = f" ({defaulted} used the {aster.DEFAULT_INTERVAL_HOURS}h default)" if defaulted else ""
            print(f"  aster: {len(got)} markets{note}")
    except FetchError as exc:
        print(f"  ! aster: {exc}")

    return rates


def _backfill(days: int) -> list[FundingRate]:
    start_ms = int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)
    rates: list[FundingRate] = []

    aster_intervals: dict[str, float] = {}
    try:
        aster_intervals = aster.parse_funding_info(http.get_json(f"{aster.BASE}/fundingInfo"))
    except FetchError as exc:
        print(f"  ! aster fundingInfo: {exc} — falling back to per-symbol lookup")

    for canonical in sorted(venue_map.load()):
        for venue in BACKFILL_VENUES:
            got = venue_map.listing(canonical, venue)
            if got is None:
                continue
            try:
                if venue == "hyperliquid":
                    # The HIP-3 namespace is part of the coin id and the recorded venue.
                    dex = got.symbol.split(":", 1)[0] if ":" in got.symbol else ""
                    pulled = hyperliquid.fetch_history(
                        got.symbol,
                        start_ms,
                        venue=f"hyperliquid:{dex}" if dex else "hyperliquid",
                    )
                else:
                    pulled = aster.fetch_history(got.symbol, intervals=aster_intervals)
            except FetchError as exc:
                print(f"  ! {canonical}/{venue}: {exc}")
                continue
            rates.extend(pulled)
            print(f"  {canonical:8s} {venue:12s} {got.symbol:16s} {len(pulled):>5} settlements")
    return rates


def _report(days: int) -> None:
    since = datetime.now(UTC) - timedelta(days=days)
    stored = funding_store.read(since=since)
    if not stored:
        print("Nothing logged yet. Run `fetch-funding --backfill 30` first.")
        return

    print(f"\n{len(stored)} observations since {since:%Y-%m-%d}\n")
    header = f"{'asset':9s} {'venue':16s} {'n':>5s} {'median':>9s} {'p10':>9s} {'p90':>9s} {'21d carry':>10s}"
    print(header)
    print("-" * len(header))

    for canonical in sorted(venue_map.load()):
        for venue in venue_map.venues_for(canonical):
            got = venue_map.listing(canonical, venue)
            if got is None:
                continue
            # Stored venue strings carry the HIP-3 dex; match on the prefix.
            rows = [
                r
                for r in stored
                if r.symbol == got.symbol.split(":", 1)[-1]
                and (r.venue == venue or r.venue.startswith(f"{venue}:"))
            ]
            stats = summarize(rows)
            if not stats.n or stats.median is None:
                continue
            carry_21d = stats.median * (21 / 365.0)
            print(
                f"{canonical:9s} {venue:16s} {stats.n:>5} "
                f"{stats.median:>8.2%} {stats.p10:>8.2%} {stats.p90:>8.2%} {carry_21d:>9.2%}"
            )

    missing = venue_map.unlisted()
    if missing:
        print(f"\nApproved but listed by no venue: {', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--backfill",
        type=int,
        metavar="DAYS",
        help="pull realised settlements for approved assets (Hyperliquid + Aster only)",
    )
    parser.add_argument("--report", action="store_true", help="summarise the log and exit")
    parser.add_argument(
        "--window", type=int, default=30, help="days of history to report over (default 30)"
    )
    parser.add_argument("--dry-run", action="store_true", help="fetch but do not write")
    args = parser.parse_args()

    if args.report:
        _report(args.window)
        return 0

    if args.backfill:
        print(f"Backfilling {args.backfill}d of realised funding...")
        rates = _backfill(args.backfill)
    else:
        print("Snapshotting funding...")
        rates = _snapshot()

    if not rates:
        print("No rates fetched — nothing written.")
        return 1

    if args.dry_run:
        print(f"\n[dry-run] {len(rates)} observations, not written.")
        return 0

    written = funding_store.append(rates)
    total = sum(written.values())
    for path, n in sorted(written.items()):
        print(f"  wrote {n:>6} -> {path.name}")
    print(f"\n{total} observations logged.")
    return 0
