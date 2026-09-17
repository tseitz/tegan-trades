"""Read a hand-kept list of money parked for its yield — ADR-0008 "Treasury as a Pot".

**Lives outside ``data/portfolios/`` on purpose.** ``portfolios.available()`` globs that
directory for every account to chart, and a parked amount has no ticker and no price series —
it would print ``NO_READ`` in ``review`` nightly for no reason. A separate path means no
skip-list to maintain there, and the glob keeps meaning "pots you read on a chart."

**Rows, not a bare ``cash:`` figure.** ``portfolios.load`` refuses a file whose ``positions:``
list is empty, so a cash-only file in that shape would not load at all. A row is also the right
shape here: a parked amount carries a venue and an APY, which a single float cannot.

**Hand-kept, never synced.** ``portfolios.write_positions`` replaces everything below the
``positions:`` marker on every sync, so a synced file can never also hold a hand-typed row — an
off-chain T-bill would be erased the next time a broker sync ran. Nothing in this repo writes
``data/treasury.yaml``; deploying money is rare and deliberate, so typing the row by hand is not
a burden. See ``cfg/treasury.example.yaml`` for the worked deployed-versus-idle example.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from oracle import portfolios

# src/oracle/treasury_file.py -> src/oracle -> src -> oracle -> packages -> <repo root>
TREASURY_PATH = Path(__file__).resolve().parents[4] / "data" / "treasury.yaml"


class TreasuryError(Exception):
    """A treasury file that cannot be trusted. Always names the offending row.

    ``load`` converts a ``portfolios.PortfolioError`` raised while parsing the shared
    ``mandate:`` block into this type, so a caller that catches ``TreasuryError`` alone can
    never miss a malformed mandate.
    """


@dataclass(frozen=True, slots=True)
class ParkedRow:
    """One parked amount, as written down.

    ``amount`` is USD, hand-typed — never ``shares × price``. An off-chain T-bill has no
    ticker, no ``oracle_map`` route and no price series, so a shares-and-price row cannot
    represent it, and Treasury's whole premise is that this principal holds its value rather
    than needing a nightly re-price. ``apy`` is likewise hand-typed: it is the receipt for the
    rate this specific row actually earns, not the venue's live headline rate — the yields feed
    and the Safety gate that would resolve the latter are #73's job, not this file's.
    """

    what: str
    amount: float
    venue: str
    apy: float | None = None
    since: date | None = None


@dataclass(frozen=True, slots=True)
class TreasuryBook:
    """Every parked row, as written down, plus the mandate that judges them."""

    mandate: portfolios.Mandate
    rows: tuple[ParkedRow, ...]
    updated: date


def load(*, path: Path = TREASURY_PATH) -> TreasuryBook | None:
    """Read the treasury file, or ``None`` if it does not exist yet.

    A missing file is normal — you may simply not have parked anything — the same instinct as
    ``portfolios.available()``'s missing directory. A file that *exists* but is malformed still
    raises, because a portfolio-shaped mistake here should look like one.
    """
    if not path.is_file():
        return None

    try:
        doc = portfolios.strict_load(path)
    except portfolios.PortfolioError as exc:
        raise TreasuryError(str(exc)) from exc
    if not isinstance(doc, dict):
        raise TreasuryError(f"{path} must be a mapping with a `parked:` list")

    rows = doc.get("parked")
    if not isinstance(rows, list) or not rows:
        raise TreasuryError(f"{path} has no `parked` rows")

    parked: list[ParkedRow] = []
    seen: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows, start=1):
        parked_row = _parked_row(row, index=index, path=path)
        key = (parked_row.venue, parked_row.what)
        if key in seen:
            raise TreasuryError(
                f"{path} row {index}: {parked_row.what} at {parked_row.venue} already "
                f"appears at row {seen[key]} — one row per venue+what"
            )
        seen[key] = index
        parked.append(parked_row)

    try:
        mandate = portfolios.parse_mandate(doc.get("mandate"), path=path)
    except portfolios.PortfolioError as exc:
        raise TreasuryError(str(exc)) from exc
    _require_flat_rate_only(mandate, path=path)

    return TreasuryBook(mandate=mandate, rows=tuple(parked), updated=_updated(doc.get("updated"), path=path))


def _require_flat_rate_only(mandate: portfolios.Mandate, *, path: Path) -> None:
    """ADR-0008 §"Benchmark": Treasury is judged against the cash rate alone, and nothing else —
    a bad-looking year against equities would teach the reader to skip the section. This raises
    rather than warns, per the repo's gates-vs-scores rule: it is a rule someone wrote, not a
    score on a continuum.

    A second `flat_rate` entry is not refused — `benchmarks.py`'s own anchor logic already
    accepts that as "an accepted limit, not a silent bug," and a loader stricter than the module
    it feeds would be two rules for one thing.
    """
    if mandate.name != "treasury":
        raise TreasuryError(
            f"{path}: `mandate.name` must be `treasury`, got {mandate.name!r} — "
            f"`benchmarks._anchor` keys the anchor file as f'{{mandate.name}}:flat_rate', "
            f"so a different name would silently anchor under a second key"
        )
    if any(b.type != "flat_rate" for b in mandate.benchmarks):
        raise TreasuryError(
            f"{path}: `mandate.benchmarks` must be `flat_rate` only — Treasury is judged "
            f"against the cash rate alone, never an index (ADR-0008)"
        )


def _parked_row(row, *, index: int, path: Path) -> ParkedRow:
    where = f"{path} row {index}"
    if not isinstance(row, dict):
        raise TreasuryError(f"{where}: expected a mapping, got {type(row).__name__}")

    what = row.get("what")
    if not isinstance(what, str) or not what.strip():
        raise TreasuryError(f"{where}: missing `what`")

    venue = row.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        raise TreasuryError(f"{where}: missing `venue`")

    amount = _number(row.get("amount"), field="amount", where=where)
    if amount is None or amount <= 0:
        raise TreasuryError(f"{where}: `amount` must be a positive number, got {row.get('amount')!r}")

    return ParkedRow(
        what=what.strip(),
        amount=amount,
        venue=venue.strip(),
        apy=_number(row.get("apy"), field="apy", where=where),
        since=_date(row.get("since"), field="since", where=where),
    )


def _updated(raw, *, path: Path) -> date:
    """Identical contract to ``portfolios._updated``: an unparseable date raises rather than
    falling back to the mtime, and this file needs that discipline more than a synced portfolio
    does — its whole risk is a hand-typed number nobody remembered to date."""
    if raw is None:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError as exc:
        raise TreasuryError(f"{path}: `updated` is not a date: {raw!r}") from exc


def _number(value, *, field: str, where: str) -> float | None:
    """None only for a genuinely absent field — present-but-unparseable raises, same contract
    as ``portfolios._number``."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise TreasuryError(f"{where}: `{field}` is not a number: {value!r}") from exc


def _date(value, *, field: str, where: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise TreasuryError(f"{where}: `{field}` is not a date: {value!r}") from exc
