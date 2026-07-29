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

from dataclasses import dataclass
from typing import Any, Callable

import requests

from execution.alpaca_wire import bracket_order, parse_order
from execution.liquidity import Liquidity
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
                 params: dict | None = None) -> Any:
        """The one place this package touches Alpaca's network.

        Returns the decoded body on any status. A 4xx carries Alpaca's own ``code`` and
        ``message``, which ``parse_order`` turns into a reportable refusal — raising here
        would throw away the only description of what was wrong.
        """
        response = requests.request(
            method, f"{self.base_url}{path}",
            headers=self.headers, json=body, params=params, timeout=TIMEOUT,
        )
        try:
            return response.json()
        except ValueError:
            return {"code": response.status_code, "message": response.text[:400]}

    def markets(self) -> dict[str, Market]:
        """Every US equity this account can place an order on, fetched once per session.

        One call returning ~11,000 assets, which is a few megabytes and several seconds. It is
        paid once at session open rather than per candidate, and deliberately not cached to
        disk: a stale universe would claim a halted symbol is tradable, and this is the check
        standing between ``cfg/venue_map.yaml`` and a real order.
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

    def liquidity(self, coin: str) -> Liquidity | None:
        """Always ``None`` — and that is a statement, not a stub.

        The liquidity gate exists because HIP-3 lets anyone deploy a perp market, so "the
        venue lists it" stopped being evidence that it trades. Equities are the opposite case:
        ``tradable`` on a national exchange already carries that evidence, and two of the
        three things the gate measures do not exist here at all — an equity has no open
        interest, and Alpaca's order-entry API publishes no book.

        Returning None rather than a fabricated ``Liquidity`` keeps that honest: it means
        "not measured", and ``check_liquidity`` refuses on it. So the gate must be OFF for
        this venue (``Config.liquidity_enforced``), and building a real equity check off the
        market-data snapshot endpoint is tracked as its own piece of work rather than faked
        here.
        """
        return None

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
