"""What a perp venue's token costs per unit of the thing that is hard to fake.

Volume is the number a venue can manufacture for free: you trade with yourself and pay the
fee. Open interest is not — it needs real margin posted and left exposed. So a venue's token
priced against volume and the same token priced against open interest are different
valuations, and where they disagree the volume one is the suspect.

That disagreement is the whole point of this probe. Measured 2026-09-13, Hyperliquid and
Lighter differ by 3.9x on 24h volume and by **18.8x** on open interest. Their market caps
differ by 16.6x. Priced on volume Lighter looks like a 4x discount; priced on open interest it
is slightly *more* expensive than Hyperliquid. Aster, which nobody was looking at, held 2.5x
Lighter's open interest at 0.55x its market-cap-to-OI ratio.

Free — three public venue APIs plus DefiLlama and CoinGecko, no key, nothing placed.

    uv run python scripts/probe_perp_venue_fundamentals.py
    uv run python scripts/probe_perp_venue_fundamentals.py --months 12
    uv run python scripts/probe_perp_venue_fundamentals.py --aster-top 120   # skip the tail

## Turnover is the honesty check

`volume / open_interest` is how many times a day the book recycles its own standing risk.
Hyperliquid ran 0.27x, Aster 0.95x, Lighter 1.28x. Lighter churns ~5x faster than Hyperliquid
across the same 234 markets, which is not proof of wash trading — high-frequency makers churn
fast and legitimately — but it is the signature you would look for, and it is cheapest to
produce exactly where fees are lowest.

Which is here: **every one of Lighter's 234 perp markets publishes `taker_fee` 0.0000.** Not
low, zero. So Lighter's fee revenue is not coming from its own order book; it arrives from
liquidation fees, withdrawals, and the Robinhood Chain instance that DefiLlama tracks as a
separate protocol. Read a Lighter "fee" number with that in mind — it is not a take rate.

## Venue API gotchas, each of which cost a wrong number first

**Aster has no bulk open-interest endpoint.** `/fapi/v1/openInterest` rejects a call without
`symbol`, so a venue total is 589 requests. Eight-way concurrency brings that to ~30s. The
other two venues answer in one call each, which is why only Aster carries a coverage number.

**Aster's symbol list is not ASCII.** It lists markets named `龙虾USDT` and `牛来USDT`. An
un-encoded symbol raises `UnicodeEncodeError` inside urllib before any request goes out, so
the symbol must be percent-encoded. Those markets are also a fair signal about what Aster's
tail is made of.

**~21 of Aster's 589 tickers 400 on the open-interest call** — present in `/ticker/24hr`,
not answerable individually. That is 3.6% of symbols, so the probe reports what share of
volume it actually resolved rather than quietly summing a partial book.

**Open interest is quoted in base units on all three venues.** Multiply by the mark price or
the total is meaningless. Aster needs a second bulk call (`/premiumIndex`) to get those marks,
and that feed carries *more* symbols than `/ticker/24hr` does.

## DefiLlama gotchas

**`dataType=dailyRevenue` does not exist for every protocol.** `aster-perps` serves
`dailyFees` and returns HTTP 400 on `dailyRevenue`, while `hyperliquid` and `lighter` serve
both. Quoting fees where revenue was wanted overstates the share that reaches a token holder,
so the fallback is explicit and the report says which figure it used. The 400 costs ~9s
because `oracle.http` retries it three times before giving up.

**Volume is paywalled and the venues are not.** `/overview/derivatives` and
`/summary/derivatives/*` both answer HTTP 402. That is the reason this probe reads volume and
open interest from each venue directly instead of taking one vendor's word for all three.

**A young adapter is a soft number.** DefiLlama's per-protocol adapters are often
community-written, and a new one gets backfilled and corrected after the fact. `Lighter
Robinhood Perps` had 54 days of history when this was written. The trend across a young series
is worth more than any single total in it.

**One protocol can need two slugs.** Aster's fees are under `aster-perps` and its TVL is under
`aster`; `/tvl/aster-perps` answers HTTP 200 with an empty body, which reads as "no TVL" rather
than as "wrong name". So `Venue` carries the fee slug and the TVL slug separately, and a new
venue needs both confirmed against `/overview/fees` instead of guessed from one.

## What this probe cannot settle

**Liquidations.** The strongest honesty metric of the set — you cannot fake one, it costs real
money — and none of the three venues publishes a clean total. Reading it needs an aggregator
that reconstructs it per venue, which is a source this repo does not yet carry.

**Book depth.** Resting size costs capital to post, so it ranks near open interest for
trustworthiness, but both venues truncate the returned book before a ±0.5% band closes. See
`scripts/probe_book_depth.py`, which measures it per market for order sizing rather than as a
venue total.

**Whether a token's supply schedule swamps all of this.** A buyback yield here is annualised
revenue over market cap; it says nothing about the unlock landing against it.
"""
from __future__ import annotations

import argparse
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

from oracle import http
from oracle.sources import hyperliquid

HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
LIGHTER_BASE = "https://mainnet.zklighter.elliot.ai/api/v1"
ASTER_BASE = "https://fapi.asterdex.com/fapi/v1"
LLAMA_FEES = "https://api.llama.fi/summary/fees"
LLAMA_TVL = "https://api.llama.fi/tvl"
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"

# Aster answers open interest one symbol at a time. Eight is enough to finish 589 symbols in
# ~30s and stayed under the venue's rate limit across repeated runs; raising it trades a few
# seconds for 429s that look like missing markets in the coverage line.
ASTER_WORKERS = 8

# Days per year used to annualise a 30-day figure. Deliberately 365/30 rather than 12, so a
# 30-day window is scaled by what it actually measured.
ANNUALISE_30D = 365.0 / 30.0


@dataclass(frozen=True)
class Venue:
    """One venue and the identifiers it is known by across four data sources.

    ``coingecko_id`` is the trap. CoinGecko's ``aster`` is a different asset and ``astar`` is
    a different chain; the perp DEX token is ``aster-2``. All three ids here were confirmed by
    mark price agreeing across Hyperliquid, Lighter and Aster — never by the name matching.

    ``llama_fees`` and ``llama_tvl`` are separate because for Aster they differ, and the wrong
    one fails silently with an empty 200 rather than a 404.
    """

    key: str
    label: str
    llama_fees: str
    llama_tvl: str
    coingecko_id: str
    token: str


VENUES = (
    Venue("hyperliquid", "Hyperliquid", "hyperliquid", "hyperliquid", "hyperliquid", "HYPE"),
    Venue("lighter", "Lighter", "lighter", "lighter", "lighter", "LIT"),
    Venue("aster", "Aster", "aster-perps", "aster", "aster-2", "ASTER"),
)


@dataclass(frozen=True)
class Book:
    """A venue's live order-book state, read from the venue itself.

    ``oi_coverage`` is the share of 24h volume whose open interest the probe actually
    resolved. It is 1.0 for the venues that answer in one call, and below it for Aster, where
    a few listed symbols refuse the per-symbol request. A partial sum reported as a total is
    the failure this field exists to prevent.
    """

    venue: str
    markets: int
    open_interest: float
    volume_24h: float
    oi_coverage: float
    zero_fee_markets: int | None

    @property
    def turnover(self) -> float | None:
        """Times per day the book recycles its own open interest. High is the wash-trade tell."""
        if not self.open_interest:
            return None
        return self.volume_24h / self.open_interest


@dataclass(frozen=True)
class Economics:
    """Off-venue figures: what the protocol earns, what is parked, what the token costs."""

    venue: str
    fees_30d: float | None
    revenue_30d: float | None
    revenue_is_fees: bool
    tvl: float | None
    market_cap: float | None
    fdv: float | None
    circulating: float | None
    total_supply: float | None
    monthly: dict[str, float]

    @property
    def revenue_annualised(self) -> float | None:
        if self.revenue_30d is None:
            return None
        return self.revenue_30d * ANNUALISE_30D


# ---------------------------------------------------------------------------- venue books


def parse_hyperliquid(payloads: list) -> Book:
    """One ``metaAndAssetCtxs`` reply per element: the core book, then one per HIP-3 builder
    dex. Equities, indices and commodities live only on those dexs, not the core book, so
    summing the core book alone previously undercounted Hyperliquid's total open interest by
    ~30% (measured 2026-09-13: $9.79B core-only vs $13.9B across every dex). Each reply is a
    two-element ``[universe, contexts]`` list, positionally zipped, same as
    ``oracle.sources.hyperliquid.parse_asset_ctxs``."""
    oi = vol = 0.0
    markets = 0
    for payload in payloads:
        if not payload:
            continue
        universe = (payload[0] or {}).get("universe") or []
        ctxs = payload[1] or []
        markets += min(len(universe), len(ctxs))
        for _, ctx in zip(universe, ctxs, strict=False):
            mark = _f(ctx.get("markPx"))
            oi += _f(ctx.get("openInterest")) * mark
            vol += _f(ctx.get("dayNtlVlm"))
    return Book("hyperliquid", markets, oi, vol, 1.0, None)


def parse_lighter(payload) -> Book:
    """``orderBookDetails`` carries open interest, mark, 24h quote volume and the published
    taker fee in one response, so the whole venue is one call."""
    rows = (payload or {}).get("order_book_details") or []
    perps = [r for r in rows if r.get("market_type") == "perp"]
    oi = sum(_f(r.get("open_interest")) * _f(r.get("mark_price")) for r in perps)
    vol = sum(_f(r.get("daily_quote_token_volume")) for r in perps)
    zero_fee = sum(1 for r in perps if _f(r.get("taker_fee")) == 0.0)
    return Book("lighter", len(perps), oi, vol, 1.0, zero_fee)


def fetch_aster(*, top: int | None = None, get_json=http.get_json) -> Book:
    """Aster needs three reads: bulk volume, bulk marks, then one call per symbol for open
    interest. ``top`` limits the per-symbol sweep to the busiest markets; the coverage number
    reports what share of volume that left out."""
    tickers = get_json(f"{ASTER_BASE}/ticker/24hr") or []
    marks = {
        r.get("symbol"): _f(r.get("markPrice"))
        for r in (get_json(f"{ASTER_BASE}/premiumIndex") or [])
    }
    by_volume = sorted(tickers, key=lambda r: -_f(r.get("quoteVolume")))
    volume_all = sum(_f(r.get("quoteVolume")) for r in tickers)
    wanted = by_volume[:top] if top else by_volume

    def one(row) -> tuple[float, float]:
        """(open interest notional, volume) for a symbol, or (0, 0) when it refuses."""
        symbol = row.get("symbol") or ""
        quoted = urllib.parse.quote(symbol)  # `龙虾USDT` raises inside urllib unencoded
        try:
            payload = get_json(f"{ASTER_BASE}/openInterest", {"symbol": quoted})
        except http.FetchError:
            return 0.0, 0.0
        if not payload:
            return 0.0, 0.0
        return _f(payload.get("openInterest")) * marks.get(symbol, 0.0), _f(row.get("quoteVolume"))

    with ThreadPoolExecutor(ASTER_WORKERS) as pool:
        results = list(pool.map(one, wanted))

    oi = sum(r[0] for r in results)
    volume_resolved = sum(r[1] for r in results)
    coverage = volume_resolved / volume_all if volume_all else 0.0
    return Book("aster", len(tickers), oi, volume_all, coverage, None)


def fetch_books(*, aster_top: int | None = None) -> dict[str, Book]:
    hl_payloads = [http.post_json(HYPERLIQUID_INFO, {"type": "metaAndAssetCtxs"})]
    for dex in hyperliquid.parse_dexs(http.post_json(HYPERLIQUID_INFO, {"type": "perpDexs"})):
        hl_payloads.append(
            http.post_json(HYPERLIQUID_INFO, {"type": "metaAndAssetCtxs", "dex": dex})
        )

    books = {
        "hyperliquid": parse_hyperliquid(hl_payloads),
        "lighter": parse_lighter(http.get_json(f"{LIGHTER_BASE}/orderBookDetails")),
    }
    books["aster"] = fetch_aster(top=aster_top)
    return books


# ------------------------------------------------------------------------- off-venue data


def fetch_llama(venue: Venue, *, months: int) -> tuple[float | None, float | None, bool, dict]:
    """Fees, revenue, whether revenue fell back to fees, and a monthly daily-mean series.

    Revenue is tried first and fees are the fallback, because revenue is the figure that can
    reach a token holder. ``aster-perps`` has no revenue adapter and 400s, which surfaces here
    as ``revenue_is_fees=True`` rather than as a silently inflated number.
    """
    fees = _llama_total(venue.llama_fees, "dailyFees")
    revenue = _llama_total(venue.llama_fees, "dailyRevenue")
    is_fees = revenue is None
    if is_fees:
        revenue = fees
    series = _llama_monthly(venue.llama_fees, "dailyFees" if is_fees else "dailyRevenue", months)
    return fees, revenue, is_fees, series


def _llama_total(protocol: str, data_type: str) -> float | None:
    """Only ``total30d`` is read, so both chart params drop -- measured on
    ``/summary/fees/hyperliquid``: 95,167 bytes -> 82,103 with ``excludeTotalDataChart`` alone
    -> 6,627 with the breakdown excluded too. A 14x cut for six floats."""
    try:
        payload = http.get_json(
            f"{LLAMA_FEES}/{protocol}",
            {
                "dataType": data_type,
                "excludeTotalDataChart": "true",
                "excludeTotalDataChartBreakdown": "true",
            },
        )
    except http.FetchError:
        return None
    return _f(payload.get("total30d")) if isinstance(payload, dict) else None


def _llama_monthly(protocol: str, data_type: str, months: int) -> dict[str, float]:
    """Mean revenue per day, by calendar month. A monthly *mean* rather than a sum so a
    part-finished month sits on the same scale as a complete one — the shape of the trend is
    the point, and a 12-day September summed looks like a collapse it isn't."""
    try:
        payload = http.get_json(f"{LLAMA_FEES}/{protocol}", {"dataType": data_type})
    except http.FetchError:
        return {}
    if not isinstance(payload, dict):
        return {}
    buckets: dict[str, list[float]] = defaultdict(list)
    for stamp, value in payload.get("totalDataChart") or []:
        when = datetime.fromtimestamp(int(stamp), UTC)
        buckets[when.strftime("%Y-%m")].append(_f(value))
    ordered = sorted(buckets)[-months:]
    return {month: sum(buckets[month]) / len(buckets[month]) for month in ordered}


def fetch_tvl(venue: Venue) -> float | None:
    try:
        return _f(http.get_json(f"{LLAMA_TVL}/{venue.llama_tvl}"))
    except http.FetchError:
        return None


def fetch_tokens() -> dict[str, dict]:
    """One CoinGecko call for all three tokens, keyed by venue."""
    ids = ",".join(v.coingecko_id for v in VENUES)
    payload = http.get_json(COINGECKO_MARKETS, {"vs_currency": "usd", "ids": ids}) or []
    by_id = {row.get("id"): row for row in payload}
    return {v.key: by_id.get(v.coingecko_id) or {} for v in VENUES}


def fetch_economics(*, months: int) -> dict[str, Economics]:
    tokens = fetch_tokens()
    out: dict[str, Economics] = {}
    for venue in VENUES:
        fees, revenue, is_fees, monthly = fetch_llama(venue, months=months)
        token = tokens.get(venue.key) or {}
        out[venue.key] = Economics(
            venue=venue.key,
            fees_30d=fees,
            revenue_30d=revenue,
            revenue_is_fees=is_fees,
            tvl=fetch_tvl(venue),
            market_cap=_opt(token.get("market_cap")),
            fdv=_opt(token.get("fully_diluted_valuation")),
            circulating=_opt(token.get("circulating_supply")),
            total_supply=_opt(token.get("total_supply")),
            monthly=monthly,
        )
    return out


# ---------------------------------------------------------------------------- derived view


@dataclass(frozen=True)
class View:
    """One venue's book and economics joined, with every published ratio named.

    The ratios live here rather than inline in the report so each one is a readable definition
    with a single obvious numerator. ``mcap_per_oi`` against ``mcap_per_daily_volume`` is the
    comparison the whole probe exists to make: the first is priced against margin someone had
    to post, the second against a number a venue can generate for free.
    """

    venue: Venue
    book: Book
    money: Economics

    @property
    def oi_over_tvl(self) -> float | None:
        """Leverage actually in use on the capital parked. Below 1 means deposits sit idle."""
        return _div(self.book.open_interest, self.money.tvl)

    @property
    def float_share(self) -> float | None:
        return _div(self.money.circulating, self.money.total_supply)

    @property
    def mcap_per_oi(self) -> float | None:
        """Dollars of market cap per dollar of real standing risk. The honest multiple."""
        return _div(self.money.market_cap, self.book.open_interest)

    @property
    def mcap_per_daily_volume(self) -> float | None:
        """The same idea against the fakeable number. Diverging from ``mcap_per_oi`` is the tell."""
        return _div(self.money.market_cap, self.book.volume_24h)

    @property
    def price_to_revenue(self) -> float | None:
        return _div(self.money.market_cap, self.money.revenue_annualised)

    @property
    def fdv_to_revenue(self) -> float | None:
        return _div(self.money.fdv, self.money.revenue_annualised)

    @property
    def buyback_yield(self) -> float | None:
        """Annualised revenue over market cap — the ceiling on a revenue-funded buyback, not
        a promise that any of it is spent that way."""
        return _div(self.money.revenue_annualised, self.money.market_cap)

    @property
    def take_rate(self) -> float | None:
        """Fees kept per dollar traded. Meaningless where the published taker fee is zero —
        see the caveat block, which flags exactly that case."""
        return _div(_div(self.money.fees_30d, 30.0), self.book.volume_24h)


# -------------------------------------------------------------------------------- report


def report(books: dict[str, Book], econ: dict[str, Economics]) -> None:
    views = [View(v, books[v.key], econ[v.key]) for v in VENUES]

    print(f"as of {datetime.now(UTC):%Y-%m-%d %H:%M} UTC — volume and open interest read "
          f"from each venue, fees and TVL from DefiLlama, token from CoinGecko\n")

    _table("THE BOOK — what users actually have at risk", views, (
        ("markets", lambda v: f"{v.book.markets:,}"),
        ("open interest", lambda v: _usd(v.book.open_interest)),
        ("24h volume", lambda v: _usd(v.book.volume_24h)),
        ("turnover /day", lambda v: _x(v.book.turnover)),
        ("TVL", lambda v: _usd(v.money.tvl)),
        ("OI / TVL", lambda v: _x(v.oi_over_tvl)),
    ))

    _table("THE TOKEN", views, (
        ("market cap", lambda v: _usd(v.money.market_cap)),
        ("FDV", lambda v: _usd(v.money.fdv)),
        ("circulating", lambda v: _pct(v.float_share)),
        ("revenue 30d", lambda v: _usd(v.money.revenue_30d)),
        ("revenue /yr", lambda v: _usd(v.money.revenue_annualised)),
    ))

    _table("WHAT YOU PAY PER UNIT — lower is cheaper", views, (
        ("mcap / OI", lambda v: _x(v.mcap_per_oi)),
        ("mcap / rev", lambda v: _x(v.price_to_revenue)),
        ("FDV / rev", lambda v: _x(v.fdv_to_revenue)),
        ("mcap / daily vol", lambda v: _x(v.mcap_per_daily_volume)),
        ("buyback yield", lambda v: _pct(v.buyback_yield)),
        ("fees / volume", lambda v: _pct(v.take_rate, dp=4)),
    ))

    _trend(econ)
    _caveats(views)


def _table(title: str, views: list[View], rows) -> None:
    print(title)
    print(f"  {'':18}" + "".join(f"{v.venue.label:>18}" for v in views))
    for name, render in rows:
        print(f"  {name:18}" + "".join(f"{render(v):>18}" for v in views))
    print()


def _trend(econ: dict[str, Economics]) -> None:
    print("REVENUE TREND — mean per day, by month (a falling multiple's denominator)")
    months = sorted({m for e in econ.values() for m in e.monthly})
    print(f"  {'':10}" + "".join(f"{v.label:>18}" for v in VENUES))
    for month in months:
        cells = "".join(f"{_usd(econ[v.key].monthly.get(month)):>18}" for v in VENUES)
        print(f"  {month:10}{cells}")
    print()


def _caveats(views: list[View]) -> None:
    print("READ THESE BEFORE QUOTING ANYTHING ABOVE")
    for view in views:
        label, book, money = view.venue.label, view.book, view.money
        if book.oi_coverage < 0.999:
            print(f"  {label}: open interest covers {book.oi_coverage:.1%} of 24h volume — "
                  f"the rest is symbols that refuse a per-symbol request or were cut by --aster-top")
        if book.zero_fee_markets:
            print(f"  {label}: {book.zero_fee_markets} of {book.markets} perp markets "
                  f"publish taker_fee 0.0000 — its fee line is not a take rate on its own book")
        if money.revenue_is_fees:
            print(f"  {label}: DefiLlama has no revenue adapter, so 'revenue' above is "
                  f"gross FEES — the holder's share is lower by an unknown amount")
    print("  all: liquidations are absent and are the one honesty metric nobody here publishes")


# ------------------------------------------------------------------------------ formatting


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _opt(value) -> float | None:
    return None if value is None else _f(value)


def _div(top, bottom) -> float | None:
    if top is None or not bottom:
        return None
    return top / bottom


def _usd(value) -> str:
    if value is None:
        return "n/a"
    for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= cut:
            return f"${value / cut:,.2f}{suffix}"
    return f"${value:,.0f}"


def _x(value) -> str:
    return "n/a" if value is None else f"{value:,.2f}x"


def _pct(value, *, dp: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{dp}f}%"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=10,
                    help="months of revenue trend to print (default 10)")
    ap.add_argument("--aster-top", type=int, default=None, metavar="N",
                    help="read open interest for only Aster's N busiest markets; the coverage "
                         "line reports what share of volume that leaves out (default: all ~589, "
                         "which takes ~30s)")
    args = ap.parse_args(argv)

    books = fetch_books(aster_top=args.aster_top)
    econ = fetch_economics(months=args.months)
    report(books, econ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
