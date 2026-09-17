"""``treasury`` — what money is parked, and what it earns.

Reads only. No fetch, no network, places nothing — same policy as ``review`` and ``compare``
for the same reason: a report that fetches makes "run it again" an unpredictable wait, and
this file is hand-kept in any case, so there is nothing to fetch.

    uv run treasury
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from oracle.treasury_file import TREASURY_PATH, load

from treasury.book import treasury_for
from treasury.render import render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="What money is parked, and what it earns. Reads only; places nothing.")
    parser.add_argument("--file", type=Path, default=TREASURY_PATH,
                        help="path to a treasury file, for a fixture or a replay "
                             "(default: data/treasury.yaml)")
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="report as at a past date (YYYY-MM-DD), for replay")
    args = parser.parse_args(argv)

    book = load(path=args.file)

    if book is None:
        # Named rather than silently printing nothing. Nothing parked is a valid state — unlike
        # `compare`'s empty store, this returns 0 rather than 1.
        print(f"no treasury file at {args.file} — "
              f"copy `cfg/treasury.example.yaml` there to get started")
        return 0

    as_of = args.as_of or datetime.now(UTC).date()
    print(render(treasury_for(book, as_of=as_of)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
