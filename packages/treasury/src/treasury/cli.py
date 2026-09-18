"""``treasury`` — what money is parked, and what it earns, plus what idle cash could earn.

Reads only. No fetch, no network, places nothing — same policy as ``review`` and ``compare``
for the same reason: a report that fetches makes "run it again" an unpredictable wait. The
treasury file and every portfolio file are hand-kept; the Safety-gate readings this prints are
whatever `fetch-altsignal` last wrote to `data/altsignal/`, read through `oracle.altsignal_store`
the same way `compare`/`review` already do.

    uv run treasury
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from oracle import altsignal_config, altsignal_store, portfolios
from oracle.treasury_file import TREASURY_PATH, load

from treasury.book import treasury_for
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

    book = load(path=args.file)

    if book is None:
        # Named rather than silently printing nothing. Nothing parked is a valid state — unlike
        # `compare`'s empty store, this returns 0 rather than 1.
        print(f"no treasury file at {args.file} — "
              f"copy `cfg/treasury.example.yaml` there to get started")
        return 0

    def warn(message: str) -> None:
        print(f"  ! {message}")

    books = _load_books(root=args.portfolios_root, warn=warn)
    venues = altsignal_config.load(CONFIG_DIR).venues

    def store_read(**kwargs):
        return altsignal_store.read(root=args.altsignal_root, **kwargs)

    as_of = args.as_of or datetime.now(UTC).date()
    result = treasury_for(book, as_of=as_of, books=books, venues=venues, store_read=store_read)
    print(render(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
