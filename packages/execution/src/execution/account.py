"""What the venue says about the account, read once and parsed. Pure.

Three separate things were being *inferred* from ``equity``, and every inference was wrong in
the same silent way: the order was reported placed, and the venue killed it hours later at the
open, where nothing was watching.

* **How much room is left.** Sizing runs off equity, which does not move when an order rests
  or a position opens, so eight 1%-risk orders sized against the same $100,000 wanted 123.6%
  of it. ``buying_power`` is the venue's own answer and it nets both.
* **Whether this account can short at all.** ``guards.check_shortable`` inferred a margin
  account from Reg T's $2,000 equity minimum. The paper account holds $99,674 and cannot
  short — ``shorting_enabled`` is false. Two of the three rejections were this, not the budget.
* **How much leverage the broker will actually hold.** ``multiplier`` is 1 here, against a
  ``max_notional_frac`` of 3.0 measured on perps (``docs/IMPROVEMENTS.md`` §36).

MEASURED on Alpaca paper 2026-07-29, and the arithmetic is exact:
``equity 99,674.47 - initial_margin 74,702.95 = buying_power 24,971.52``, where that initial
margin is one open position ($48,219.38) plus three resting bracket entries ($26,483.57). So
**one field answers "what have I already spoken for"** and it cannot drift out of step with
the venue, which enumerating positions and orders separately eventually would.

**Every field is optional and ``None`` means "the venue did not say".** That is not defensive
padding — it is the same asymmetry ``participation.check_depth`` is built on. An unreadable
buying power that defaulted to 0.0 would refuse every order in the account, which is a far
worse failure than the one this module exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    """The venue's own view of what this account can do right now.

    ``committed`` is initial margin — positions **and** resting orders together. It is carried
    beside ``buying_power`` rather than derived from it because the two answer different
    questions: buying power is what is left, committed is what is holding the rest, and the
    confirmation preview needs both to be worth reading.
    """
    equity: float
    buying_power: float | None = None
    committed: float | None = None
    multiplier: float | None = None
    can_short: bool | None = None

    def headroom(self, placed: float = 0.0) -> float | None:
        """Notional this account can still commit, less what this session has already sent.

        ``None`` when the venue reported no buying power, which leaves the budget gate off
        rather than refusing everything.

        **``placed`` is not redundant with re-reading the venue.** Alpaca accepted eight
        brackets between 03:24 and 04:01 ET on 2026-07-29 and rejected three at the 09:30
        open — so buying power does not necessarily decrement while the market is shut, and a
        fresh read per candidate would have shown the same $24,971 eight times over. The local
        total is what lets the eighth order see the first seven; the venue read is what keeps
        the total honest across sessions. Both, or neither works.

        Floors at zero: an over-committed account has no room, not negative room.
        """
        if self.buying_power is None:
            return None
        return max(0.0, self.buying_power - max(0.0, placed))


def _number(payload: dict, key: str) -> float | None:
    """A numeric field, or None if absent or unparseable. Alpaca sends these as strings."""
    raw = payload.get(key)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_account(payload) -> Account | None:
    """The account fields this package acts on. ``None`` if the reply is not an account.

    Returning None rather than a zeroed ``Account`` is load-bearing: downstream, None disables
    the budget gate and a zero refuses every order for having no buying power. A 4xx body or a
    renamed field must produce the first, never the second.
    """
    if not isinstance(payload, dict):
        return None
    equity = _number(payload, "equity")
    if equity is None:
        return None

    shorting = payload.get("shorting_enabled")
    return Account(
        # Floored, matching the equity read this replaces: a debit balance is not a budget.
        equity=max(0.0, equity),
        buying_power=_number(payload, "buying_power"),
        committed=_number(payload, "initial_margin"),
        multiplier=_number(payload, "multiplier"),
        can_short=bool(shorting) if isinstance(shorting, bool) else None,
    )
