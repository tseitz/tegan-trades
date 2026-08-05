"""The impure edge: the one object in the repo that signs and transmits.

Everything interesting is elsewhere — ``plan`` decides the numbers, ``guards`` decides
whether to send at all, ``wire`` builds the payload and reads the reply. This module holds
only the connection and the key, which is what makes the boundary auditable.

Three venue facts that shape the class, all verified against the SDK rather than assumed:

* **HIP-3 markets must be requested explicitly.** ``Info`` loads only the core book unless
  ``perp_dexs`` names the builders, so ``xyz:GOLD`` would fail to resolve to an asset index
  and the order would raise before it was sent. ``dexs`` is threaded through for this.
* **Which balance is the collateral depends on the account's mode.** Under a unified account
  the *spot* balance backs perps and ``clearinghouseState`` reads 0 however funded the
  account is; under manual mode each dex has its own pool. Detected, never assumed — see
  ``equity``.
* **Mainnet is opt-in twice.** The network is explicit and never defaults to real money.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

# Aliased because ``execution.account.Account`` is this package's own account state and the
# collision would be silent — both have a plausible ``Account.from_key`` reading.
from eth_account import Account as EthAccount
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from hyperliquid.utils.signing import OrderRequest

from execution.account import Account
from execution.book import OrderState, Position, RestingOrder
from execution.book import is_live as order_is_live
from execution.liquidity import Liquidity, parse_book, parse_context
from execution.participation import Depth
from execution.plan import Market, OrderPlan
from execution.wire import GROUPING, Placement, order_requests, parse_placement

TESTNET = "testnet"
MAINNET = "mainnet"

NETWORKS = {
    TESTNET: constants.TESTNET_API_URL,
    MAINNET: constants.MAINNET_API_URL,
}

# Spot assets are indexed from 10000 and builder-deployed perps from 110000, so this window
# is exactly the spot range. This package trades perpetuals only.
_SPOT_RANGE = range(10_000, 110_000)

# Account abstraction mode — how spot and perps balances relate. Reported by the venue at
# ``info type=userAbstraction``, so it is detected rather than configured: an account whose
# mode is assumed wrongly sizes every trade against a balance that isn't backing it.
UNIFIED_ACCOUNT = "unifiedAccount"
PORTFOLIO_MARGIN = "portfolioMargin"

# Modes where the SPOT balance is the collateral behind perps, so
# ``clearinghouseState.marginSummary.accountValue`` reads 0 while the account is fully funded.
# The venue's own docs carry this warning on the perpetuals endpoint. Anything not listed here
# is treated as manual/standard, which reads the perps balance — the conservative default,
# since a mode we do not recognise then reports 0 and refuses to trade rather than sizing
# against money that may not be collateral.
SPOT_COLLATERAL_MODES = frozenset({UNIFIED_ACCOUNT, PORTFOLIO_MARGIN})

# What backs a perp on the dexs this repo routes to. Core and the ``xyz`` builder are both
# USDC-quoted; CASH perps use USDT, which nothing in cfg/venue_map.yaml currently references.
COLLATERAL_TOKEN = "USDC"


def dex_of(coin: str) -> str:
    """The HIP-3 builder a coin belongs to; ``""`` for the core book.

    ``xyz:GOLD`` -> ``xyz``. The namespace is load-bearing rather than cosmetic: it selects
    both which meta to load and which margin pool backs the trade.
    """
    return coin.split(":", 1)[0] if ":" in coin else ""


def perp_markets(coin_to_asset: dict, asset_to_sz_decimals: dict) -> dict[str, Market]:
    """Filter the SDK's resolution tables down to perpetuals. Pure, so it can be tested.

    Spot markets share the same namespace and must be excluded — this package trades perps
    only, and a spot symbol colliding with a perp name would otherwise route an order to the
    wrong instrument entirely.
    """
    return {
        coin: Market(coin=coin, sz_decimals=asset_to_sz_decimals[asset])
        for coin, asset in coin_to_asset.items()
        if asset not in _SPOT_RANGE and asset in asset_to_sz_decimals
    }


def spot_collateral(spot_state, *, token: str = COLLATERAL_TOKEN) -> float:
    """The spot balance of the token that collateralises perps. Pure.

    Under a unified account this — not the perps margin summary — is the money behind a
    position. Returns 0.0 for an account holding none of it, which the guards turn into a
    refusal rather than an order sized against nothing.
    """
    for balance in (spot_state or {}).get("balances") or []:
        if (balance or {}).get("coin") == token:
            return float(balance.get("total") or 0.0)
    return 0.0


def margin_committed(perp_state) -> float:
    """Collateral already spoken for on one dex — isolated margin plus cross maintenance. Pure.

    Subtracted from the spot balance to get what is actually *available*, per the venue's
    documented unified-account formula. Deliberately conservative: maintenance margin
    understates what an open cross position ties up, so erring toward subtracting more keeps
    a second trade from being sized against collateral the first one is relying on.
    """
    state = perp_state or {}
    total = float(state.get("crossMaintenanceMarginUsed") or 0.0)
    for entry in state.get("assetPositions") or []:
        position = (entry or {}).get("position") or {}
        if ((position.get("leverage") or {}).get("type")) == "isolated":
            total += float(position.get("marginUsed") or 0.0)
    return total


class Broker(Protocol):
    """What the triage loop needs. A fake implementing this is how the flow is tested."""

    def markets(self) -> dict[str, Market]: ...

    def equity(self, dex: str = "") -> float: ...

    # What the venue says about the account as a whole — buying power, what is already
    # committed, whether it can short. ``None`` means "this venue reports no such thing",
    # which leaves the budget gate off rather than refusing every order. See ``account``.
    def account(self) -> Account | None: ...

    # Entries still resting at the venue, oldest first. ``None`` means the venue cannot be
    # asked; an empty tuple means it was asked and there are none. See ``book``.
    def resting(self) -> tuple[RestingOrder, ...] | None: ...

    # Positions currently open. Same None/empty distinction as ``resting``.
    def positions(self) -> tuple[Position, ...] | None: ...

    # Cancel one order by its venue id. Returns None on success, or a message saying why not.
    def cancel(self, order_id: str) -> str | None: ...

    def liquidity(self, coin: str) -> Liquidity | None: ...

    # How a typical session in this market looks. ``None`` means "not measured" and never
    # "not tradable" — only the equity broker answers it, because only an equity venue
    # publishes sessions. See ``participation``.
    def depth(self, coin: str) -> Depth | None: ...

    def place(self, plan: OrderPlan) -> Placement: ...

    # What became of each candidate's bracket. A ``None`` value is "cannot be read", which is
    # not a status — see ``book.is_live``. The duplicate guard and the reconcile pass both read
    # this, so neither can reach a different conclusion about the same order.
    def states(self, keys) -> dict[str, OrderState | None]: ...

    def live_keys(self, keys) -> set[str]: ...


@dataclass(frozen=True)
class Credentials:
    """An API/agent wallet. ``account_address`` is the account being traded, which is *not*
    the agent key's own address — the SDK signs with the agent and acts for the account."""
    account_address: str
    secret_key: str


class HyperliquidBroker:
    """A live connection. Constructing one does network I/O; it is never built speculatively."""

    def __init__(self, credentials: Credentials, *, network: str = TESTNET,
                 dexs: tuple[str, ...] = ()):
        if network not in NETWORKS:
            raise ValueError(f"unknown network {network!r}; expected one of {sorted(NETWORKS)}")
        self.network = network
        self.account_address = credentials.account_address
        self._mode: str | None = None
        # One metaAndAssetCtxs reply per dex covers every market on it, so it is fetched
        # once. The l2 book beside it is never cached — a depth snapshot is only true now.
        self._contexts: dict[str, dict] = {}
        # "" is the core book and is always loaded; the rest are HIP-3 builders. Deduplicated
        # while preserving order so a caller can pass the same dex twice harmlessly.
        self.dexs = tuple(dict.fromkeys(("", *dexs)))
        wallet = EthAccount.from_key(credentials.secret_key)
        self._exchange = Exchange(
            wallet,
            base_url=NETWORKS[network],
            account_address=credentials.account_address,
            perp_dexs=list(self.dexs),
        )
        self._info = self._exchange.info

    @property
    def is_mainnet(self) -> bool:
        return self.network == MAINNET

    def markets(self) -> dict[str, Market]:
        """Every perpetual this connection can actually place an order on.

        Derived from the SDK's own resolution tables rather than from a separate ``meta``
        fetch, so a coin present here is by construction a coin ``bulk_orders`` can turn into
        an asset index. Any other source of truth could disagree with the thing doing the
        sending.
        """
        return perp_markets(self._info.coin_to_asset, self._info.asset_to_sz_decimals)

    @property
    def mode(self) -> str:
        """The account's abstraction mode, fetched once and cached.

        Detected rather than configured because the venue reports it and because getting it
        wrong is silent: a unified account read as standard returns 0 and refuses every
        trade, and a standard account read as unified sizes against a spot balance that is
        not collateral for perps at all.
        """
        if self._mode is None:
            try:
                self._mode = str(self._info.query_user_abstraction_state(self.account_address))
            except Exception:  # noqa: BLE001 - an unreadable mode must not be fatal
                # Falls back to the mode that reads the perps balance. Wrong for a unified
                # account, but wrong in the safe direction: it reports 0 and places nothing.
                self._mode = ""
        return self._mode

    def equity(self, dex: str = "") -> float:
        """Collateral available to back a new position on ``dex``, in USD.

        Read live rather than configured: a stale equity figure silently rescales every
        position, and it is the one input here that changes without anyone editing anything.

        **Which balance that is depends on the account's mode**, and this is not a detail —
        it is the difference between a funded account reporting 0 and refusing to trade.

        * *Unified account / portfolio margin* — the **spot** balance of the collateral token
          is what backs perps, and ``clearinghouseState`` reads 0 no matter how funded the
          account is. Margin already committed across every loaded dex is subtracted, because
          under these modes there is **one pool shared by all dexs**, so ``dex`` does not
          select a balance and every dex returns the same number.
        * *Manual / standard* — perps and spot are separate and each dex has its own pool, so
          ``dex`` does select the balance and the perps margin summary is the right read.
        """
        if self.mode in SPOT_COLLATERAL_MODES:
            available = spot_collateral(self._info.spot_user_state(self.account_address))
            for loaded in self.dexs:
                available -= margin_committed(
                    self._info.user_state(self.account_address, dex=loaded)
                )
            return max(0.0, available)

        state = self._info.user_state(self.account_address, dex=dex) or {}
        summary = state.get("marginSummary") or {}
        return float(summary.get("accountValue") or 0.0)

    def liquidity(self, coin: str) -> Liquidity | None:
        """Volume, open interest and near-touch depth for one market. None if unreadable.

        Two calls: ``metaAndAssetCtxs`` for the dex (cached — one per dex per session, since
        it carries every market) and ``l2Book`` for the coin (never cached, because a depth
        snapshot is only meaningful now).

        Returning None on failure rather than an empty ``Liquidity`` is deliberate: empty
        would read as "measured, and it's dead", and the guard treats the two differently.
        """
        dex = dex_of(coin)
        try:
            if dex not in self._contexts:
                meta, ctxs = self._info.post(
                    "/info", {"type": "metaAndAssetCtxs", "dex": dex}
                )
                names = [a["name"] for a in (meta or {}).get("universe") or []]
                self._contexts[dex] = dict(zip(names, ctxs or []))
            ctx = self._contexts[dex].get(coin)
            if ctx is None:
                return None
            volume, open_interest = parse_context(ctx)
            bid_depth, ask_depth, spread = parse_book(
                (self._info.l2_snapshot(coin) or {}).get("levels")
            )
        except Exception:  # noqa: BLE001 - unreadable is a refusal, not a crash
            return None
        return Liquidity(
            coin=coin, day_volume=volume, open_interest=open_interest,
            bid_depth=bid_depth, ask_depth=ask_depth, spread=spread,
        )

    def depth(self, coin: str) -> Depth | None:
        """Always ``None`` — a perp has no sessions to take a median over.

        Not a gap. This venue already answers the same question better and live, through
        ``liquidity``: open interest and near-touch depth are stronger evidence than a month
        of daily bars, and ``check_liquidity`` already enforces them. ``participation`` exists
        for the venue that has none of that.
        """
        return None

    def account(self) -> Account | None:
        """Always ``None`` — this venue's collateral accounting is already inside ``equity``.

        Not a stub, and not the same question Alpaca answers. Under a unified account
        ``equity`` already subtracts ``margin_committed`` across every loaded dex, so the
        number returned there *is* headroom; under manual mode each dex is its own pool and a
        single account-wide budget would be the wrong shape entirely. Returning None leaves
        the budget gate off here rather than inventing a total that would mean something
        different per mode.

        The two facts the equity read does not cover — ``can_short`` and a broker multiplier —
        do not arise on perps: a short is the sell side of the same contract, and leverage is
        per-market and already bounded by ``max_notional_frac``.
        """
        return None

    def resting(self) -> tuple[RestingOrder, ...] | None:
        """Always ``None`` — not "no orders", but "cannot be asked".

        ``wire.order_requests`` sends no ``cloid``, so an order placed by this repo is known
        to the venue only by an oid nothing here indexes. The same blocker that keeps
        ``live_keys`` conservative, tracked as `docs/IMPROVEMENTS.md` §33.
        """
        return None

    def positions(self) -> tuple[Position, ...] | None:
        """Always ``None``. Readable in principle from ``user_state.assetPositions``; unbuilt
        because ``resting`` above cannot be answered, and half a book listing is worse than
        none — it would show what is open while hiding what is committed."""
        return None

    def cancel(self, order_id: str) -> str | None:
        """Refuses. Nothing here can produce an order id to pass in — see ``resting``."""
        return f"cancelling is not implemented for {self.network}; cancel {order_id} by hand"

    def states(self, keys) -> dict[str, OrderState | None]:
        """Every key unreadable — this venue cannot be asked about a candidate at all.

        ``wire.order_requests`` sends no ``cloid``, so an order this repo placed is known to
        the venue only by an oid nothing here indexes. ``None`` is exactly right for that: not
        a status, but "no answer available", which every reader already handles.
        """
        return dict.fromkeys(keys)

    def live_keys(self, keys) -> set[str]:
        """Every key, unchanged — this venue cannot yet answer the question.

        Deliberately conservative rather than unimplemented. ``AlpacaBroker`` can narrow the
        duplicate guard to candidates that still have something working, because it sends
        ``candidate_key`` as the ``client_order_id`` and can ask the venue about a candidate
        directly. Nothing here does: ``order_requests`` sends no ``cloid``, so the venue knows
        these orders only by an oid this repo would have to keep its own index of.

        So the guard keeps its old meaning here — placed once, blocked thereafter — which is
        safe and occasionally costs a re-entry. Sending a ``cloid`` per leg is the fix; it is
        a wire change and is tracked in `docs/IMPROVEMENTS.md` §33 rather than guessed at.

        Derived from ``states`` rather than returning ``set(keys)`` directly, so that the day a
        ``cloid`` starts going out, this method is already correct.
        """
        return {key for key, state in self.states(keys).items() if order_is_live(state)}

    def place(self, plan: OrderPlan) -> Placement:
        """Send the bracket as one grouped action, and report what came back."""
        market = self.markets().get(plan.coin)
        if market is None:
            return Placement(
                ok=False,
                error=f"{plan.coin} is not available on {self.network}",
            )
        # ``wire`` deliberately deals in plain dicts so it stays free of SDK imports and can
        # be tested as pure data; the cast is the one place that structural fact is asserted.
        requests = cast(list[OrderRequest], order_requests(plan, market.sz_decimals))
        raw = self._exchange.bulk_orders(requests, grouping=GROUPING)
        return parse_placement(raw)
