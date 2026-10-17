"""``kraken-sync`` — refresh one portfolio file from what Kraken says the account holds.

The flow, and why the book file is the opt-in, live in ``exchange_cli``.
"""

from __future__ import annotations

from oracle import exchange_cli, kraken_account

DEFAULT_BOOK = "kraken"

EXAMPLE = """\
account: {name}
domain: crypto
stale_after: 7

mandate:
  name: {name}
  benchmarks:
    - type: symbol
      key: btc
    - type: symbol
      key: eth
  horizon: swing
  risk_posture: aggressive
"""


def sync(argv: list[str] | None = None) -> int:
    return exchange_cli.run(
        argv,
        account=kraken_account,
        prog="kraken-sync",
        default_book=DEFAULT_BOOK,
        example=EXAMPLE,
        unpriced_why="no USD pair on Kraken, so no mark",
    )
