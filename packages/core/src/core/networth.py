"""Net worth: every portfolio's priced holdings plus its cash, plus Treasury's parked pot,
summed into the one figure ADR-0007 defines, plus the nightly ledger row that records it and
the change over N days read back from that ledger (ADR-0013). Pure — no I/O; the file lives in
the `networth` package.

**This is the only place in the repo that crosses a mandate boundary on purpose.** Everywhere
else, ADR-0007's deployed-versus-idle line is what keeps each dollar singular, and
`TreasuryResult.idle` is read by Treasury and summed by nobody but this module. Callers hand in
results they already loaded — nothing here fetches or reads state.

**One hazard nothing here can enforce.** A stablecoin swept into a portfolio's `cash:` by
`wallet-sync` *and* hand-typed as a parked row in `data/treasury.yaml` double-counts, and no
test can catch it — only the person editing `data/treasury.yaml` can. This total also excludes
the execution book (the digest's `HOLDING` section): the trading venues are not a Mandate, so a
reader comparing the two numbers should not expect them to reconcile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


@dataclass(frozen=True, slots=True)
class NetWorth:
    """Tonight's total across every pot."""
    total: float
    pots: int
    unpriced: int
    # Left out of `total`, and counted in `unpriced`: `ReviewResult.mismatched` says our price
    # and the file's own mark are too far apart to be one asset.
    mismatched: tuple[str, ...] = ()
    parked: float = 0.0
    by_book: dict[str, float] = field(default_factory=dict)


def _pot(result) -> tuple[float, int, list[str]]:
    """One portfolio's contribution: priced holdings plus cash, by the same precedence
    `treasury.book._idle_rows` already uses — `cash_by_account` when present, else `cash`.
    `result` is duck-typed like `holdings.Change.reading`, so this module stays pure: it reads
    `.readings[*].market_value`, `.mismatched` and `.book.cash`/`.book.cash_by_account`,
    nothing else."""
    mismatched = [ticker for ticker, _, _ in result.mismatched]
    market = 0.0
    unpriced = 0
    for reading in result.readings:
        if reading.market_value is None or reading.holding.ticker in mismatched:
            unpriced += 1
        else:
            market += reading.market_value

    book = result.book
    cash = sum(book.cash_by_account.values()) if book.cash_by_account else (book.cash or 0.0)
    return market + cash, unpriced, mismatched


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
    mismatched: list[str] = []
    by_book: dict[str, float] = {}
    for result in results:
        value, pot_unpriced, pot_mismatched = _pot(result)
        total += value
        unpriced += pot_unpriced
        mismatched += pot_mismatched
        by_book[result.book.name] = value

    # Treasury earns a pot only when it actually holds money — `parked == 0.0` cannot be told
    # apart from "no file", so counting it unconditionally would claim a fifth mandate that
    # isn't there.
    pots = len(results) + (1 if parked else 0)
    return NetWorth(total=total, pots=pots, unpriced=unpriced,
                    mismatched=tuple(dict.fromkeys(mismatched)), parked=float(parked),
                    by_book=by_book)


# ── the ledger row ───────────────────────────────────────────────────────────


class RowError(ValueError):
    """A ledger line missing a field or carrying one of the wrong type."""


@dataclass(frozen=True, slots=True)
class Row:
    """One night in `data/networth.jsonl`. `by_book` is keyed by portfolio file stem, the name
    `review.cli.load_books` uses — renaming a file splits that book's series."""
    date: date
    recorded_at: datetime
    total: float
    parked: float
    by_book: dict[str, float]
    unpriced: int
    mismatched: tuple[str, ...]
    prices_fetched_at: datetime | None
    stale: bool

    @property
    def trusted(self) -> bool:
        return not self.stale and not self.mismatched

    def to_json(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "total": self.total,
            "parked": self.parked,
            "by_book": self.by_book,
            "unpriced": self.unpriced,
            "mismatched": list(self.mismatched),
            "prices_fetched_at": (self.prices_fetched_at.isoformat()
                                  if self.prices_fetched_at else None),
            "stale": self.stale,
        }


def row(net: NetWorth, *, on: date, recorded_at: datetime,
        prices_fetched_at: datetime | None, stale: bool) -> Row:
    return Row(date=on, recorded_at=recorded_at, total=net.total, parked=net.parked,
               by_book=dict(net.by_book), unpriced=net.unpriced, mismatched=net.mismatched,
               prices_fetched_at=prices_fetched_at, stale=stale)


def parse(raw) -> Row:
    """Raises `RowError` rather than defaulting a field: a guessed value in a ledger that cannot
    be rebuilt would read as recorded fact."""
    if not isinstance(raw, dict):
        raise RowError(f"expected an object, got {type(raw).__name__}")
    try:
        by_book = raw["by_book"]
        if not isinstance(by_book, dict):
            raise RowError("by_book is not an object")
        fetched = raw["prices_fetched_at"]
        recorded_at = datetime.fromisoformat(raw["recorded_at"])
        # `latest_per_date` compares these, and naive against aware raises.
        if recorded_at.tzinfo is None:
            raise RowError("recorded_at has no timezone")
        return Row(
            date=date.fromisoformat(raw["date"]),
            recorded_at=recorded_at,
            total=float(raw["total"]),
            parked=float(raw["parked"]),
            by_book={str(k): float(v) for k, v in by_book.items()},
            unpriced=int(raw["unpriced"]),
            mismatched=tuple(str(t) for t in raw["mismatched"]),
            prices_fetched_at=datetime.fromisoformat(fetched) if fetched else None,
            stale=bool(raw["stale"]),
        )
    except KeyError as exc:
        raise RowError(f"missing field {exc.args[0]!r}") from exc
    except (TypeError, ValueError) as exc:
        raise RowError(str(exc)) from exc


def latest_per_date(rows) -> list[Row]:
    """One row per date, oldest first. A re-run the same day supersedes the earlier row by
    `recorded_at`, never by file position: the mirror merge appends the other machine's rows
    at the end in whatever order they arrive."""
    best: dict[date, Row] = {}
    for r in rows:
        if r.date not in best or r.recorded_at > best[r.date].recorded_at:
            best[r.date] = r
    return [best[d] for d in sorted(best)]


# ── change over N days ───────────────────────────────────────────────────────

HORIZONS = (1, 7, 30, 365)

# How many days before the target a baseline may fall and still be labelled that horizon.
# Zero for 1d: yesterday or nothing, else a missed night reads as one day's move.
_SLACK = {1: 0, 7: 1, 30: 3, 365: 14}


@dataclass(frozen=True, slots=True)
class Change:
    days: int
    baseline: Row
    amount: float
    pct: float | None
    # Part of the move may be coverage, not value: the two nights left out different rows, or
    # tonight itself is stale or left one out.
    uneven: bool


def baseline(rows, *, today: date, days: int) -> Row | None:
    """The newest trusted row dated within the horizon's window, or `None`."""
    target = today - timedelta(days=days)
    earliest = target - timedelta(days=_SLACK.get(days, 0))
    fits = [r for r in latest_per_date(rows) if earliest <= r.date <= target and r.trusted]
    return fits[-1] if fits else None


def horizons(total: float, unpriced: int, rows, *, today: date,
             trusted: bool = True) -> dict[int, Change]:
    """Every horizon in `HORIZONS` the ledger can answer. Absent, not zero, when it can't."""
    out = {}
    for days in HORIZONS:
        base = baseline(rows, today=today, days=days)
        if base is None:
            continue
        out[days] = Change(
            days=days, baseline=base, amount=total - base.total,
            pct=None if base.total == 0 else (total - base.total) / base.total,
            uneven=base.unpriced != unpriced or not trusted,
        )
    return out
