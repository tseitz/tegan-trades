"""``coinbase-sync`` — refresh one portfolio file from what Coinbase says the account holds.

Same flow as ``kraken-sync`` (``exchange_cli``), and the same opt-in: the book file. Two refusals
exit 1 and leave the file alone: a key with any permission beyond View, and a breakdown that
does not reconcile to Coinbase's own total.
"""

from __future__ import annotations

from oracle import coinbase_account, exchange_cli

DEFAULT_BOOK = "coinbase"

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
        account=coinbase_account,
        prog="coinbase-sync",
        default_book=DEFAULT_BOOK,
        example=EXAMPLE,
        unpriced_why="Coinbase gave no USD value",
    )
