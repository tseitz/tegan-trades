"""Net worth: every portfolio's priced holdings plus its cash, plus Treasury's parked pot,
summed into the one figure ADR-0007 defines. Pure — no I/O.

**This is the only place in the repo that crosses a mandate boundary on purpose.** Everywhere
else, ADR-0007's deployed-versus-idle line is what keeps each dollar singular, and
`TreasuryResult.idle` is read by Treasury and summed by nobody but this module. `#75`
(`digest.cli.build`) already loads every portfolio once and holds a parked total in hand
within the same run — nothing here fetches or reads state, it only sums what is already there.

**One hazard nothing here can enforce.** A stablecoin swept into a portfolio's `cash:` by
`wallet-sync` *and* hand-typed as a parked row in `data/treasury.yaml` double-counts, and no
test can catch it — only the person editing `data/treasury.yaml` can. This total also excludes
the execution book (the digest's `HOLDING` section): the trading venues are not a Mandate, so a
reader comparing the two numbers should not expect them to reconcile.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class NetWorth:
    """Tonight's total, plus what last night's was — see `delta`."""
    total: float
    pots: int
    unpriced: int
    previous: float | None = None
    bootstrap: bool = False

    @property
    def change(self) -> float | None:
        return None if self.previous is None else self.total - self.previous

    @property
    def change_pct(self) -> float | None:
        """A fraction (`0.021`, not `2.1`) — `fmt.pct` multiplies by 100 itself, and a value
        already scaled here would render `+210.0%`. `None` against a previous total of zero:
        that division is not a percentage, it is a claim about growth from nothing."""
        if self.previous is None or self.previous == 0:
            return None
        return (self.total - self.previous) / self.previous


def _pot(result) -> tuple[float, int]:
    """One portfolio's contribution: priced holdings plus cash, by the same precedence
    `treasury.book._idle_rows` already uses — `cash_by_account` when present, else `cash`.
    `result` is duck-typed like `holdings.Change.reading`, so this module stays pure: it reads
    `.readings[*].market_value` and `.book.cash`/`.book.cash_by_account`, nothing else."""
    market = 0.0
    unpriced = 0
    for reading in result.readings:
        if reading.market_value is None:
            unpriced += 1
        else:
            market += reading.market_value

    book = result.book
    cash = sum(book.cash_by_account.values()) if book.cash_by_account else (book.cash or 0.0)
    return market + cash, unpriced


def of(results, *, parked: float | None) -> NetWorth | None:
    """Tonight's total, or `None` when it would be a guess or there is nothing to sum.

    `results is None` or `parked is None` means a read failed — a total would misrepresent
    what actually happened tonight, so none prints. `not results and not parked` means there is
    truly nothing on either side — a fresh clone with no portfolio files and no
    `data/treasury.yaml`. Neither of those is a total of zero: that is a claim about your
    money, and on a night a read failed it would be a false one. An empty `results` with a real
    `parked` figure is a genuine net worth (a machine with no portfolio files and a populated
    `data/treasury.yaml`), and prints as Treasury-only.
    """
    if results is None or parked is None:
        return None
    if not results and not parked:
        return None

    total = float(parked)
    unpriced = 0
    for result in results:
        value, pot_unpriced = _pot(result)
        total += value
        unpriced += pot_unpriced

    # Treasury earns a pot only when it actually holds money — `parked == 0.0` cannot be told
    # apart from "no file", so counting it unconditionally would claim a fifth mandate that
    # isn't there.
    pots = len(results) + (1 if parked else 0)
    return NetWorth(total=total, pots=pots, unpriced=unpriced)


def delta(net: NetWorth, remembered: float | None) -> NetWorth:
    """Attach last night's total. Mirrors `treasury.delta`: a `None` memory means this key has
    never been written, which is the first-night `bootstrap` — not a previous total of zero."""
    return replace(net, previous=remembered, bootstrap=remembered is None)


def remember(net: NetWorth) -> float:
    """Tonight's total, for tomorrow's `delta`."""
    return net.total
