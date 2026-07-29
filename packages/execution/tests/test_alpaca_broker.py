"""The equity broker's decidable parts — universe filtering, equity reading, host selection.

Placing an actual order needs a key and a funded account. Everything here runs offline by
injecting a transport, which is the same seam ``oracle``'s source adapters use for ``get_json``.
"""
from __future__ import annotations

import pytest

from execution.alpaca_broker import (
    DATA_URL,
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


# ── which candidates still have something live ──────────────────────────────────────────────
#
# The duplicate guard exists to stop TWO LIVE BRACKETS on one candidate. It is not a record of
# history: a bracket that was accepted and is now flat — cancelled, expired, or round-tripped
# by a gap — leaves nothing to duplicate, and blocking it burns the setup forever.

def _order(status, legs=()):
    return {"id": "x", "status": status,
            "legs": [{"id": f"l{i}", "status": s} for i, s in enumerate(legs)]}


def _broker_with(orders):
    class T:
        def __call__(self, method, path, body=None, params=None):
            if path == "/v2/orders:by_client_order_id":
                return orders.get(params["client_order_id"], {"code": 404, "message": "not found"})
            return None
    return AlpacaBroker(CREDS, transport=T())


def test_a_resting_bracket_is_live():
    broker = _broker_with({"k1": _order("accepted", ("held", "held"))})
    assert broker.live_keys({"k1"}) == {"k1"}


def test_a_filled_entry_with_armed_exits_is_live():
    """Position open and protected — a second bracket here really would be a duplicate."""
    broker = _broker_with({"k1": _order("filled", ("held", "held"))})
    assert broker.live_keys({"k1"}) == {"k1"}


def test_a_gap_round_trip_is_not_live():
    """THE BUG THIS FIXES. Entry filled through its own stop, the stop exited at once, the
    take-profit was cancelled by the OCO. The bracket was `ok` so the log says PLACED — but
    the position is flat and the setup should be offered again if price returns to the zone."""
    broker = _broker_with({"k1": _order("filled", ("canceled", "filled"))})
    assert broker.live_keys({"k1"}) == set()


def test_a_cancelled_bracket_is_not_live():
    broker = _broker_with({"k1": _order("canceled", ("canceled", "canceled"))})
    assert broker.live_keys({"k1"}) == set()


@pytest.mark.parametrize("status", ["expired", "rejected", "done_for_day"])
def test_other_terminal_states_are_not_live(status):
    assert _broker_with({"k1": _order(status)}).live_keys({"k1"}) == set()


def test_an_unreadable_or_missing_order_stays_blocked():
    """Fail CLOSED, and this is the one place in the package that direction is right. Not
    knowing must not release the guard — double-placing a live bracket is worse than missing
    one re-entry, and the log still says an order went out."""
    assert _broker_with({}).live_keys({"k1"}) == {"k1"}


def test_an_unrecognised_status_stays_blocked():
    assert _broker_with({"k1": _order("something_new")}).live_keys({"k1"}) == {"k1"}


def test_only_the_keys_asked_about_are_returned():
    broker = _broker_with({"k1": _order("canceled"), "k2": _order("accepted")})
    assert broker.live_keys({"k1", "k2"}) == {"k2"}


def test_no_keys_means_no_requests():
    calls = []
    class T:
        def __call__(self, method, path, body=None, params=None):
            calls.append(path); return None
    assert AlpacaBroker(CREDS, transport=T()).live_keys(set()) == set()
    assert calls == []


# ── depth: the participation cap's input ────────────────────────────────────────────────────

def test_depth_reads_the_data_host_not_the_trading_host():
    """Market data is a third host, paired with neither network. Sending this to paper-api
    returns a 404 that ``depth_from_bars`` would read as an unmeasurable market."""
    calls = []

    def transport(method, path, body=None, params=None, base=None):
        calls.append((path, base, params))
        return {"bars": {"INTL": [{"v": 100, "n": 5, "vw": 30.0}]}}

    broker = AlpacaBroker(CREDS, network=PAPER, transport=transport)
    broker.depth("INTL")

    path, base, params = calls[0]
    assert base == DATA_URL
    assert path == "/v2/stocks/bars"
    assert params["symbols"] == "INTL" and params["timeframe"] == "1Day"


def test_depth_is_the_same_on_paper_and_live():
    """There is no paper price. Both networks read one feed, which is what makes a
    participation cap measured in rehearsal a real statement about the real market."""
    def transport(method, path, body=None, params=None, base=None):
        return {"bars": {"INTL": [{"v": 24_707, "n": 175, "vw": 29.8}]}}

    paper = AlpacaBroker(CREDS, network=PAPER, transport=transport).depth("INTL")
    live = AlpacaBroker(CREDS, network=LIVE, transport=transport).depth("INTL")
    assert paper == live


def test_an_unreadable_feed_is_not_measured_rather_than_dead():
    """It must degrade to "no cap applied". A data outage that refused every equity would be
    the same mistake that forced ``liquidity_enforced`` off for this venue."""
    def transport(*a, **kw):
        raise ConnectionError("data host unreachable")

    assert AlpacaBroker(CREDS, network=PAPER, transport=transport).depth("INTL") is None


def test_depth_is_fetched_once_per_coin_per_session():
    calls = []

    def transport(method, path, body=None, params=None, base=None):
        calls.append(path)
        return {"bars": {"INTL": [{"v": 100, "n": 5, "vw": 30.0}]}}

    broker = AlpacaBroker(CREDS, network=PAPER, transport=transport)
    broker.depth("INTL")
    broker.depth("INTL")
    assert len(calls) == 1


# ── the account, the book, and cancelling ───────────────────────────────────────────────────

# Trimmed from the live paper reply on 2026-07-29. The arithmetic in it is the evidence that
# one field answers "what have I already spoken for" — see ``execution.account``.
ACCOUNT = {
    "equity": "99674.47", "buying_power": "24971.52", "initial_margin": "74702.95",
    "multiplier": "1", "shorting_enabled": False,
}


def test_the_account_read_carries_more_than_equity():
    """The same ``/v2/account`` call, parsed for everything it holds. Reading only ``equity``
    is what let eight orders be sized against a balance three of them could not have."""
    transport = FakeTransport({("GET", "/v2/account"): ACCOUNT})
    account = AlpacaBroker(CREDS, transport=transport).account()
    assert account.buying_power == 24_971.52
    assert account.committed == 74_702.95
    assert account.can_short is False


def test_an_unreadable_account_disables_the_budget_rather_than_emptying_it():
    transport = FakeTransport({("GET", "/v2/account"): {"message": "forbidden"}})
    assert AlpacaBroker(CREDS, transport=transport).account() is None


def test_headroom_is_not_cached_between_reads():
    """The opposite of ``markets`` and ``depth``. Headroom that did not move between
    candidates would not be headroom."""
    transport = FakeTransport({("GET", "/v2/account"): ACCOUNT})
    broker = AlpacaBroker(CREDS, transport=transport)
    broker.account()
    broker.account()
    assert len(transport.calls) == 2


def test_resting_asks_only_for_open_orders():
    transport = FakeTransport({("GET", "/v2/orders"): []})
    AlpacaBroker(CREDS, transport=transport).resting()
    assert transport.calls[0]["params"]["status"] == "open"
    assert transport.calls[0]["params"]["nested"] == "true"


def test_resting_distinguishes_none_from_empty():
    """"Cannot be asked" and "nothing is resting" are opposite facts: the first must not let a
    caller conclude the account is free."""
    assert AlpacaBroker(CREDS, transport=FakeTransport(
        {("GET", "/v2/orders"): []})).resting() == ()
    assert AlpacaBroker(CREDS, transport=FakeTransport(
        {("GET", "/v2/orders"): {"message": "rate limited"}})).resting() is None


def test_positions_join_their_age_from_the_fill_that_opened_them():
    """Two calls, because Alpaca's position objects carry no timestamp of their own."""
    seen = []

    def transport(method, path, body=None, params=None, base=None):
        seen.append((path, (params or {}).get("status")))
        if path == "/v2/positions":
            return [{"symbol": "INTL", "qty": "1639", "side": "long",
                     "market_value": "48219.38", "unrealized_pl": "-329.82"}]
        return [{"symbol": "INTL", "status": "filled", "position_intent": "buy_to_open",
                 "filled_at": "2026-07-29T13:34:57Z"}]

    (position,) = AlpacaBroker(CREDS, transport=transport).positions()
    assert position.opened_at is not None
    assert ("/v2/orders", "closed") in seen


def test_a_failed_history_read_costs_the_age_not_the_listing():
    def transport(method, path, body=None, params=None, base=None):
        if path == "/v2/positions":
            return [{"symbol": "INTL", "qty": "1", "side": "long", "market_value": "29.42"}]
        return {"message": "rate limited"}

    (position,) = AlpacaBroker(CREDS, transport=transport).positions()
    assert position.symbol == "INTL"
    assert position.opened_at is None


def test_cancel_reports_success_as_nothing_to_say():
    """A 204 decodes to no body at all, so anything falsy is success."""
    transport = FakeTransport({("DELETE", "/v2/orders/abc"): None})
    assert AlpacaBroker(CREDS, transport=transport).cancel("abc") is None
    assert transport.calls[0]["method"] == "DELETE"


def test_cancel_reports_the_venues_reason_when_it_refuses():
    transport = FakeTransport({
        ("DELETE", "/v2/orders/abc"): {"code": 42210000, "message": "order is not cancelable"},
    })
    reason = AlpacaBroker(CREDS, transport=transport).cancel("abc")
    assert "not cancelable" in reason


def test_an_empty_error_body_is_not_read_as_a_successful_cancel():
    """A 204 and a bodiless 404 both decode to an empty message. Reporting a missing order as
    cancelled would leave the reader believing the budget was freed."""
    transport = FakeTransport({("DELETE", "/v2/orders/gone"): {"code": 404, "message": ""}})
    assert AlpacaBroker(CREDS, transport=transport).cancel("gone") is not None


def test_a_bodiless_204_is_a_successful_cancel():
    transport = FakeTransport({("DELETE", "/v2/orders/ok"): {"code": 204, "message": ""}})
    assert AlpacaBroker(CREDS, transport=transport).cancel("ok") is None
