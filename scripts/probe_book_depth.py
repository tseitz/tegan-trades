"""How deep is each venue's book on the assets we actually want to trade?

§25 established that *funding* differs by venue and by direction. This asks the prior question:
can the venue fill the order at all. It exists because approving `SILVER LONG` in a live sitting
printed `xyz:SILVER traded $0 in 24h` — Hyperliquid's core book carries no metals, so the
configured venue resolved to a HIP-3 builder with a dead book while Lighter and Aster both list
silver.

Methodology mirrors §25's slippage table so the numbers are comparable: walk the ask side for a
buy of N notional, report the volume-weighted fill against mid. `depth10bp` is resting size
within 10bp of mid, summed both sides — the number that says whether a stop can be held.

**Read the `levels` column before comparing venues.** Hyperliquid's `l2Book` returns ~20 levels
per side regardless of what is asked, so its depth is a floor and its slippage a ceiling; Aster
and Lighter answer at 500. §25 flagged this asymmetry and it is the single easiest way to read
this table wrong.

Free — public endpoints, no key, no order placed. Aster rate-limits by IP with escalating bans,
so this makes one request per (venue, asset) and no retries.

    uv run python scripts/probe_book_depth.py
    uv run python scripts/probe_book_depth.py --assets SILVER GOLD BTC

## What venue comparisons have already established — do not re-derive these

**Split by DIRECTION, not by asset class.** The obvious-looking "crypto on Hyperliquid, equities
on Aster" is *strictly worse than either single venue*. Hyperliquid's funding is positive on
every approved equity and Aster's is zero, and zero funding only helps when you would be
*paying* it: measured across all 11 mapped equities, longs were cheaper on Aster 11/11 and
shorts better on Hyperliquid 11/11. Routing all equities to Aster sends every short to the venue
that pays nothing — and the corpus is 1,082 shorts against 2,524 longs.

**Rank on slippage, never on depth.** These are different quantities and silver separates them:
Lighter carries the *most* resting size within 10bp ($2.03M against Hyperliquid's $1.72M) and
still costs 3–5x more to cross, because its spread is 2.8bp against 0.2bp. The size is there,
it is just not near the touch.

**The ordering is stable; the magnitudes are not.** A second SILVER snapshot minutes later moved
Lighter's slippage from 3.0/4.3/4.7bp to 0.8/1.0/2.2bp. *Which* venue wins survived the
re-measure; *by how much* did not. Quote the ordering, and re-run before sizing anything.

**Do not re-argue the thin-book objection from liquidity alone.** It was tested by walking both
real books, and the 24h volume gap (NVDA $90M vs $82k) predicts the opposite of what the books
actually do at retail size. Hyperliquid carries a fixed ~0.86% drag that Aster's ~10x worse
slippage never catches up to below ~$50k.

**Never quote a testnet liquidity number as a fact about a market.** `xyz:SILVER traded $0 in
24h` above is the *mock* book; measured on mainnet the same instrument is the tightest of the
three. `execute.py` says as much — on the rehearsal venue the liquidity gate is measured but
not enforced.

**Liveness is answered from the funding log, not from config** (`oracle/liveness.py`): a market
is dormant when its venue cohort reported and it did not. Do not add `dormant:` flags to
`cfg/venue_map.yaml` — a curated flag rots toward "looks alive", the same error the liquidity
gate exists to catch.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from oracle import http, venue_map
from oracle.sources import aster, hyperliquid, lighter

# Notional rungs, matching §25's NVDA table so the two can be read side by side.
SIZES = (2_000.0, 10_000.0, 50_000.0)

# Distance from mid within which resting size is counted as "holdable" depth.
DEPTH_BAND = 0.0010


@dataclass(frozen=True, slots=True)
class Book:
    venue: str
    symbol: str
    bids: tuple[tuple[float, float], ...]   # (price, size) descending
    asks: tuple[tuple[float, float], ...]   # (price, size) ascending

    @property
    def mid(self) -> float | None:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0][0] + self.asks[0][0]) / 2

    @property
    def spread_bp(self) -> float | None:
        mid = self.mid
        if mid is None or mid <= 0:
            return None
        return (self.asks[0][0] - self.bids[0][0]) / mid * 10_000

    def depth_within(self, band: float = DEPTH_BAND) -> float | None:
        """Resting notional inside ``band`` of mid, both sides."""
        mid = self.mid
        if mid is None:
            return None
        lo, hi = mid * (1 - band), mid * (1 + band)
        return (sum(p * s for p, s in self.bids if p >= lo)
                + sum(p * s for p, s in self.asks if p <= hi))

    def slippage_bp(self, notional: float) -> float | None:
        """VWAP of a market buy of ``notional``, in bp above mid. None if the book is too thin
        to fill it — which is a finding, not an error, and must not read as 0."""
        mid = self.mid
        if mid is None or mid <= 0:
            return None
        spent = filled = 0.0
        for price, size in self.asks:
            take = min(size, (notional - spent) / price)
            spent += take * price
            filled += take
            if spent >= notional - 1e-9:
                return (spent / filled - mid) / mid * 10_000
        return None


def _levels(raw, *, price_key="px", size_key="sz") -> tuple[tuple[float, float], ...]:
    """Normalise one side of a book to (price, size) pairs.

    Venues disagree on shape: Aster sends positional ``[price, qty]`` arrays, Hyperliquid sends
    ``{px, sz}`` dicts, Lighter sends whole orders keyed ``price``/``remaining_base_amount``.
    The keys are only consulted for dict rows.
    """
    out = []
    for lvl in raw or ():
        if isinstance(lvl, dict):
            price, size = lvl.get(price_key), lvl.get(size_key)
        else:
            price, size = lvl[0], lvl[1]
        try:
            p, s = float(price), float(size)
        except (TypeError, ValueError):
            continue
        if p > 0 and s > 0:
            out.append((p, s))
    return tuple(out)


def fetch_aster(symbol: str) -> Book:
    d = http.get_json(f"{aster.BASE}/depth", params={"symbol": symbol, "limit": 500})
    return Book("aster", symbol, _levels(d.get("bids")), _levels(d.get("asks")))


_LIGHTER_MARKETS: dict[str, int] | None = None


def _lighter_market_id(symbol: str) -> int | None:
    """Lighter keys its book by numeric market id, so the symbol must be looked up once."""
    global _LIGHTER_MARKETS
    if _LIGHTER_MARKETS is None:
        payload = http.get_json(f"{lighter.BASE}/orderBookDetails")
        rows = payload.get("order_book_details", payload) if isinstance(payload, dict) else payload
        _LIGHTER_MARKETS = {
            str(r.get("symbol", "")).upper(): r.get("market_id")
            for r in (rows or ()) if isinstance(r, dict)
        }
    return _LIGHTER_MARKETS.get(symbol.upper())


def fetch_lighter(symbol: str) -> Book | None:
    """Lighter returns individual resting *orders*, not aggregated price levels, and caps
    ``limit`` at 100 — 500 is rejected outright as ``invalid param``. So its rows carry
    ``remaining_base_amount`` rather than a size, and its 100 orders may collapse into far
    fewer distinct prices. Compare the `lvls` column, not the venues, when reading depth."""
    market_id = _lighter_market_id(symbol)
    if market_id is None:
        return None
    d = http.get_json(f"{lighter.BASE}/orderBookOrders",
                      params={"market_id": market_id, "limit": 100})
    bids = _levels(d.get("bids"), price_key="price", size_key="remaining_base_amount")
    asks = _levels(d.get("asks"), price_key="price", size_key="remaining_base_amount")
    return Book("lighter", symbol, bids, asks)


def fetch_hyperliquid(symbol: str) -> Book:
    """``xyz:SILVER`` is one coin string; the info endpoint takes it whole, dex prefix included."""
    d = http.post_json(hyperliquid.BASE, {"type": "l2Book", "coin": symbol})
    levels = (d or {}).get("levels") or [[], []]
    return Book("hyperliquid", symbol, _levels(levels[0]), _levels(levels[1]))


FETCHERS = {"aster": fetch_aster, "lighter": fetch_lighter, "hyperliquid": fetch_hyperliquid}


def probe(asset: str) -> list[Book]:
    books = []
    for venue in venue_map.venues_for(asset):
        listing = venue_map.listing(asset, venue)
        fetch = FETCHERS.get(venue)
        if listing is None or fetch is None:
            continue
        try:
            book = fetch(listing.symbol)
        except Exception as exc:                      # noqa: BLE001 — a venue outage is data
            print(f"  {venue:12} {listing.symbol:16} ERROR {type(exc).__name__}: {exc}"[:140])
            continue
        if book is None:
            print(f"  {venue:12} {listing.symbol:16} not listed under that symbol")
            continue
        books.append(book)
    return books


def report(asset: str, books: list[Book]) -> None:
    print(f"\n{asset}")
    header = (f"  {'venue':12} {'symbol':16} {'lvls':>5} {'mid':>10} {'spread':>8} "
              f"{'depth±10bp':>11} " + " ".join(f"{'$'+f'{int(s/1000)}k':>8}" for s in SIZES))
    print(header)
    for b in books:
        mid, spread, depth = b.mid, b.spread_bp, b.depth_within()
        if mid is None:
            print(f"  {b.venue:12} {b.symbol:16} {'':>5} {'EMPTY BOOK — no bid or no ask':>40}")
            continue
        slips = []
        for size in SIZES:
            s = b.slippage_bp(size)
            slips.append("  toothin" if s is None else f"{s:7.1f}bp")
        n = min(len(b.bids), len(b.asks))
        print(f"  {b.venue:12} {b.symbol:16} {n:5} {mid:10.4f} {spread:7.1f}bp "
              f"${depth:10,.0f} " + " ".join(slips))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assets", nargs="+",
                    default=["SILVER", "GOLD", "SPX", "BTC", "ETH", "HYPE"],
                    help="canonical assets to probe (default: metals + index + crypto controls)")
    args = ap.parse_args(argv)

    print(f"book depth by venue — buy-side slippage vs mid, {DEPTH_BAND:.2%} depth band")
    print("NOTE: depth caps differ — hyperliquid l2Book ~20 levels/side, lighter 100 orders")
    print("      (500 is rejected), aster 500. Compare the `lvls` column before the depth one;")
    print("      hyperliquid's depth is a floor and its slippage a ceiling (§25). Slippage at")
    print("      these sizes is set by the top of book, so the caps do not explain it.")
    for asset in args.assets:
        report(asset, probe(asset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
