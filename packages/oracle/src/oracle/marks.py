"""What each venue says its markets are worth, right now.

The independent half of an identity check. ``core.identity`` decides whether two prices are the
same instrument; this supplies the number that is not ours. Reading it from the venue is the
point — a source that shared our routing would agree with us about ``WTI`` being W&T Offshore
and confirm the collision instead of catching it.

These parsers lived in ``scripts/probe_venue_coverage.py`` while the check was only evidence
for whoever curated the map. They moved here when it became a gate, because a script cannot be
imported by the fetch path or by a test, and three copies of "read Aster's mark" is three
things to keep correct.

**A mark is per (venue, symbol), and the venue string carries the HIP-3 builder** — ``xyz:GOLD``
in ``cfg/venue_map.yaml`` is builder ``xyz``, symbol ``GOLD``. Splitting it this way is what
lets a mark join to a map entry at all, and it keeps the core book distinguishable from a
builder's book of the same name, which is the difference between a market with depth and one
with none.

Free — public endpoints, no key, nothing placed. One request per venue (plus one per Hyperliquid
builder), never one per asset.
"""
from __future__ import annotations

from dataclasses import dataclass

from oracle import http
from oracle.sources import aster, hyperliquid, lighter

# Quote suffixes venues bolt onto a base ticker. Stripped when reading a venue's universe so
# `BTCUSDT` is recognised as a candidate for `BTC` — the candidate still has to clear the mark
# check, which is the whole point.
QUOTE_SUFFIXES = ("USDT", "USDC", "USD")


@dataclass(frozen=True, slots=True)
class Mark:
    venue: str          # "hyperliquid", "hyperliquid:xyz", "lighter", "aster"
    symbol: str         # as the venue names it, dex prefix stripped
    price: float


def _f(raw) -> float | None:
    """A price, or nothing. Zero and unparseable are both *absence*: a zero compared against
    any close is four orders of magnitude away, which reads as a collision rather than as the
    missing datum it is."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def hyperliquid_marks(*, post_json=http.post_json) -> list[Mark]:
    """Core book plus every HIP-3 builder. `metaAndAssetCtxs` positionally zips universe to
    contexts exactly as the funding source does, and the same length-mismatch rule applies:
    truncate rather than raise, since a partial reading beats none."""
    marks: list[Mark] = []
    dexs = ["", *hyperliquid.parse_dexs(post_json(hyperliquid.BASE, {"type": "perpDexs"}))]
    for dex in dexs:
        payload = post_json(hyperliquid.BASE, {"type": "metaAndAssetCtxs", "dex": dex})
        if not payload or len(payload) < 2:
            continue
        universe = (payload[0] or {}).get("universe") or []
        venue = f"{hyperliquid.VENUE}:{dex}" if dex else hyperliquid.VENUE
        for asset, ctx in zip(universe, payload[1] or []):
            name = (asset or {}).get("name")
            if not name or (asset or {}).get("isDelisted"):
                continue
            price = _f((ctx or {}).get("markPx"))
            if price is None:
                continue
            marks.append(Mark(venue, name.split(":", 1)[-1], price))
    return marks


def lighter_marks(*, get_json=http.get_json) -> list[Mark]:
    payload = get_json(f"{lighter.BASE}/orderBookDetails")
    rows = payload.get("order_book_details") if isinstance(payload, dict) else payload
    out = []
    for row in rows or ():
        symbol, price = (row or {}).get("symbol"), _f((row or {}).get("mark_price"))
        if symbol and price is not None and (row or {}).get("status") == "active":
            out.append(Mark(lighter.VENUE, symbol, price))
    return out


def aster_marks(*, get_json=http.get_json) -> list[Mark]:
    out = []
    for row in get_json(f"{aster.BASE}/premiumIndex") or ():
        symbol, price = (row or {}).get("symbol"), _f((row or {}).get("markPrice"))
        if symbol and price is not None:
            out.append(Mark(aster.VENUE, symbol, price))
    return out


def base_of(symbol: str) -> str:
    """`BTCUSDT` -> `BTC`. Only strips a suffix that leaves something behind, so `USDC` and
    the handful of markets whose whole name is a quote token survive intact."""
    upper = symbol.upper()
    for suffix in QUOTE_SUFFIXES:
        if upper.endswith(suffix) and len(upper) > len(suffix):
            return upper[: -len(suffix)]
    return upper


def index_marks(marks) -> dict[tuple[str, str], list[Mark]]:
    """(venue, base ticker) -> the markets whose name reduces to it. A list because a venue can
    carry both `EUR` and `EURUSD`, and picking one for the reader would be the guess."""
    index: dict[tuple[str, str], list[Mark]] = {}
    for mark in marks:
        index.setdefault((mark.venue, base_of(mark.symbol)), []).append(mark)
    return index
