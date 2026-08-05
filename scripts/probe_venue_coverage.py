"""Which venue listing is actually the asset we think it is — confirmed by price, not by name.

`cfg/venue_map.yaml` is hand-curated because a name match is silently catastrophic: the
venues list a memecoin under `SPX`, and an order placed off that match shorts the wrong
instrument with real money. That paranoia is right and this probe does not weaken it. It
supplies the *evidence* a human needs to fill the map, and it is the price that supplies it.

**The test is the mark.** For every canonical asset the corpus prices, take the last cached
close and ask each venue what its same-named market marks at. The two numbers settle identity
on their own: SPX closed 7403 while the venues' `SPX` marks 0.33 — a ratio of 4e-5, not a
judgement call. SPY against the S&P is 1/10.03, which is the proxy case `scale` exists for and
reads as a clean ratio rather than noise. So the probe reports three things and decides only
the first:

    MATCH      mark within tolerance of our close        -> safe to add, verbatim
    IN RANGE   not the close, but inside the week's band -> a timing gap; a human confirms
    DIFFERS    a real market, a different number         -> the ratio says proxy or trap
    NO PRICE   we hold no fresh close for this asset     -> cannot confirm, so do not add

**DIFFERS is never auto-classified.** A 10.03x ratio is a proxy and a 4e-5 ratio is a
collision, but nothing in the arithmetic says which — and the failure mode of guessing is an
order on a memecoin. The ratio is printed; the reader decides.

**Already-mapped pairs are re-checked, not skipped.** The 30 entries curated by hand are the
part of the file nothing has ever verified, and a typo there is worse than a gap: a gap prints
`not executable` and a typo places an order. They report under the same verdicts.

Free — public endpoints, no key, nothing placed. One request per venue (four for Hyperliquid's
core book plus each HIP-3 builder), not one per asset.

    uv run python scripts/probe_venue_coverage.py
    uv run python scripts/probe_venue_coverage.py --verdict MATCH --unmapped-only

## How to read a DIFFERS

**Venues agreeing with each other and disagreeing with us is a timing gap, not an identity
one.** `BE` marked 187.4 on all three venues against our 166.8 close — and 187.4 is BE's
*previous* session to within 0.4%, on a name that moved 12% in a day. Three independent books
do not share a collision. A collision looks like `BB`: 8.03 on Hyperliquid and Lighter, 0.016
on Aster, because Aster's `BBUSDT` is a token and the other two are BlackBerry.

**A ratio far from 1 that both legs agree on is a proxy, and it belongs in `scale`.** `RUT`
marked 0.0995 of the index on Lighter and Aster alike — that is IWM, and the map had it
recorded as a 1:1 listing.

**Our own price can be the wrong instrument**, and this probe reads as a venue problem when it
is. `WTI` routed to the equity ticker WTI (W&T Offshore, $3.18) while Lighter's WTI is crude at
$82.61; the venue was right. Check `route()` before blaming the mark — a route flagged
`needs_validation` is a guess off a transcript and is the first thing to doubt.

## What this probe cannot settle, and must not be read as settling

**A confirmed mark is not a tradeable market.** Identity is all this measures. Whether the book
can fill an order is `scripts/probe_book_depth.py`; whether the market is alive at all is
`oracle/liveness.py`, derived from the funding log. `xyz:DXY` would MATCH here and has zero
open interest.

**Collateral differs by builder and the map does not carry it.** `execution/broker.py` assumes
USDC backs every perp it trades, which is true of Hyperliquid's core book and the `xyz`
builder. A market added from a builder quoted in something else would be sized against a
balance that is not backing it, so check `COLLATERAL_TOKEN` before mapping a new dex.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from core.canon import load_registry
from core.identity import DIFFERS, IN_RANGE, MATCH, NO_PRICE, compare
from oracle import cache, corpus, listings, venue_map
from oracle.marks import (
    Mark,
    aster_marks,
    hyperliquid_marks,
    index_marks,
    lighter_marks,
)
from oracle.route import OracleRef, load_routing_table, route
from oracle.sources import aster, lighter

CONFIG_DIR = Path(__file__).resolve().parents[1] / "cfg"

# Beyond this the cached close is not evidence of anything. Equities do not price on weekends,
# so a Friday close read on Monday is fine; a month-old one is a stale fetch, and calling a
# market a mismatch off it would blame the venue for our own staleness.
MAX_CLOSE_AGE_DAYS = 10

# How many recent sessions the mark may match instead of the latest close. A venue's oracle
# can sit a session behind, and on a volatile name that reads as a collision: `BE` marked 187.4
# on all three venues against a 166.8 close, which is its *previous* session to within 0.4%.
# The window is a second basis, never a looser tolerance — it reports as its own verdict so a
# reader always knows which number confirmed the pair.
RANGE_SESSIONS = 5

@dataclass(frozen=True, slots=True)
class Check:
    asset: str
    venue: str
    symbol: str
    mark: float
    close: float | None
    close_date: date | None
    mapped: bool        # already in cfg/venue_map.yaml under this venue
    low: float = 0.0    # session low/high over the last RANGE_SESSIONS bars
    high: float = 0.0

    @property
    def _comparison(self):
        """``core.identity`` owns the arithmetic; this probe owns the presentation.

        **The raw ratio is deliberately what gets compared, with no ``scale`` applied**, even
        for a pairing the map already declares one for. Applying it would confirm the very
        thing the reader is here to judge: `RUT` carried IWM at a tenth of the index with no
        `scale` recorded, and a scaled comparison would have printed MATCH and hidden it. The
        gates apply `scale`; this reports what is actually on the wire.
        """
        return compare(mark=self.mark, close=self.close, low=self.low, high=self.high)

    @property
    def ratio(self) -> float | None:
        """Venue mark over our close. 1.0 is the same instrument; 10.03 is SPY against SPX."""
        return self._comparison.ratio

    @property
    def verdict(self) -> str:
        return self._comparison.verdict


@dataclass(frozen=True, slots=True)
class Close:
    """Our own price for one asset: the latest bar, plus the band it has traded in lately."""
    close: float
    when: date
    low: float
    high: float


def cached_closes(*, as_of: date) -> dict[str, Close]:
    """Every corpus asset that routes to a source and has a recent enough cached close.

    Routes exactly as `setups` does — curated map first, corpus domain consensus second — so an
    asset this probe cannot confirm is one the queue could not have priced either. Derived
    ratios are skipped: `ETH/BTC` is not an instrument any venue lists.
    """
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    table = load_routing_table(
        CONFIG_DIR,
        [(r.asset, r.domain) for r in rows],
        listings=listings.load_or_fetch(cache.DATA_ROOT / "_listings.json"),
    )
    floor = as_of - timedelta(days=MAX_CLOSE_AGE_DAYS)
    out: dict[str, Close] = {}
    for asset in sorted({r.asset for r in rows}):
        resolved = route(asset, table)
        if not isinstance(resolved, OracleRef):
            continue
        series = cache.load(resolved.source, resolved.symbol)
        if series is None or not series.bars:
            continue
        last = series.bars[-1]
        if last.date >= floor and last.close > 0:
            recent = series.bars[-RANGE_SESSIONS:]
            out[asset] = Close(
                close=last.close,
                when=last.date,
                low=min(b.low for b in recent),
                high=max(b.high for b in recent),
            )
    return out


def check_all(marks, closes, *, mapped_only: bool = False) -> list[Check]:
    """Every (asset, venue) pair worth a human's attention: same-named markets, plus every
    pair the map already claims — those are checked whether or not the names agree, since a
    curated `GOLD -> XAU` is invisible to a name match and is exactly what needs verifying."""
    index = index_marks(marks)
    by_symbol = {(m.venue, m.symbol.upper()): m for m in marks}
    venues = sorted({m.venue for m in marks})

    checks: list[Check] = []
    seen: set[tuple[str, str, str]] = set()

    def build(asset: str, venue: str, symbol: str, mark: float, mapped: bool) -> Check:
        ours = closes.get(asset)
        return Check(
            asset=asset, venue=venue, symbol=symbol, mark=mark,
            close=ours.close if ours else None,
            close_date=ours.when if ours else None,
            mapped=mapped,
            low=ours.low if ours else 0.0,
            high=ours.high if ours else 0.0,
        )

    def add(asset: str, mark: Mark, mapped: bool) -> None:
        key = (asset, mark.venue, mark.symbol)
        if key in seen:
            return
        seen.add(key)
        checks.append(build(asset, mark.venue, mark.symbol, mark.price, mapped))

    def is_mapped(asset: str, venue: str, symbol: str) -> bool:
        """Mapped means *this symbol*, not merely this venue. The looser reading would have
        marked the `SPX` memecoin as curated, since the map does name a Hyperliquid market for
        SPX — a different one."""
        listing = venue_map.listing(asset, venue.split(":", 1)[0])
        return listing is not None and listing.symbol.split(":")[-1].upper() == symbol.upper()

    for asset in sorted(venue_map.load()):
        for venue in venue_map.venues_for(asset):
            listing = venue_map.listing(asset, venue)
            if listing is None:
                continue
            # The map keeps HIP-3 markets namespaced (`xyz:GOLD`); marks are keyed by builder.
            if ":" in listing.symbol:
                dex, symbol = listing.symbol.split(":", 1)
                log_venue = f"{venue}:{dex}"
            else:
                log_venue, symbol = venue, listing.symbol
            mark = by_symbol.get((log_venue, symbol.upper()))
            if mark is not None:
                add(asset, mark, mapped=True)
                continue
            # A mapped symbol no venue returns is the failure mode the map cannot self-report:
            # it reads as curated fact and would refuse at order time. Zero mark, which
            # `core.identity` reads as absence — so this lands under NO PRICE, never MATCH and
            # no longer under DIFFERS. "Nobody answered" is not "answered differently", and the
            # gates have to tell those apart even where a report could blur them.
            checks.append(build(asset, log_venue, symbol, 0.0, True))

    if not mapped_only:
        for asset in sorted(closes):
            for venue in venues:
                for mark in index.get((venue, asset.upper()), ()):
                    add(asset, mark, mapped=is_mapped(asset, venue, mark.symbol))
    return checks


# Which market to write into the map when a venue lists the same asset more than once.
#
# Hyperliquid: the core book first, then the `xyz` builder. Both are USDC-collateralised, which
# is what `execution/broker.py` assumes; the other builders are not verified and are reported
# but never emitted. Aster: the USDT quote, matching every entry already in the file, with the
# USD market as fallback. Lighter carries one market per symbol and needs no rule.
_HL_PREFERENCE = ("hyperliquid", "hyperliquid:xyz")
_ASTER_PREFERENCE = ("USDT", "USD")


def preferred(checks: list[Check]) -> dict[str, dict[str, str]]:
    """asset -> {map venue key: symbol}, one market per venue, MATCH rows only."""
    out: dict[str, dict[str, str]] = {}
    for check in checks:
        if check.verdict != MATCH:
            continue
        if check.venue.startswith("hyperliquid"):
            if check.venue not in _HL_PREFERENCE:
                continue
            key, symbol = "hyperliquid", (
                f"xyz:{check.symbol}" if check.venue.endswith(":xyz") else check.symbol
            )
            current = out.setdefault(check.asset, {}).get(key)
            # Core beats the builder: an unprefixed symbol already sitting there wins.
            if current and ":" not in current:
                continue
            out[check.asset][key] = symbol
        elif check.venue == aster.VENUE:
            current = out.setdefault(check.asset, {}).get("aster")
            if current and _rank(current) <= _rank(check.symbol):
                continue
            out[check.asset]["aster"] = check.symbol
        else:
            out.setdefault(check.asset, {})[check.venue] = check.symbol
    return out


def _rank(symbol: str) -> int:
    for i, quote in enumerate(_ASTER_PREFERENCE):
        if symbol.upper().endswith(quote):
            return i
    return len(_ASTER_PREFERENCE)


def emit_yaml(checks: list[Check]) -> None:
    """A paste-ready block for `cfg/venue_map.yaml`, confirmed rows only.

    Deliberately not written to the file. The map is curated prose as much as data — the
    grouping and the comments explaining each trap are the part that keeps someone from
    re-introducing one — and a generator that overwrote it would strip exactly that.
    """
    for asset, entry in sorted(preferred(checks).items()):
        if venue_map.load().get(asset):
            continue
        body = ", ".join(f"{v}: {s}" for v, s in sorted(entry.items()))
        print(f"  {asset + ':':9}{{{body}}}")


def report(checks: list[Check], *, verdicts: set[str], unmapped_only: bool) -> None:
    shown = [
        c for c in checks
        if c.verdict in verdicts and not (unmapped_only and c.mapped)
    ]
    order = {MATCH: 0, IN_RANGE: 1, DIFFERS: 2, NO_PRICE: 3}
    shown.sort(key=lambda c: (order[c.verdict], c.asset, c.venue))

    print(f"{'asset':10} {'venue':20} {'symbol':14} {'mark':>12} {'our close':>12} "
          f"{'ratio':>9}  {'in map':6} verdict")
    for c in shown:
        ratio = "" if c.ratio is None else f"{c.ratio:9.4f}"
        close = "" if c.close is None else f"{c.close:12.4f}"
        mark = "  not listed" if not c.mark else f"{c.mark:12.4f}"
        print(f"{c.asset:10} {c.venue:20} {c.symbol:14} {mark} {close} "
              f"{ratio:>9}  {'yes' if c.mapped else '-':6} {c.verdict}")

    tally = {v: sum(1 for c in checks if c.verdict == v)
             for v in (MATCH, IN_RANGE, DIFFERS, NO_PRICE)}
    print(f"\n{len(checks)} pairs checked — " + " · ".join(f"{v} {n}" for v, n in tally.items()))

    # The gap that matters is a VENUE the map does not reach for an asset, not a symbol it
    # spells differently. Aster lists most things twice (USDT and USD) and the map takes one on
    # purpose, so counting unmapped symbols would report ~40 gaps that are all the same
    # deliberate choice.
    gaps, builder_only = set(), set()
    for c in checks:
        if c.verdict != MATCH or c.venue.split(":")[0] in venue_map.venues_for(c.asset):
            continue
        (gaps if c.venue in (*_HL_PREFERENCE, lighter.VENUE, aster.VENUE)
         else builder_only).add(f"{c.asset}/{c.venue}")
    print(f"{len(gaps)} confirmed (asset, venue) pairs the map does not reach: "
          f"{' '.join(sorted(gaps)) or 'none'}")
    print(f"{len(builder_only)} more sit only on an unmapped HIP-3 builder "
          f"(collateral unverified — see the map's header): {' '.join(sorted(builder_only))}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verdict", nargs="+", default=[MATCH, IN_RANGE, DIFFERS, NO_PRICE],
                    choices=[MATCH, IN_RANGE, DIFFERS, NO_PRICE], help="verdicts to print")
    ap.add_argument("--unmapped-only", action="store_true",
                    help="hide pairs cfg/venue_map.yaml already carries")
    ap.add_argument("--mapped-only", action="store_true",
                    help="check only what the map claims — a re-validation of the curated file")
    ap.add_argument("--yaml", action="store_true",
                    help="emit a paste-ready block of confirmed entries the map lacks")
    args = ap.parse_args(argv)

    marks = hyperliquid_marks() + lighter_marks() + aster_marks()
    venues = sorted({m.venue for m in marks})
    print(f"marks: {len(marks)} markets across {len(venues)} venues — {', '.join(venues)}")

    closes = cached_closes(as_of=datetime.now(UTC).date())
    print(f"closes: {len(closes)} corpus assets with a cached close under "
          f"{MAX_CLOSE_AGE_DAYS} days old\n")

    checks = check_all(marks, closes, mapped_only=args.mapped_only)
    if args.yaml:
        emit_yaml(checks)
        return 0
    report(checks, verdicts=set(args.verdict), unmapped_only=args.unmapped_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
