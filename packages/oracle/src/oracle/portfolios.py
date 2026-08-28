"""Read a hand-kept list of what you actually own — and write the half a sync owns.

**Reading and writing live together on purpose.** ``plaid-sync`` and ``wallet-sync`` both fill
these files, and a second module that laid out the same YAML would be free to drift from the
reader below. One writer, one reader, one format.

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

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import yaml
from core.nearby import ALL_KINDS
from core.review import Holding
from core.setups import DEFAULT_HALF_LIFE

DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "portfolios"

# `core.thesis.Timeframe`, reused rather than restated. A fifth word here would be a second
# vocabulary for one idea, and `HalfLife` below already keys off these four.
HORIZONS = ("scalp", "swing", "position", "macro")
DEFAULT_HORIZON = "position"

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
    # What the broker says this holding is and what it is worth. Present only on a synced file;
    # a hand-kept one has no second opinion to offer. `mark` is never used as a price — it feeds
    # `core.review.mark_disagrees`, whose whole value is that it came from somewhere else.
    figi: str | None = None
    mark: float | None = None


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
    # When these positions were last true. Taken from the file's `updated:` line if it has
    # one, otherwise from its mtime — you edited the file when you edited the file, and a
    # date you have to remember to bump is the first thing that goes stale.
    updated: date | None = None
    stale_after: int = DEFAULT_HALF_LIFE.position
    # Money that could go into a position today, when the broker reported it. None means nobody
    # said — a hand-kept file, or a broker that omits the balance — and that has to stay
    # distinct from zero, because "no room to add" is a fact and "we do not know" is not.
    cash: float | None = None
    # Which account holds which part of it, when one file covers more than one. A Roth and a
    # Traditional IRA are one book to think about and their positions genuinely sum — the same
    # ticker in both is one exposure. Their cash does not: you cannot buy in the Roth with
    # Traditional money, so the total alone would name a sum you cannot spend anywhere.
    cash_by_account: dict[str, float] = field(default_factory=dict)

    def age_days(self, *, on: date) -> int | None:
        return None if self.updated is None else (on - self.updated).days

    def is_stale(self, *, on: date) -> bool:
        """Past the point where these positions probably no longer describe the account.

        **A hand-kept file is a snapshot pretending to be a feed.** That is fine on an account
        that changes twice a year and dangerous on one that changes weekly: every verdict here
        is computed against holdings that may not exist any more, and a confidently wrong
        answer is worse than no answer. Reported rather than refused — you are the one who
        knows whether you have traded.
        """
        age = self.age_days(on=on)
        return age is not None and age > self.stale_after

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
            figi=(str(row["figi"]) if row.get("figi") else None),
            mark=_number(row.get("mark"), field="mark", where=f"{path} position {index}"),
        ))

    horizon = str(doc.get("horizon") or DEFAULT_HORIZON)
    if horizon not in HORIZONS:
        raise PortfolioError(f"{path}: unknown `horizon` {horizon!r} — "
                             f"pick from {', '.join(HORIZONS)}")

    stale_after = doc.get("stale_after")
    return Portfolio(
        name=str(doc.get("account") or name),
        horizon=horizon,
        positions=tuple(positions),
        level_kinds=_level_kinds(doc.get("levels"), path=path),
        updated=_updated(doc.get("updated"), path=path),
        cash=_number(doc.get("cash"), field="cash", where=str(path)),
        cash_by_account=_cash_by_account(doc.get("cash_by_account"), path=path),
        # The view half-lives reused as a starting point, not a measurement of how fast a
        # portfolio file rots. They are the right shape — a scalper's book turns over in days
        # and a retirement book in years — but an actively traded account goes wrong far
        # sooner than its horizon suggests, which is what `stale_after:` is for.
        stale_after=(DEFAULT_HALF_LIFE.days_for(horizon) if stale_after is None
                     else int(stale_after)),
    )


def _updated(raw, *, path: Path) -> date:
    """When the positions were last true.

    An unparseable date raises rather than falling back to the mtime. The fallback would
    report a freshly-saved file as current at the exact moment you were trying to tell it the
    positions were six months old — the wrong direction to be wrong in.
    """
    if raw is None:
        # UTC to match every `as_of` in the repo. A file saved late in the evening
        # local time then dates to tomorrow, which costs at most a day against
        # thresholds measured in weeks — and a local date could read as negative age.
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError as exc:
        raise PortfolioError(f"{path}: `updated` is not a date: {raw!r}") from exc


def _cash_by_account(raw, *, path: Path) -> dict[str, float]:
    """Written by ``plaid-sync``; absent on a hand-kept file and on a single-account one."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PortfolioError(f"{path}: `cash_by_account` must be a mapping of account "
                             f"name to amount")
    out: dict[str, float] = {}
    for name, value in raw.items():
        amount = _number(value, field=f"cash_by_account.{name}", where=str(path))
        if amount is None:
            raise PortfolioError(f"{path}: `cash_by_account.{name}` has no amount")
        out[str(name)] = amount
    return out


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


# ─── writing ─────────────────────────────────────────────────────────────────────────────
# What a sync puts back. Only the generated half of the document is ever replaced; see
# `write_positions`.


@dataclass(frozen=True, slots=True)
class Source:
    """Who fills a portfolio file. Named by the module that does the filling, never here.

    Only ``domain`` reaches the reader — the other two appear in comments, so a file says out
    loud which command owns its ``positions:`` block and will overwrite anything typed there.
    """
    name: str        # how the banner spells it, e.g. "Plaid"
    command: str     # what to re-run, e.g. "plaid-sync"
    domain: str = DEFAULT_DOMAIN


@dataclass(frozen=True, slots=True)
class Row:
    """One position, in the shape ``load`` will read back."""
    ticker: str
    shares: float
    cost: float | None
    domain: str
    # The source's own identity and price for this holding. Neither is used to fetch anything —
    # they exist to be disagreed with. Every price in this repo is fetched *by ticker*, so a
    # wrong ticker is a confident wrong answer; the broker or the chain arrived at its mark from
    # the instrument the units actually sit in, which makes it the one independent check there
    # is. `figi` is the broker's security id; on a wallet it is the token contract address.
    figi: str | None = None
    mark: float | None = None


@dataclass(frozen=True, slots=True)
class Skipped:
    """A holding that could not become a row, and why. Never silently dropped: an account
    review that omits a position tells you it is fine, which is the one answer it must never
    give by accident.

    ``kind`` groups drops so a caller can print the handful that were judgement calls and count
    the rest. A wallet drops several hundred airdropped tokens per chain, and a report nobody
    can read to the end has failed in the same way as one that said nothing.
    """
    what: str
    why: str
    kind: str = ""


HEADER = """\
# {name}. Written by `uv run {command} {name}` — edit the settings above `positions:` freely,
# but anything you add to the list itself is overwritten on the next sync.
#
# Gitignored: this repo is public and share counts are not configuration.
account: {name}

horizon: {horizon}

domain: {domain}
"""


# What a sync wrote last time, stripped from the preserved half before rewriting. The `cash:`
# line matters most: left in place it would appear twice in one document, and PyYAML resolves a
# duplicate key silently by taking the last one. A settings file that quietly ignores a line you
# edited is worse than one that refuses it. The banner is here for the same reason at a smaller
# scale — nightly syncs would otherwise stack a comment per night forever. `\\S+` rather than a
# literal source name so a file keeps being recognised if it changes hands between syncs.
_GENERATED = re.compile(
    r"^(cash:.*|cash_by_account:.*(?:\n[ \t]+\S.*)*|# Synced from \S+ .*)$\n?",
    re.MULTILINE)


def _trimmed(value: float) -> str:
    """So a share count reads like a share count and not like float arithmetic."""
    return f"{value:.8f}".rstrip("0").rstrip(".") or "0"


def write_positions(path: Path, rows, *, source: Source, horizon: str = DEFAULT_HORIZON,
                    cash: float | None = None,
                    cash_by: dict[str, float] | None = None) -> None:
    """Replace the ``positions:`` block, keeping every line above it exactly as written.

    The settings and comments in a portfolio file are yours — ``levels:``, ``stale_after:``,
    the note about why a number is what it is. A sync that rewrote the whole document would
    delete them nightly, so it rewrites only the generated half. ``updated:`` is deliberately
    not written: the mtime moves when this writes, and a date the code has to remember to bump
    is the first thing to go stale.
    """
    doc = {}
    if path.exists():
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text) or {}
        head, marker, _ = text.partition("\npositions:")
        prefix = (head + "\n") if marker else text.rstrip("\n") + "\n\n"
    else:
        prefix = HEADER.format(name=path.stem, command=source.command, horizon=horizon,
                               domain=source.domain)

    # The file's own `domain:` and not the source's: a per-row `domain:` is written only where
    # it differs from what this document already declares. Reading the source here instead
    # would stamp every row of a hand-started file that says `domain: crypto` with a redundant
    # line, or worse, omit the line that makes a row route correctly.
    fallback = str((doc if isinstance(doc, dict) else {}).get("domain") or DEFAULT_DOMAIN)
    lines = [_GENERATED.sub("", prefix).rstrip("\n"), "",
             f"# Synced from {source.name} {datetime.now(UTC).date().isoformat()}."]
    if cash is not None:
        lines.append(f"cash: {cash:.2f}")
    # Only when a file covers more than one account. On a single-account book the split would
    # restate the total under a second name, and a report that says the same number twice
    # trains the eye past both.
    if cash_by and len(cash_by) > 1:
        lines.append("cash_by_account:")
        lines += [f"  {name}: {value:.2f}" for name, value in sorted(cash_by.items())]
    lines.append("positions:")
    for row in rows:
        lines.append(f"  - ticker: {row.ticker}")
        lines.append(f"    shares: {_trimmed(row.shares)}")
        if row.cost is not None:
            lines.append(f"    cost: {_trimmed(row.cost)}")
        if row.domain != fallback:
            lines.append(f"    domain: {row.domain}")
        # Written for the reader's benefit as much as the code's: `figi` is what lets you settle
        # by hand which instrument a row really is, when the mark check says two prices disagree.
        if row.figi:
            lines.append(f"    figi: {row.figi}")
        if row.mark is not None:
            lines.append(f"    mark: {_trimmed(row.mark)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
