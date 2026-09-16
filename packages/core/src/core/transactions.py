"""One brokerage transaction, and the taxonomy #71's held-flat math reads it through.

``type``/``subtype`` are stored verbatim from Plaid, not normalised away — Plaid puts a
dividend and a deposit under the same ``type: "cash"``, and only ``subtype`` tells them apart,
so collapsing the pair at storage time would destroy that distinction before anything gets a
chance to use it.

``kind`` is the single taxonomy: #71 consumes it rather than re-deriving a second mapping from
``(type, subtype)`` that would then have to be kept in step with this one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Filled from `scripts/probe_plaid_transactions.py` against the real retirement Item, per
# `oracle.plaid`'s own rule for an unverifiable Plaid claim: the probe is the authority, not
# the docs page. See that probe's docstring for the observed pairs and counts — `buy/buy`,
# `sell/sell`, `cash/dividend`, `cash/interest`, `cash/withdrawal` and `fee/miscellaneous fee`
# are all confirmed against real data; `cash/deposit` and the other `dividend`/`fee` spellings
# below are Plaid's documented vocabulary, carried over unconfirmed because no matching
# transaction has happened on this account in the probed window.
#
# `('transfer', 'transfer')` is deliberately absent from every set below and falls to "other":
# M1 sends that exact pair for both directions of a cash movement, so type/subtype alone cannot
# say which way it went — only `amount`'s sign can, and this taxonomy maps, it does not compute.
_DEPOSIT = frozenset({("cash", "deposit"), ("cash", "contribution")})
_WITHDRAWAL = frozenset({("cash", "withdrawal"), ("cash", "distribution")})
_DIVIDEND = frozenset({("cash", "dividend"), ("cash", "dividend reinvestment"),
                       ("cash", "qualified dividend"), ("cash", "interest")})
_BUY = frozenset({("buy", "buy"), ("buy", "reinvest")})
_SELL = frozenset({("sell", "sell")})
_FEE = frozenset({("fee", "miscellaneous fee"), ("fee", "management fee"),
                  ("fee", "account fee"), ("cash", "fee")})


@dataclass(frozen=True, slots=True)
class InvestmentTransaction:
    """One row from ``/investments/transactions/get``, as Plaid reported it.

    ``security_id``/``ticker`` are both ``None`` on a cash movement — a deposit or a dividend
    has no security, and that is correct, not a gap. ``amount`` follows Plaid's own sign
    convention rather than a normalised one, so a caller comparing this against Plaid's own
    docs sees the same number.
    """
    id: str
    account_id: str
    security_id: str | None
    ticker: str | None
    date: date
    quantity: float | None
    price: float | None
    amount: float
    fees: float | None
    type: str
    subtype: str

    @property
    def kind(self) -> str:
        """"deposit" | "withdrawal" | "dividend" | "buy" | "sell" | "fee" | "other".

        Maps; does not compute. What a deposit *does* to a held-flat basket is #71's job — this
        only makes "deposits, withdrawals and dividends are distinguishable" a property a test
        can assert.
        """
        pair = (self.type, self.subtype)
        if pair in _DEPOSIT:
            return "deposit"
        if pair in _WITHDRAWAL:
            return "withdrawal"
        if pair in _DIVIDEND:
            return "dividend"
        if pair in _BUY:
            return "buy"
        if pair in _SELL:
            return "sell"
        if pair in _FEE:
            return "fee"
        return "other"


@dataclass(frozen=True, slots=True)
class TransactionSpan:
    """The shape of one account's cached history — how far back it reaches, and how much of
    it there is. ``oldest is None`` means an empty cache, distinct from no cache file at all
    (``None`` at the ``TransactionSpan | None`` level in ``review.cli.ReviewResult``)."""
    oldest: date | None
    newest: date | None
    count: int

    @classmethod
    def of(cls, transactions) -> TransactionSpan:
        dates = [t.date for t in transactions]
        if not dates:
            return cls(oldest=None, newest=None, count=0)
        return cls(oldest=min(dates), newest=max(dates), count=len(dates))
