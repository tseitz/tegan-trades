"""Read a hand-kept list of what you actually own.

**These files live under ``data/``, which is gitignored, and that placement is the point.**
Everything else this package reads from ``cfg/`` is committed — routing, the roster, the
canon registry — and this repository is public. Share counts and cost basis are not
configuration, they are a statement of net worth, so they go where nothing can push them.

The format is deliberately dumb: a ticker and a share count per line. There is no M1 API to
pull a retirement account from, and the accounts this exists for barely move, so a file you
edit twice a year is not a workaround — it is the right size of tool. If a broker connection
ever arrives it produces ``Holding`` records too, and nothing downstream changes.

Every failure here raises rather than returning a partial book. A portfolio review that
quietly drops the row it could not parse tells you the position is fine, which is the one
answer it must never give by accident.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import yaml
from core.nearby import ALL_KINDS
from core.review import Holding

DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "portfolios"

DEFAULT_HORIZON = "long"

# What routing assumes an asset is when the file does not say. `crypto` is the only value
# `oracle.route` treats specially — everything else lands on Yahoo — so "stock" is both the
# common case for an account like this and the safe one to be wrong about.
DEFAULT_DOMAIN = "stock"

# Which level sources a portfolio counts, when the file does not narrow them. All four by
# default: start wide and cut back once you know which ones you actually read. The knob is
# here rather than on a CLI flag because the answer belongs to the account — a decade-horizon
# retirement book and a swing account want different sources from the same code.
DEFAULT_LEVEL_KINDS = ALL_KINDS


class PortfolioError(Exception):
    """A portfolio file that cannot be trusted. Always names the offending row."""


@dataclass(frozen=True, slots=True)
class Position:
    """A holding plus the one routing fact the corpus cannot supply.

    ``domain`` lives here rather than on ``Holding`` because it is not part of owning
    something — it is a hint about where to find its price. ``core.review`` never reads it.
    """
    holding: Holding
    domain: str


@dataclass(frozen=True, slots=True)
class Portfolio:
    """One account's holdings, as written down.

    ``horizon`` is carried but not yet acted on. It is here because the retirement account
    and the day-trading account want different answers to the same chart — noted now so the
    file format does not have to change when that lands.
    """
    name: str
    horizon: str
    positions: tuple[Position, ...]
    level_kinds: tuple[str, ...] = DEFAULT_LEVEL_KINDS

    @property
    def holdings(self) -> tuple[Holding, ...]:
        return tuple(p.holding for p in self.positions)

    @property
    def domain_rows(self) -> tuple[tuple[str, str], ...]:
        """``(asset, domain)`` pairs in the shape ``route.build_domain_consensus`` wants.

        Fed *alongside* the corpus rows, never instead of them. One row per holding cannot
        outvote the hundreds a discussed asset carries, so the file supplies an answer only
        where the corpus has none — which is exactly the case this exists for.
        """
        return tuple((p.holding.ticker, p.domain) for p in self.positions)


def names_to_load(explicit, *, every: bool, root: Path = DATA_ROOT) -> tuple[str, ...]:
    """Which portfolios a run should warm: the ones named, plus all of them when ``every``.

    Deduplicated, and the explicit names keep their order — so a run that lists one account
    and also asks for all of them fetches each exactly once. ``every`` exists for the nightly,
    which cannot enumerate files a shell script has never heard of: hardcoding a name there
    would silently stop warming any account added afterwards, and `review` would print it
    unpriced forever without anything failing.
    """
    ordered = list(explicit)
    if every:
        ordered += [n for n in available(root=root) if n not in ordered]
    seen: dict[str, None] = {}
    for name in ordered:
        seen.setdefault(name, None)
    return tuple(seen)


def canonical_domain_rows(book: Portfolio, registry) -> list[tuple[str, str]]:
    """``(canonical asset, domain)`` per position, in the shape the routing table wants.

    A separate step rather than something ``load`` does, and the split is deliberate: the file
    stays exactly what you wrote, and the registry stays the single place an alias becomes an
    asset. Both ``fetch-prices`` and ``review`` call this, so the asset a holding is fetched
    under and the asset it is later routed under can never be two different strings.
    """
    from core.canon import resolve_asset

    return [(resolve_asset(p.holding.ticker, registry)[0], p.domain) for p in book.positions]


def fetch_rows(domain_rows, *, since: date):
    """Corpus-row-shaped stand-ins so ``plan_fetches`` can window assets it has never seen.

    ``plan_fetches`` opens each series at its asset's earliest *mention*, and a holding has no
    mention — an undated row is skipped outright rather than fetched. ``since`` supplies one,
    and it should be the same history floor the run is using, so a portfolio ticker gets the
    lookback weekly structure needs rather than a window starting today.
    """
    return [
        SimpleNamespace(asset=asset, domain=domain, published_at=since.isoformat())
        for asset, domain in domain_rows
    ]


def available(*, root: Path = DATA_ROOT) -> tuple[str, ...]:
    """Every portfolio on disk, sorted. A missing directory is normal — you may not have
    written one yet — so it yields nothing rather than raising."""
    if not root.is_dir():
        return ()
    return tuple(sorted(p.stem for p in root.glob("*.yaml")))


def load(name: str, *, root: Path = DATA_ROOT) -> Portfolio:
    """Read one portfolio, or raise ``PortfolioError`` saying exactly what is wrong."""
    path = root / f"{name}.yaml"
    if not path.is_file():
        known = available(root=root)
        listing = ", ".join(known) if known else "none found"
        raise PortfolioError(f"no portfolio {name!r} at {path} (available: {listing})")

    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PortfolioError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise PortfolioError(f"{path} must be a mapping with a `positions:` list")

    rows = doc.get("positions")
    if not isinstance(rows, list) or not rows:
        raise PortfolioError(f"{path} has no positions")

    fallback_domain = str(doc.get("domain") or DEFAULT_DOMAIN)
    positions: list[Position] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        holding = _holding(row, index=index, path=path)
        if holding.ticker in seen:
            raise PortfolioError(
                f"{path} position {index}: {holding.ticker} already appears at position "
                f"{seen[holding.ticker]} — one row per ticker"
            )
        seen[holding.ticker] = index
        positions.append(Position(
            holding=holding,
            domain=str(row.get("domain") or fallback_domain),
        ))

    return Portfolio(
        name=str(doc.get("account") or name),
        horizon=str(doc.get("horizon") or DEFAULT_HORIZON),
        positions=tuple(positions),
        level_kinds=_level_kinds(doc.get("levels"), path=path),
    )


def _level_kinds(raw, *, path: Path) -> tuple[str, ...]:
    """Validated against ``ALL_KINDS`` rather than passed through.

    An unknown name would silently match nothing, so ``levels: [weekly]`` — the obvious typo
    for ``weekly_zone`` — would produce an empty section that looks exactly like an account
    with no levels near it. Refusing names the mistake instead.
    """
    if raw is None:
        return DEFAULT_LEVEL_KINDS
    if not isinstance(raw, list) or not all(isinstance(k, str) for k in raw):
        raise PortfolioError(f"{path}: `levels` must be a list of names from "
                             f"{', '.join(ALL_KINDS)}")
    unknown = [k for k in raw if k not in ALL_KINDS]
    if unknown:
        raise PortfolioError(f"{path}: unknown level kind(s) {', '.join(unknown)} — "
                             f"pick from {', '.join(ALL_KINDS)}")
    return tuple(raw)


def _holding(row, *, index: int, path: Path) -> Holding:
    # 1-based: this string is read by someone with the YAML file open, counting rows by eye.
    where = f"{path} position {index}"
    if not isinstance(row, dict):
        raise PortfolioError(f"{where}: expected a mapping, got {type(row).__name__}")

    raw_ticker = row.get("ticker")
    if not isinstance(raw_ticker, str) or not raw_ticker.strip():
        raise PortfolioError(f"{where}: missing `ticker`")
    # Upcased and trimmed, and nothing further. Resolving an alias to its canonical asset is
    # `core.canon`'s job and happens later against the registry; a second naming authority
    # here would be free to disagree with it.
    ticker = raw_ticker.strip().upper()

    shares = _number(row.get("shares"), field="shares", where=where)
    if shares is None or shares <= 0:
        raise PortfolioError(f"{where}: `shares` must be a positive number, got {row.get('shares')!r}")

    return Holding(ticker=ticker, shares=shares, cost=_number(row.get("cost"), field="cost",
                                                              where=where))


def _number(value, *, field: str, where: str) -> float | None:
    """None only for a genuinely absent field. A present-but-unparseable one raises, because
    treating ``shares: "a lot"`` as "unspecified" would size a position at nothing."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioError(f"{where}: `{field}` is not a number: {value!r}") from exc
