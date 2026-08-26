"""The impure edge for US equities. The counterpart to ``broker``, same Protocol.

``HyperliquidBroker`` signs an EIP-712 action with a key that is itself the identity. Alpaca
is an ordinary broker-dealer: identity is an API key pair in a header, and the account behind
it is a real brokerage account at Kraken-style KYC, not a wallet. That difference is the whole
reason this is a separate class rather than a branch inside the other one.

Four venue facts that shape it, each of which is silent when wrong:

* **Paper and live are different hosts, not a flag.** There is no field in the request saying
  which one you meant, so the URL *is* the safety boundary — and live is never the default.
* **The universe is ~11,000 assets and it is the only authority on what can be traded.** An
  asset can be listed and still be non-tradable (halted, delisted, or not held at this broker),
  so ``tradable`` is filtered on rather than assumed from presence.
* **Equities have no open interest and no perp-style book snapshot**, so the liquidity gate
  that exists for HIP-3 markets does not transfer — see ``liquidity``.
* **One account, one pool.** There is no per-namespace collateral, so ``dex`` is accepted for
  Protocol compatibility and ignored.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from execution.account import Account, parse_account
from execution.alpaca_wire import bracket_order, parse_order
from execution.book import (
    OrderState,
    Position,
    RestingOrder,
    filled_at_by_symbol,
    parse_positions,  # and it answers a different question (the network, not an order)
    parse_resting,
    parse_state,
)
from execution.book import (
    is_live as order_is_live,  # aliased: this class has an ``is_live`` of its own,
)
from execution.liquidity import Liquidity
from execution.outcome import ExitFill, parse_fills
from execution.participation import Depth, depth_from_bars
from execution.plan import SHARE_GRID, Market, OrderPlan
from execution.wire import Placement

PAPER = "paper"
LIVE = "live"

# The URL is the only thing distinguishing a rehearsal from real money, so the two are named
# constants rather than a string built from a boolean.
NETWORKS = {
    PAPER: "https://paper-api.alpaca.markets",
    LIVE: "https://api.alpaca.markets",
}

# Market data is a THIRD host, and it is not paired with either network above. Paper and live
# read the identical feed — there is no paper market data, because there is no such thing as a
# paper price. This is the fact that makes ``participation``'s measurement trustworthy in
# rehearsal, and it is the reason the "rehearsal books are mock" warning inherited from
# Hyperliquid testnet is false here: only the *fill* is simulated on paper, never the data.
DATA_URL = "https://data.alpaca.markets"

# Calendar days of bars to ask for. Over-asked on purpose: ~45 calendar days is ~31 trading
# sessions after weekends and holidays, and asking for 30 calendar days would return ~21.
# One month of *trading* is long enough that INTL's 28x volume range (7,966 to 222,872 shares,
# 2026-06-15..07-28) does not decide the answer, and short enough to describe the market as it
# trades now.
DEPTH_LOOKBACK_DAYS = 45

# Alpaca's own maximum for the activities feed. A page shorter than this is the ONLY end-of-feed
# marker the endpoint gives, so the number has to match the request exactly — ask for less and
# every full page looks like the last one.
ACTIVITY_PAGE_SIZE = 100

# A runaway backstop, not an expected bound. ``page_token`` advances to a strictly newer id each
# time, so the loop ends on a short page; this only caps the damage if the venue ever repeats an
# id. 100 pages is 10,000 fills, far past any night this account will have.
MAX_ACTIVITY_PAGES = 100

KEY_HEADER = "APCA-API-KEY-ID"
SECRET_HEADER = "APCA-API-SECRET-KEY"

TIMEOUT = 20

# Equities trade in whole shares under a bracket, which is the same statement as "the lot has
# zero decimal places". Kept as a named constant because ``Market.sz_decimals`` is a perp
# concept and a bare 0 here would read as a placeholder rather than a rule.
SHARE_DECIMALS = 0



@dataclass(frozen=True)
class AlpacaCredentials:
    """An API key pair. Unlike a wallet key this cannot move money out of the account —
    withdrawals are ACH to a bank on file and are not reachable from the trading API."""
    key_id: str
    secret_key: str


def tradable_markets(assets) -> dict[str, Market]:
    """The subset of the asset list an order can actually go to. Pure, so it is tested.

    ``tradable`` is checked rather than inferred: an asset can be present, active and still
    refuse orders — halted, delisted, or simply not carried by this broker. Filtering here
    means ``check_listing`` compares against what is genuinely reachable, which is the whole
    reason it takes the live universe rather than trusting ``cfg/venue_map.yaml``.
    """
    markets: dict[str, Market] = {}
    for asset in assets or []:
        if not isinstance(asset, dict) or not asset.get("tradable"):
            continue
        symbol = asset.get("symbol")
        if not symbol:
            continue
        markets[str(symbol)] = Market(
            coin=str(symbol), sz_decimals=SHARE_DECIMALS, grid=SHARE_GRID,
        )
    return markets


def account_equity(account) -> float:
    """Equity backing new positions, in USD. Pure.

    Reads ``equity`` — the account's total value — rather than ``buying_power``, which on a
    margin account is a multiple of it. Sizing against buying power would silently apply the
    broker's leverage on top of this repo's own ``max_notional_frac``, so the risk budget
    would mean something different here than it does on the perp venue.

    Returns 0.0 for anything unreadable, which the guards turn into a refusal rather than an
    order sized against a number nobody checked.
    """
    if not isinstance(account, dict):
        return 0.0
    try:
        return max(0.0, float(account.get("equity") or 0.0))
    except (TypeError, ValueError):
        return 0.0


class AlpacaBroker:
    """A live connection. Constructing one does network I/O; it is never built speculatively."""

    def __init__(self, credentials: AlpacaCredentials, *, network: str = PAPER,
                 transport: Callable[..., Any] | None = None):
        if network not in NETWORKS:
            raise ValueError(f"unknown network {network!r}; expected one of {sorted(NETWORKS)}")
        self.network = network
        self.base_url = NETWORKS[network]
        self._credentials = credentials
        self._transport = transport or self._request
        self._markets: dict[str, Market] | None = None
        # Cached per coin per session, including the ``None`` of a failed read: a market that
        # would not answer once is not worth asking about again mid-triage, and re-asking
        # would make one candidate's cap depend on how many times it was looked at.
        self._depth: dict[str, Depth | None] = {}

    @property
    def is_live(self) -> bool:
        return self.network == LIVE

    @property
    def headers(self) -> dict[str, str]:
        return {
            KEY_HEADER: self._credentials.key_id,
            SECRET_HEADER: self._credentials.secret_key,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: dict | None = None,
                 params: dict | None = None, base: str | None = None) -> Any:
        """The one place this package touches Alpaca's network.

        Returns the decoded body on any status. A 4xx carries Alpaca's own ``code`` and
        ``message``, which ``parse_order`` turns into a reportable refusal — raising here
        would throw away the only description of what was wrong.

        ``base`` overrides the host for market data, which lives on ``DATA_URL`` rather than
        on the trading host. Passed explicitly rather than inferred from the path so that the
        one boundary that separates paper from live stays a single, readable assignment.
        """
        response = requests.request(
            method, f"{base or self.base_url}{path}",
            headers=self.headers, json=body, params=params, timeout=TIMEOUT,
        )
        try:
            return response.json()
        except ValueError:
            return {"code": response.status_code, "message": response.text[:400]}

    def markets(self) -> dict[str, Market]:
        """Every US equity this account can place an order on, fetched once per session.

        One call. Measured against paper on 2026-07-28: **13,297 tradable assets in 0.7s** —
        cheap enough that the "fetch it once" here is tidiness rather than necessity. All 64
        ``alpaca`` rows in ``cfg/venue_map.yaml`` were present, which is the independent
        confirmation that map's header asks for and could not get from a price comparison.

        Deliberately not cached to disk: a stale universe would claim a halted symbol is
        tradable, and this is the check standing between the venue map and a real order.
        """
        if self._markets is None:
            assets = self._transport(
                "GET", "/v2/assets",
                params={"status": "active", "asset_class": "us_equity"},
            )
            self._markets = tradable_markets(assets if isinstance(assets, list) else [])
        return self._markets

    def equity(self, dex: str = "") -> float:
        """Account equity in USD. ``dex`` is ignored — there is one pool.

        Read live for the same reason the perp broker reads it live: it is the one input that
        changes without anyone editing anything, and a stale figure rescales every position.
        """
        return account_equity(self._transport("GET", "/v2/account"))

    def account(self) -> Account | None:
        """Buying power, what is already committed, and whether this account can short.

        The same ``/v2/account`` call ``equity`` makes, parsed for everything it carries
        instead of one field. Kept as a separate request rather than folded into ``equity``
        because the two have opposite caching requirements: equity is read once per session so
        that two approvals in a sitting are sized against the same balance, while headroom has
        to move between candidates or it is not headroom. See ``session.read_account``.
        """
        return parse_account(self._transport("GET", "/v2/account"))

    def resting(self, order_ids=None) -> tuple[RestingOrder, ...] | None:
        """Entries still working at the venue, oldest first. ``None`` if unreadable.

        ``order_ids`` is accepted and ignored. This venue carries ``candidate_key`` out as the
        ``client_order_id`` and hands it back on every order, so the join needs no local index
        — and unlike Hyperliquid's it survives losing ``data/``.

        ``nested=true`` rolls a bracket's exits under their parent so they are not returned as
        top-level orders — but ``parse_resting`` filters on ``position_intent`` regardless,
        because an unnested reply must not turn a working stop into something offered for
        cancellation. Two answers to one question, and the safe one does not depend on a query
        parameter being honoured.
        """
        payload = self._transport(
            "GET", "/v2/orders",
            params={"status": "open", "nested": "true", "limit": 500, "direction": "asc"},
        )
        if not isinstance(payload, list):
            return None
        return parse_resting(payload)

    def positions(self) -> tuple[Position, ...] | None:
        """Open positions with their ages, largest first. ``None`` if unreadable.

        Two calls, because Alpaca's position objects carry no timestamp: the positions
        themselves, and the closed-order history that says when each one's entry filled. A
        failed history read costs the age column and not the listing — ``parse_positions``
        takes the map as optional for exactly that.
        """
        payload = self._transport("GET", "/v2/positions")
        if not isinstance(payload, list):
            return None
        history = self._transport(
            "GET", "/v2/orders",
            params={"status": "closed", "limit": 500, "direction": "desc"},
        )
        opened = filled_at_by_symbol(history if isinstance(history, list) else [])
        return parse_positions(payload, opened)

    def cancel(self, order_id: str) -> str | None:
        """Cancel one order. ``None`` on success, otherwise the venue's reason.

        Cancelling a bracket *parent* takes its held exits with it, which is why only parents
        ever reach here — see ``book.parse_resting``.

        A successful cancel is a 204 with **no body at all**, which ``_request`` turns into
        ``{"code": 204, "message": ""}``. So an empty message cannot be read as success on its
        own: a 404 with an empty body would say the same thing, and reporting a missing order
        as cancelled is the one failure mode this method must not have. The status code is
        checked as well, and anything outside 2xx is a failure whether or not it explained
        itself.
        """
        reply = self._transport("DELETE", f"/v2/orders/{order_id}")
        if not isinstance(reply, dict):
            return None
        message = reply.get("message") or reply.get("error") or ""
        code = reply.get("code")
        if not message and (code is None or (isinstance(code, int) and 200 <= code < 300)):
            return None
        return f"{code}: {message or 'no reason given'}" if code is not None else str(message)

    def liquidity(self, coin: str) -> Liquidity | None:
        """Always ``None`` — and that is a statement, not a stub.

        The liquidity gate exists because HIP-3 lets anyone deploy a perp market, so "the
        venue lists it" stopped being evidence that it trades. Equities are the opposite case:
        ``tradable`` on a national exchange already carries that evidence, and two of the
        three things the gate measures do not exist here at all — an equity has no open
        interest, and Alpaca's order-entry API publishes no book.

        Returning None rather than a fabricated ``Liquidity`` keeps that honest: it means
        "not measured", and ``check_liquidity`` refuses on it. So the gate must be OFF for
        this venue (``Config.liquidity_enforced``) — and the real equity check is ``depth``
        below, built off the market-data bars rather than faked into this shape.
        """
        return None

    def depth(self, coin: str) -> Depth | None:
        """Median session over the last ``DEPTH_SESSIONS`` trading days. None if unreadable.

        One call, from ``DATA_URL`` — the same feed paper and live both read, so this measures
        the real market even in rehearsal.

        The lookback is calendar days, deliberately over-asked: 45 calendar days yields ~31
        sessions after weekends and holidays, and asking for exactly 30 would return ~21.
        ``depth_from_bars`` medians over whatever comes back rather than requiring a count,
        so an over-ask costs nothing and an under-ask would quietly narrow the window.

        Failure returns None, which ``check_depth`` treats as "no cap applied" rather than as
        a refusal — a data outage must not stop trading, it must only stop *shrinking*.
        """
        if coin in self._depth:
            return self._depth[coin]

        depth = None
        try:
            start = (datetime.now(UTC) - timedelta(days=DEPTH_LOOKBACK_DAYS)).date().isoformat()
            payload = self._transport(
                "GET", "/v2/stocks/bars",
                params={"symbols": coin, "timeframe": "1Day", "start": start,
                        "feed": "sip", "limit": 10_000},
                base=DATA_URL,
            )
            bars = ((payload or {}).get("bars") or {}).get(coin) if isinstance(payload, dict) else None
            depth = depth_from_bars(bars)
        except Exception:  # noqa: BLE001 - unreadable is "not measured", not a crash
            depth = None

        self._depth[coin] = depth
        return depth

    def states(self, keys, order_ids=None) -> dict[str, OrderState | None]:
        """What became of each candidate's bracket, keyed by ``candidate_key``.

        One request per key, and the only place this package asks the venue that question.
        ``live_keys`` and the reconcile pass both read the result rather than each making their
        own trip, so they cannot come to different conclusions about the same order.

        A ``None`` value means unreadable, or that the venue no longer knows the id. It is
        deliberately not a status — see ``book.is_live`` for what each reader does with it.
        """
        return {
            key: parse_state(
                self._transport("GET", "/v2/orders:by_client_order_id",
                                params={"client_order_id": key}),
                key,
            )
            for key in keys
        }

    def funding_paid(self, symbol: str, *, start: datetime,
                     end: datetime | None = None) -> float | None:
        """``0.0`` — a measurement, not an unknown. Equities have no funding rate.

        The distinction is load-bearing: ``None`` would mark every equity close as having
        unmeasured holding costs and strip `credible` off the whole Alpaca history for a term
        that does not exist here. See ``Broker.funding_paid``.

        NOT COMPLETE FOR SHORTS. A borrowed short does accrue a hard-to-borrow fee, which Alpaca
        reports as its own activity type and this does not read. Every close recorded so far is
        long, so the zero is honest today and will quietly stop being so on the first short.
        """
        return 0.0

    def fills(self, since: str) -> tuple[ExitFill, ...] | None:
        """Every print this account has made since ``since``, oldest first. ``None`` if unreadable.

        The close pass's raw material, and deliberately **one call for the whole account**
        rather than ``states``' one-request-per-key: a fill carries its own ``order_id``, so
        attributing it back to a bracket leg is free once the rows are in hand.

        ``None`` versus ``()`` is the asymmetry this package keeps returning to. An empty tuple
        says "this account has traded nothing", which the close pass reads as "these positions
        are all still open" — a defensible conclusion from a real fact. ``None`` says the venue
        did not answer, and the candidates stay on the work list for tomorrow. Collapsing the
        two would mark open trades closed, or worse, closed trades open forever.

        **Paginated, because Alpaca's page maximum is 100 and a short page is the only end
        marker.** The oldest fills are the ones a close pass wants, and they are last, so an
        unpaginated read loses exactly the wrong end of the feed.
        """
        rows: list[dict] = []
        page_token: str | None = None
        try:
            # ``for/else``: falling out of the loop means the backstop was reached with the feed
            # still going, i.e. a KNOWN-incomplete read. That must not be handed back as
            # complete — see the ``else`` clause.
            for _ in range(MAX_ACTIVITY_PAGES):
                params: dict[str, Any] = {"after": since, "direction": "asc",
                                          "page_size": ACTIVITY_PAGE_SIZE}
                if page_token is not None:
                    params["page_token"] = page_token
                payload = self._transport("GET", "/v2/account/activities/FILL", params=params)
                # A dict here is Alpaca's error envelope — the feed itself is a bare array.
                if not isinstance(payload, list):
                    return None
                rows.extend(r for r in payload if isinstance(r, dict))
                if len(payload) < ACTIVITY_PAGE_SIZE:
                    break
                last_id = payload[-1].get("id") if isinstance(payload[-1], dict) else None
                if not last_id:
                    # A FULL page with no cursor on its last row: there is no way to ask for the
                    # next one, and the page being full is proof there is a next one.
                    return None
                page_token = str(last_id)
            else:
                return None
        except Exception:  # noqa: BLE001 - unreadable is "not measured", not a crash
            # The same guard ``depth`` carries, and for a stronger reason: this runs inside an
            # unattended nightly step, so an exception here would abort the reconcile pass and
            # the listing after it. ``None`` leaves every candidate pending for tomorrow.
            return None
        return parse_fills(rows)

    def live_keys(self, keys, order_ids=None) -> set[str]:
        """Of these candidate keys, which still have something live at the venue.

        The duplicate guard exists to stop **two live brackets on one candidate**, not to
        record that an order was once sent. ``store.placed_keys`` can only answer the second
        question, and the difference is a real lost trade: a bracket that filled through its
        own stop on a gapped open round-trips flat within seconds, yet it was ``ok``, so the
        log marks the candidate placed and the setup is never offered again.

        The rule itself lives in ``book.is_live``, because the reconcile pass needs the same
        statuses read the same way.
        """
        return {key for key, state in self.states(keys).items() if order_is_live(state)}

    def place(self, plan: OrderPlan) -> Placement:
        """Send the bracket as one OTOCO order, and report what came back."""
        market = self.markets().get(plan.coin)
        if market is None:
            return Placement(
                ok=False,
                error=f"{plan.coin} is not tradable on Alpaca {self.network}",
            )
        raw = self._transport("POST", "/v2/orders", bracket_order(plan))
        return parse_order(raw)
