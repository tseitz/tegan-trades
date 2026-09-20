"""Parse every mandate-carrying file before the nightly spends anything on a run it cannot finish.

`#67` made a malformed mandate block raise, naming the file, and that worked. The gap was *when*:
`data/` is gitignored, so `#72`'s new required `mandate:` key migrated nowhere, and on 2026-09-18
the droplet discovered it at step 10 of 18 — after `ingest-roster` had spent 732s and
`distill-roster` 279s pulling a corpus the run could no longer price. Nine steps of work, then a
2-second death, and a summary that read as mostly fine.

So this runs second, after `code-update` and before anything costs time or money, and the nightly
aborts on it rather than recording a failure and carrying on. It is the one step that may do that
— see the invariant at the top of `scripts/nightly.sh`.

Absence is not failure. No `data/portfolios/` and no `data/treasury.yaml` is a fresh clone, which
must pass; the loaders already draw that line (`portfolios.available()` yields nothing for a
missing directory, `treasury_file.load()` returns `None` for a missing file) and this inherits it
rather than restating it.

    uv run python scripts/preflight.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

from oracle import portfolios, treasury_file


def check(
    *,
    root: Path = portfolios.DATA_ROOT,
    treasury_path: Path = treasury_file.TREASURY_PATH,
) -> list[str]:
    """Every mandate-carrying file that will not load, one line each. Empty means go."""
    problems: list[str] = []

    for name in portfolios.available(root=root):
        try:
            portfolios.load(name, root=root)
        except portfolios.PortfolioError as exc:
            problems.append(str(exc))

    try:
        treasury_file.load(path=treasury_path)
    except treasury_file.TreasuryError as exc:
        problems.append(str(exc))

    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Parse every mandate-carrying file. Non-zero if any will not load."
    )
    ap.add_argument("--root", type=Path, default=portfolios.DATA_ROOT,
                    help="portfolio directory (default: data/portfolios)")
    ap.add_argument("--treasury", type=Path, default=treasury_file.TREASURY_PATH,
                    help="treasury file (default: data/treasury.yaml)")
    args = ap.parse_args(argv)

    problems = check(root=args.root, treasury_path=args.treasury)
    if problems:
        print(f"{len(problems)} mandate-carrying file(s) will not load:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    checked = len(portfolios.available(root=args.root)) + args.treasury.is_file()
    print(f"{checked} mandate-carrying file(s) load.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
