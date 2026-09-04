"""``fetch-altsignal`` — pull the Phase 5 alt-signal sources.

Free on every source: no money, safe to run from the nightly job — same tier as
``fetch-funding``. DefiLlama, Kalshi and Polymarket fetch whatever ``cfg/altsignal.yaml``
names. pump.fun is different — it's a global feed (recently graduated tokens), not a curated
list, so it always runs; it needs a free Solana Tracker key (``SOLANATRACKER_API_KEY`` in
``.env``, see ``oracle.altsignal.pumpfun``) and is skipped with a clear message if that's unset,
same as any other unreachable source.

    fetch-altsignal            snapshot every configured source, plus pump.fun
    fetch-altsignal --report   summarise what has been logged, per source per key
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.altsignal import AltSignalReading

from oracle import altsignal_config, altsignal_store
from oracle.altsignal import defillama, kalshi, polymarket, pumpfun
from oracle.http import FetchError

CONFIG_DIR = Path(__file__).resolve().parents[4] / "cfg"


def _snapshot(cfg: altsignal_config.AltSignalConfig, verbose: bool = True) -> list[AltSignalReading]:
    at = datetime.now(UTC)
    readings: list[AltSignalReading] = []

    chains = [c.chain for c in cfg.chains]
    if chains:
        try:
            got = defillama.fetch(chains, observed_at=at)
            readings.extend(got)
            if verbose:
                print(f"  defillama: {len(got)} readings")
        except FetchError as exc:
            # One source being unreachable must not cost the others — a gap in the log is
            # unrecoverable, so partial beats nothing. Same reasoning as fetch-funding.
            print(f"  ! defillama: {exc}")

    kalshi_tickers = [m.key for m in cfg.markets if m.platform == "kalshi"]
    if kalshi_tickers:
        try:
            got = kalshi.fetch(kalshi_tickers, observed_at=at)
            readings.extend(got)
            if verbose:
                print(f"  kalshi: {len(got)} readings")
        except FetchError as exc:
            print(f"  ! kalshi: {exc}")

    polymarket_slugs = [m.key for m in cfg.markets if m.platform == "polymarket"]
    if polymarket_slugs:
        try:
            got = polymarket.fetch(polymarket_slugs, observed_at=at)
            readings.extend(got)
            if verbose:
                print(f"  polymarket: {len(got)} readings")
        except FetchError as exc:
            print(f"  ! polymarket: {exc}")

    try:
        got = pumpfun.fetch()
        readings.extend(got)
        if verbose:
            print(f"  pumpfun: {len(got)} readings")
    except FetchError as exc:
        print(f"  ! pumpfun: {exc}")

    return readings


def _report(days: int) -> None:
    since = datetime.now(UTC) - timedelta(days=days)
    stored = altsignal_store.read(since=since)
    if not stored:
        print("Nothing logged yet. Run `fetch-altsignal` first.")
        return

    print(f"\n{len(stored)} observations since {since:%Y-%m-%d}\n")
    header = f"{'source':11s} {'kind':18s} {'key':40s} {'value':>12s}  observed_at"
    print(header)
    print("-" * len(header))
    for r in stored:
        value = f"{r.value:.4g}" if isinstance(r.value, (int, float)) else str(r.value)
        print(f"{r.source:11s} {r.kind:18s} {r.key:40s} {value:>12s}  {r.observed_at:%Y-%m-%d %H:%M}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--report", action="store_true", help="summarise the log and exit")
    parser.add_argument(
        "--window", type=int, default=30, help="days of history to report over (default 30)"
    )
    parser.add_argument("--dry-run", action="store_true", help="fetch but do not write")
    args = parser.parse_args()

    cfg = altsignal_config.load(CONFIG_DIR)

    if args.report:
        _report(args.window)
        return 0

    # No early-return on an empty cfg/altsignal.yaml — pump.fun is a global feed, not a
    # curated list, so it still has something to fetch even on a fresh checkout.
    print("Snapshotting alt-signal sources...")
    readings = _snapshot(cfg)

    if not readings:
        print("No readings fetched — nothing written.")
        return 1

    if args.dry_run:
        print(f"\n[dry-run] {len(readings)} readings, not written.")
        return 0

    written = altsignal_store.append(readings)
    total = sum(written.values())
    for path, n in sorted(written.items()):
        print(f"  wrote {n:>6} -> {path.name}")
    print(f"\n{total} readings logged.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
