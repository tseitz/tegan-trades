"""CoinGecko — token economics, and the only free source that reads open interest and perps
volume for two protocols with one methodology (`docs/research/protocol-comparison-metrics.md`
§4's sharpest finding: mixing an aggregator figure with a venue figure on the other side turns
a 13x like-for-like gap into a reported 26x one).

Two endpoints, both free and keyless (verified live 2026-09-14/16):

    /coins/markets?vs_currency=usd&ids=…    market cap, FDV, circulating/total supply
    /derivatives/exchanges?per_page=250     open interest + 24h volume, BTC-denominated

**Both derivatives fields are BTC-denominated.** Rather than a second network call, ``bitcoin``
rides along in the same batched ``/coins/markets`` call that already fetches every configured
protocol's token economics, and its ``current_price`` is the conversion rate — one extra id
instead of one extra request.

**Two id namespaces, confirmed live and never to be assumed to predict each other**
(§3): ``coingecko`` is the coin id (`/coins/markets`) and ``coingecko_derivatives`` is the
derivatives-exchange id (`/derivatives/exchanges`) — Aster is coin ``aster-2`` but exchange
``aster``. Both are stored, keyed by whichever id the reading actually came from.

**Rate limits.** The fully-keyless path took a 429 on a cold ``/ping`` and succeeded ~8s later
(§2) — keyless works at comparison-card volumes but is not a guarantee. Register a free
CoinGecko Demo key before this goes anywhere near the nightly.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.altsignal import AltSignalReading

from oracle import http

MARKETS_BASE = "https://api.coingecko.com/api/v3/coins/markets"
DERIVATIVES_BASE = "https://api.coingecko.com/api/v3/derivatives/exchanges"

# Batched into every markets call so its `current_price` converts the BTC-denominated
# derivatives fields without a second request.
BTC_ID = "bitcoin"

# `/derivatives/exchanges` is not paginated by the caller today — 250 covers every exchange
# CoinGecko lists (112 measured 2026-09-14) with headroom.
DERIVATIVES_PAGE_SIZE = 250

SOURCE = "coingecko"

_MARKET_FIELDS = {
    "market_cap": "market_cap",
    "fdv": "fully_diluted_valuation",
    "circulating_supply": "circulating_supply",
    "total_supply": "total_supply",
}


def parse_market(row: dict) -> dict[str, float | None]:
    """The four token-economics fields off one ``/coins/markets`` row, under this module's own
    reading-kind names rather than CoinGecko's field names."""
    return {kind: row.get(field) for kind, field in _MARKET_FIELDS.items()}


def fetch_markets(ids: list[str], *, get_json=http.get_json) -> dict[str, dict]:
    """Raw ``/coins/markets`` rows keyed by id — one call for every id, mirroring the probe's
    `fetch_tokens`. Empty ``ids`` skips the call rather than asking CoinGecko for nothing."""
    if not ids:
        return {}
    payload = get_json(MARKETS_BASE, {"vs_currency": "usd", "ids": ",".join(ids)}) or []
    return {row["id"]: row for row in payload if row.get("id")}


def parse_derivatives_row(row: dict, *, btc_usd: float) -> tuple[float | None, float | None]:
    """``(open interest USD, 24h volume USD)`` from one BTC-denominated derivatives-exchange
    row. CoinGecko serves ``trade_volume_24h_btc`` as a string on some rows, so both fields are
    coerced through ``float`` rather than trusted to already be numeric."""
    oi_btc = row.get("open_interest_btc")
    volume_btc = row.get("trade_volume_24h_btc")
    oi = float(oi_btc) * btc_usd if oi_btc is not None else None
    volume = float(volume_btc) * btc_usd if volume_btc is not None else None
    return oi, volume


def fetch_derivatives(*, get_json=http.get_json) -> dict[str, dict]:
    """Raw ``/derivatives/exchanges`` rows keyed by exchange id — one call for every exchange
    CoinGecko lists."""
    payload = get_json(DERIVATIVES_BASE, {"per_page": DERIVATIVES_PAGE_SIZE}) or []
    return {row["id"]: row for row in payload if row.get("id")}


def fetch(
    entries, *, get_json=http.get_json, observed_at: datetime | None = None
) -> list[AltSignalReading]:
    """One reading per market-economics field per protocol, plus one ``open_interest`` and one
    ``volume_24h`` reading per configured ``coingecko_derivatives`` id.

    **Never pre-summed** — a protocol can name several derivatives-exchange rows (Lighter is
    ``lighter`` + ``robinhood-chain-lighter-futures``), and `compare` is what sums them; summing
    here would throw away which rows were actually seen, the same reasoning
    `oracle.altsignal.defillama.fetch_protocols` uses for DefiLlama's open interest.

    A protocol or exchange id absent from CoinGecko's response yields no reading for that id,
    never an error — a partial sweep across several ids is worth more than none.
    """
    at = observed_at or datetime.now(UTC)
    readings: list[AltSignalReading] = []

    coin_ids = sorted({e.coingecko for e in entries} | {BTC_ID})
    markets = fetch_markets(coin_ids, get_json=get_json)

    for entry in entries:
        row = markets.get(entry.coingecko)
        if row is None:
            continue
        for kind, value in parse_market(row).items():
            if value is not None:
                readings.append(
                    AltSignalReading(
                        source=SOURCE, kind=kind, key=entry.coingecko, value=value, observed_at=at
                    )
                )

    btc_row = markets.get(BTC_ID)
    btc_usd = btc_row.get("current_price") if btc_row else None
    if btc_usd is not None:
        derivatives = fetch_derivatives(get_json=get_json)
        for entry in entries:
            for deriv_id in entry.coingecko_derivatives:
                row = derivatives.get(deriv_id)
                if row is None:
                    continue
                oi, volume = parse_derivatives_row(row, btc_usd=btc_usd)
                if oi is not None:
                    readings.append(
                        AltSignalReading(
                            source=SOURCE, kind="open_interest", key=deriv_id,
                            value=oi, observed_at=at,
                        )
                    )
                if volume is not None:
                    readings.append(
                        AltSignalReading(
                            source=SOURCE, kind="volume_24h", key=deriv_id,
                            value=volume, observed_at=at,
                        )
                    )

    return readings
