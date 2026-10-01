"""``yields`` — for every token you hold, anywhere, what could it be earning (#95).

Reads only. Never fetches — mirrors `compare`/`review`/`treasury`'s policy: the Safety-gate
readings this prints are whatever `fetch-altsignal` last wrote to `data/altsignal/`.

    uv run yields
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from oracle import altsignal_config, altsignal_store, portfolios

from yields.render import render
from yields.scan import yields_for

# src/yields/cli.py -> src/yields -> src -> yields -> packages -> <repo root>
CONFIG_DIR = Path(__file__).resolve().parents[4] / "cfg"


def _load_books(*, root: Path, warn) -> tuple:
    """Every portfolio on disk — mirrors `treasury.cli._load_books`: one unreadable file must
    not take down a ranking about every other book's holdings."""
    books = []
    for name in portfolios.available(root=root):
        try:
            books.append(portfolios.load(name, root=root))
        except portfolios.PortfolioError as exc:
            warn(f"portfolio {name!r} was skipped — {exc}")
    return tuple(books)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="For every token you hold, anywhere, what could it be earning. "
                    "Reads only; fetches nothing.")
    parser.add_argument("--portfolio",
                        help="limit to one portfolio name under data/portfolios/ "
                             "(default: every portfolio on disk)")
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="report as at a past date (YYYY-MM-DD), for replay")
    args = parser.parse_args(argv)

    def warn(message: str) -> None:
        print(f"  ! {message}")

    if args.portfolio:
        try:
            books = (portfolios.load(args.portfolio),)
        except portfolios.PortfolioError as exc:
            print(exc)
            return 1
    else:
        books = _load_books(root=portfolios.DATA_ROOT, warn=warn)

    if not books:
        print(f"no portfolios yet — write one at {portfolios.DATA_ROOT}/<name>.yaml")
        return 0

    cfg = altsignal_config.load(CONFIG_DIR)
    as_of = args.as_of or datetime.now(UTC).date()
    result = yields_for(books, altsignal_cfg=cfg, as_of=as_of, store_read=altsignal_store.read)

    if result.matched and result.readings_as_of.freshest is None:
        # Named rather than silently printing an all-"not fetched" card. A fresh checkout has
        # never run `fetch-altsignal`, and the command that fills the store is the answer.
        print("no readings stored for any configured wrapper yet — "
              "run `uv run fetch-altsignal` first")
        return 1

    print(render(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
