"""``compare`` — put one protocol beside another.

Reads only. Never fetches — mirrors `review`'s policy for the same reason
(`review/cli.py`'s docstring): a report that fetches makes "run it again" an unpredictable
wait, and DefiLlama's fee series is already ~27h behind live regardless of when you ask. Run
`uv run fetch-altsignal` first to populate the store; the nightly already does this daily.

    uv run compare HYPE LIT
"""
from __future__ import annotations

import argparse
from pathlib import Path

from oracle import altsignal_config

from compare.card import UnknownAssetError, compare_for
from compare.render import render

CONFIG_DIR = Path(__file__).resolve().parents[4] / "cfg"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Put one protocol beside another. Reads only; fetches nothing.")
    parser.add_argument("left", help="ticker as written in cfg/altsignal.yaml's protocols: block")
    parser.add_argument("right", help="same, the protocol to compare it against")
    args = parser.parse_args(argv)

    cfg = altsignal_config.load(CONFIG_DIR)
    try:
        result = compare_for(args.left, args.right, altsignal_cfg=cfg)
    except UnknownAssetError as exc:
        print(exc)
        return 1

    if result.freshest == (None, None):
        # Named rather than silently printing an all-"not fetched" card. A fresh checkout has
        # never run `fetch-altsignal`, and the command that fills the store is the answer.
        print(f"no readings stored for {args.left} or {args.right} yet — "
              f"run `uv run fetch-altsignal` first")
        return 1

    print(render(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
