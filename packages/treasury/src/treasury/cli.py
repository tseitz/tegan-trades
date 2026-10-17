"""``treasury`` — what money is parked, and what it earns, plus what idle cash could earn.

Reads only. No fetch, no network, places nothing — same policy as ``review`` and ``compare``
for the same reason: a report that fetches makes "run it again" an unpredictable wait. The
treasury file is hand-kept, and a row that names a Plaid account takes its number from the
snapshot `plaid-sync` last wrote to `data/balances/`; every portfolio file is synced or
hand-kept, and the Safety-gate readings this prints are
whatever `fetch-altsignal` last wrote to `data/altsignal/`, read through `oracle.altsignal_store`
the same way `compare`/`review` already do.

    uv run treasury
"""
from __future__ import annotations

import argparse
import dataclasses
import math
from datetime import UTC, date, datetime
from pathlib import Path

from oracle import altsignal_config, altsignal_store, balance_store, portfolios
from oracle.benchmarks import ANCHOR_ROOT
from oracle.treasury_file import TREASURY_PATH, ParkedRow, TreasuryBook, load

from treasury.book import TreasuryResult, treasury_for
from treasury.render import render

# src/treasury/cli.py -> src/treasury -> src -> treasury -> packages -> <repo root>
CONFIG_DIR = Path(__file__).resolve().parents[4] / "cfg"


def _load_books(*, root: Path, warn) -> tuple:
    """Every other loaded portfolio — the idle-cash source. One unreadable file must not take
    down a card about money parked somewhere else, the same warn-and-continue `review.load_books`
    already gives a malformed portfolio."""
    books = []
    for name in portfolios.available(root=root):
        try:
            books.append(portfolios.load(name, root=root))
        except portfolios.PortfolioError as exc:
            warn(f"portfolio {name!r} was skipped — {exc}")
    return tuple(books)


def _resolve(book: TreasuryBook, *, root: Path, warn) -> TreasuryBook:
    """Price every ``balance:`` row from its snapshot. A miss warns (naming the sync that
    repairs it) and drops the row, so no caller ever sees ``amount=None``. ``updated`` becomes
    the oldest "as of": the file's date for hand rows, the fetch date for synced ones."""
    rows: list[ParkedRow] = []
    dates: list[date] = []
    snapshots: dict[str, dict | None] = {}
    for row in book.rows:
        if row.source is None:
            rows.append(row)
            dates.append(book.updated)
            continue
        portfolio, account = row.source
        fix = f"run `uv run plaid-sync {portfolio}`"
        if portfolio not in snapshots:
            try:
                snapshots[portfolio] = balance_store.read(portfolio, root=root)
            except balance_store.BalanceStoreError as exc:
                warn(f"{row.what} at {row.venue} was left out — {exc}; {fix}")
                snapshots[portfolio] = None
            else:
                if snapshots[portfolio] is None:
                    warn(f"{row.what} at {row.venue} was left out — no balance snapshot "
                         f"for {portfolio!r}; {fix}")
        snapshot = snapshots[portfolio]
        if snapshot is None:
            continue
        entry = snapshot["accounts"].get(account)
        if entry is None:
            warn(f"{row.what} at {row.venue} was left out — {account!r} is not in the "
                 f"{portfolio!r} snapshot; {fix}")
            continue
        value = entry.get("current")
        if value is None:
            value = entry.get("available")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            warn(f"{row.what} at {row.venue} was left out — {account!r} has no balance "
                 f"in the {portfolio!r} snapshot; {fix}")
            continue
        fetched = datetime.fromisoformat(entry["fetched_at"]).date()
        rows.append(dataclasses.replace(row, amount=float(value), as_of=fetched))
        dates.append(fetched)
    return dataclasses.replace(book, rows=tuple(rows), updated=min(dates, default=book.updated))


def load_result(
    *, file: Path = TREASURY_PATH, portfolios_root: Path = portfolios.DATA_ROOT,
    altsignal_root: Path = altsignal_store.DATA_ROOT, cfg_dir: Path = CONFIG_DIR,
    anchor_root: Path = ANCHOR_ROOT, as_of: date | None = None, warn,
    balances_root: Path | None = None, books: tuple | None = None,
) -> TreasuryResult | None:
    """The whole card for one loaded treasury book, or ``None`` when there is nothing parked.

    This is the seam a surface calls instead of reaching into ``oracle.treasury_file`` /
    ``oracle.portfolios`` / ``oracle.altsignal_config`` / ``oracle.altsignal_store`` directly —
    see ADR-0004 — mirroring ``review.cli.load_books``. ``anchor_root`` and ``cfg_dir`` are
    exposed alongside the other roots so a caller (a test, or a fixture-driven digest run) can
    keep `benchmarks.report`'s first-sight write to `data/benchmarks/anchors.json` and the
    `venues:` read off the real filesystem entirely.

    ``books`` lets a caller that has already loaded every portfolio (``digest.cli.build`` has,
    for its own ``_holdings`` pass) pass them straight through rather than have this reload —
    and, with it, warn about — the same files a second time.
    """
    book = load(path=file)
    if book is None:
        return None
    book = _resolve(book, root=balances_root or balance_store.DATA_ROOT, warn=warn)

    resolved_books = _load_books(root=portfolios_root, warn=warn) if books is None else books
    venues = altsignal_config.load(cfg_dir).venues

    def store_read(**kwargs):
        return altsignal_store.read(root=altsignal_root, **kwargs)

    resolved_as_of = as_of or datetime.now(UTC).date()
    return treasury_for(book, as_of=resolved_as_of, books=resolved_books, venues=venues,
                        store_read=store_read, anchor_root=anchor_root)


def no_treasury_note(file: Path = TREASURY_PATH) -> str:
    """The sentence `main()` prints when `load_result` returns `None` — public so a second
    Surface (the dashboard) words a `None` card identically rather than growing a second
    spelling of it."""
    return f"no treasury file at {file} — copy `cfg/treasury.example.yaml` there to get started"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="What money is parked, and what it earns. Reads only; places nothing.")
    parser.add_argument("--file", type=Path, default=TREASURY_PATH,
                        help="path to a treasury file, for a fixture or a replay "
                             "(default: data/treasury.yaml)")
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="report as at a past date (YYYY-MM-DD), for replay")
    parser.add_argument("--portfolios-root", type=Path, default=portfolios.DATA_ROOT,
                        help="portfolio directory, for a fixture (default: data/portfolios)")
    parser.add_argument("--altsignal-root", type=Path, default=altsignal_store.DATA_ROOT,
                        help="alt-signal store directory, for a fixture (default: data/altsignal)")
    args = parser.parse_args(argv)

    def warn(message: str) -> None:
        print(f"  ! {message}")

    result = load_result(file=args.file, portfolios_root=args.portfolios_root,
                         altsignal_root=args.altsignal_root, as_of=args.as_of, warn=warn)

    if result is None:
        # Named rather than silently printing nothing. Nothing parked is a valid state — unlike
        # `compare`'s empty store, this returns 0 rather than 1.
        print(no_treasury_note(args.file))
        return 0

    print(render(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
