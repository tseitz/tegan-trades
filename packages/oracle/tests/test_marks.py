"""Reading each venue's live mark out of its own idea of a payload.

These parsers moved out of ``scripts/probe_venue_coverage.py`` when the identity check stopped
being a probe and became a gate, and they were untested for as long as they were a script. Each
venue's shape is pinned here against a payload in its real form, because a parser that silently
returns nothing turns the gate off rather than tripping it.
"""
from __future__ import annotations

from oracle import marks
from oracle.sources import aster, hyperliquid, lighter


# ── hyperliquid: core book plus HIP-3 builders ──────────────────────────────────────────────

def _hl(payloads):
    """A `post_json` that answers `perpDexs` first, then one `metaAndAssetCtxs` per dex."""
    def post_json(url, body):
        return payloads[body["type"]] if body["type"] == "perpDexs" else payloads[body["dex"]]
    return post_json


def test_reads_the_core_book():
    got = marks.hyperliquid_marks(post_json=_hl({
        "perpDexs": [],
        "": [{"universe": [{"name": "BTC"}]}, [{"markPx": "64000.5"}]],
    }))
    assert got == [marks.Mark(hyperliquid.VENUE, "BTC", 64000.5)]


def test_a_builder_market_is_namespaced_by_venue_and_bare_by_symbol():
    """The map keeps HIP-3 markets as `xyz:GOLD` while the venue names them `GOLD`. Carrying
    the builder in the venue rather than the symbol is what lets a mark join to a map entry."""
    got = marks.hyperliquid_marks(post_json=_hl({
        "perpDexs": [{"name": "xyz"}],
        "": [{"universe": []}, []],
        "xyz": [{"universe": [{"name": "xyz:GOLD"}]}, [{"markPx": "2650.0"}]],
    }))
    assert got == [marks.Mark(f"{hyperliquid.VENUE}:xyz", "GOLD", 2650.0)]


def test_a_delisted_market_is_not_a_mark():
    got = marks.hyperliquid_marks(post_json=_hl({
        "perpDexs": [],
        "": [{"universe": [{"name": "OLD", "isDelisted": True}]}, [{"markPx": "1.0"}]],
    }))
    assert got == []


def test_a_short_context_list_truncates_rather_than_raising():
    """Universe and contexts are zipped positionally. A partial reading beats none — the same
    rule the funding source follows against the same endpoint."""
    got = marks.hyperliquid_marks(post_json=_hl({
        "perpDexs": [],
        "": [{"universe": [{"name": "BTC"}, {"name": "ETH"}]}, [{"markPx": "64000.5"}]],
    }))
    assert [m.symbol for m in got] == ["BTC"]


# ── lighter and aster ───────────────────────────────────────────────────────────────────────

def test_lighter_reads_only_active_markets():
    got = marks.lighter_marks(get_json=lambda url: {"order_book_details": [
        {"symbol": "BTC", "mark_price": "64000.5", "status": "active"},
        {"symbol": "DEAD", "mark_price": "1.0", "status": "inactive"},
    ]})
    assert got == [marks.Mark(lighter.VENUE, "BTC", 64000.5)]


def test_aster_reads_the_premium_index():
    got = marks.aster_marks(get_json=lambda url: [{"symbol": "BTCUSDT", "markPrice": "64000.5"}])
    assert got == [marks.Mark(aster.VENUE, "BTCUSDT", 64000.5)]


# ── a price that is not a price ─────────────────────────────────────────────────────────────

def test_an_unparseable_or_zero_price_is_absence_not_zero():
    """Zero would compare as a real number four orders of magnitude from any close, which is
    indistinguishable from a collision. Absence has to stay absent."""
    got = marks.aster_marks(get_json=lambda url: [
        {"symbol": "A", "markPrice": "not-a-number"},
        {"symbol": "B", "markPrice": "0"},
        {"symbol": "C", "markPrice": None},
    ])
    assert got == []


# ── joining a venue's spelling to ours ──────────────────────────────────────────────────────

def test_a_quote_suffix_is_stripped_to_find_the_base_ticker():
    assert marks.base_of("BTCUSDT") == "BTC"
    assert marks.base_of("XAUUSD") == "XAU"


def test_a_symbol_that_is_entirely_a_quote_token_survives():
    """`USDC` reduced to the empty string would join to every asset at once."""
    assert marks.base_of("USDC") == "USDC"
    assert marks.base_of("USD") == "USD"


def test_one_base_ticker_can_hold_several_markets():
    """A venue carries both `EUR` and `EURUSD`, and picking one here would be the guess this
    whole check exists to avoid making."""
    index = marks.index_marks([
        marks.Mark("aster", "EURUSDT", 1.08),
        marks.Mark("aster", "EUR", 1.08),
        marks.Mark("aster", "BTCUSDT", 64000.5),
    ])
    assert {m.symbol for m in index[("aster", "EUR")]} == {"EURUSDT", "EUR"}
    assert [m.symbol for m in index[("aster", "BTC")]] == ["BTCUSDT"]
