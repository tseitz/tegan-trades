"""The equity broker's decidable parts — universe filtering, equity reading, host selection.

Placing an actual order needs a key and a funded account. Everything here runs offline by
injecting a transport, which is the same seam ``oracle``'s source adapters use for ``get_json``.
"""
from __future__ import annotations

import pytest

from execution.alpaca_broker import (
    LIVE,
    NETWORKS,
    PAPER,
    AlpacaBroker,
    AlpacaCredentials,
    account_equity,
    tradable_markets,
)
from execution.plan import PERP_GRID, SHARE_GRID, OrderPlan

CREDS = AlpacaCredentials(key_id="PK123", secret_key="s3cret")

PLAN = OrderPlan(
    asset="MSFT", coin="MSFT", direction="long", size=4.0,
    entry=480.25, stop=455.0, target=560.0,
    risk=101.0, notional=1_921.0, equity=10_000.0, candidate_key="cbb6fa5d4548",
)


class FakeTransport:
    """Records calls and replays canned replies, keyed by (method, path)."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []

    def __call__(self, method, path, body=None, params=None):
        self.calls.append({"method": method, "path": path, "body": body, "params": params})
        return self.replies.get((method, path))


# ── the universe ────────────────────────────────────────────────────────────────────────────

def test_only_tradable_assets_become_markets():
    """An asset can be present, active and still refuse orders — halted, delisted, or not
    carried by this broker. ``check_listing`` compares against this, so a non-tradable symbol
    surviving here would let the venue map authorise an order the venue then rejects."""
    markets = tradable_markets([
        {"symbol": "MSFT", "tradable": True},
        {"symbol": "HALTED", "tradable": False},
        {"symbol": "GOOGL", "tradable": True},
    ])
    assert set(markets) == {"MSFT", "GOOGL"}


def test_equity_markets_carry_the_share_grid():
    """The whole point of the grid field: a perp-rounded price on a $29 stock quotes three
    decimals, which Rule 612 does not permit and Alpaca rejects."""
    market = tradable_markets([{"symbol": "MSFT", "tradable": True}])["MSFT"]
    assert market.grid == SHARE_GRID
    assert market.grid != PERP_GRID
    assert market.sz_decimals == 0


@pytest.mark.parametrize("assets", [None, [], [{"tradable": True}], ["nonsense"]])
def test_a_malformed_asset_list_yields_no_markets_rather_than_raising(assets):
    """An unreadable universe must refuse everything, not crash mid-session — a session that
    dies throws away the judgement already entered."""
    assert tradable_markets(assets) == {}


# ── equity ──────────────────────────────────────────────────────────────────────────────────

def test_equity_reads_the_account_value():
    assert account_equity({"equity": "25000.50", "buying_power": "100000"}) == 25_000.50


def test_equity_is_not_buying_power():
    """On a margin account buying power is a multiple of equity. Sizing against it would apply
    the broker's leverage on top of ``max_notional_frac``, so 1% risk would not mean here what
    it means on the perp venue."""
    assert account_equity({"equity": "1000", "buying_power": "4000"}) == 1000.0


@pytest.mark.parametrize("account", [None, {}, {"equity": None}, {"equity": "abc"}, "nope"])
def test_an_unreadable_account_reports_zero(account):
    """Zero is a refusal the guards act on. Guessing a balance would size a real order."""
    assert account_equity(account) == 0.0


def test_a_negative_equity_floors_to_zero():
    assert account_equity({"equity": "-50"}) == 0.0


# ── hosts and headers ───────────────────────────────────────────────────────────────────────

def test_paper_and_live_are_different_hosts():
    """No field in the request says which you meant, so the URL is the safety boundary."""
    assert NETWORKS[PAPER] != NETWORKS[LIVE]
    assert "paper" in NETWORKS[PAPER]


def test_the_default_network_is_paper():
    """Live is never reached by omission."""
    assert AlpacaBroker(CREDS, transport=FakeTransport()).network == PAPER


def test_an_unknown_network_is_refused():
    with pytest.raises(ValueError, match="unknown network"):
        AlpacaBroker(CREDS, network="mainnet", transport=FakeTransport())


def test_credentials_travel_as_headers_not_in_the_body():
    broker = AlpacaBroker(CREDS, transport=FakeTransport())
    assert broker.headers["APCA-API-KEY-ID"] == "PK123"
    assert broker.headers["APCA-API-SECRET-KEY"] == "s3cret"


def test_is_live_is_false_on_paper():
    assert AlpacaBroker(CREDS, network=PAPER, transport=FakeTransport()).is_live is False
    assert AlpacaBroker(CREDS, network=LIVE, transport=FakeTransport()).is_live is True


# ── the universe is fetched once ────────────────────────────────────────────────────────────

def test_markets_are_fetched_once_per_session():
    """~11,000 assets and several megabytes. Paid at session open, not per candidate."""
    transport = FakeTransport({("GET", "/v2/assets"): [{"symbol": "MSFT", "tradable": True}]})
    broker = AlpacaBroker(CREDS, transport=transport)
    broker.markets()
    broker.markets()
    assert sum(c["path"] == "/v2/assets" for c in transport.calls) == 1


def test_the_universe_request_asks_only_for_active_us_equities():
    transport = FakeTransport({("GET", "/v2/assets"): []})
    AlpacaBroker(CREDS, transport=transport).markets()
    assert transport.calls[0]["params"] == {"status": "active", "asset_class": "us_equity"}


# ── liquidity ───────────────────────────────────────────────────────────────────────────────

def test_liquidity_is_unmeasured_rather_than_fabricated():
    """None means 'not measured' and ``check_liquidity`` refuses on it. An equity has no open
    interest and this API publishes no book, so a fabricated Liquidity would be the gate
    silently passing itself."""
    assert AlpacaBroker(CREDS, transport=FakeTransport()).liquidity("MSFT") is None


# ── placing ─────────────────────────────────────────────────────────────────────────────────

def test_placing_an_unlisted_symbol_never_reaches_the_network():
    transport = FakeTransport({("GET", "/v2/assets"): []})
    placement = AlpacaBroker(CREDS, transport=transport).place(PLAN)
    assert not placement.ok
    assert "not tradable" in (placement.error or "")
    assert not any(c["method"] == "POST" for c in transport.calls)


def test_a_placed_bracket_posts_the_otoco_order():
    transport = FakeTransport({
        ("GET", "/v2/assets"): [{"symbol": "MSFT", "tradable": True}],
        ("POST", "/v2/orders"): {"id": "abc", "status": "new", "legs": []},
    })
    placement = AlpacaBroker(CREDS, transport=transport).place(PLAN)
    assert placement.ok
    posted = next(c for c in transport.calls if c["method"] == "POST")
    assert posted["body"]["order_class"] == "bracket"
    assert posted["body"]["symbol"] == "MSFT"


def test_a_venue_error_body_becomes_a_reported_refusal():
    """A 4xx carries Alpaca's own code and message; raising would throw away the only
    description of what was wrong."""
    transport = FakeTransport({
        ("GET", "/v2/assets"): [{"symbol": "MSFT", "tradable": True}],
        ("POST", "/v2/orders"): {"code": 42210000, "message": "fractional orders"},
    })
    placement = AlpacaBroker(CREDS, transport=transport).place(PLAN)
    assert not placement.ok
    assert "42210000" in (placement.error or "")
