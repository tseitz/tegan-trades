"""Which venues exist, which networks each one has, and which of those spend real money.

One table rather than a condition repeated in four places. The reason it is worth its own
module is the last column: ``mainnet`` and ``live`` are the two strings in this repo that mean
"this transaction cannot be undone", and they belong to different venues. A check written as
``network == "mainnet"`` is correct for Hyperliquid and silently wrong for Alpaca — it would
wave a real brokerage order through as though it were a rehearsal.

Each venue's rehearsal network is genuinely free and genuinely full-fidelity, so the default
is always the rehearsal and reaching real money always takes an explicit, typed act.
"""
from __future__ import annotations

from execution.alpaca_broker import LIVE, PAPER
from execution.broker import MAINNET, TESTNET

HYPERLIQUID = "hyperliquid"
ALPACA = "alpaca"

# venue -> (rehearsal, real money). Order matters: the first entry is the default.
NETWORKS: dict[str, tuple[str, str]] = {
    HYPERLIQUID: (TESTNET, MAINNET),
    ALPACA: (PAPER, LIVE),
}

# Every network on which an order costs real money, across all venues. Membership here is
# what triggers the typed confirmation — see ``config.requires_typed_confirmation``.
REAL_MONEY = frozenset(real for _, real in NETWORKS.values())


def default_network(venue: str) -> str:
    """The rehearsal network for a venue. Raises on an unknown venue rather than guessing —
    a venue nobody recognises must not silently resolve to somewhere money can move."""
    if venue not in NETWORKS:
        raise ValueError(f"unknown venue {venue!r}; expected one of {sorted(NETWORKS)}")
    return NETWORKS[venue][0]


def is_real_money(network: str) -> bool:
    return network in REAL_MONEY
