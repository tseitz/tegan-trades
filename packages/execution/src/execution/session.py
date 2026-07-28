"""One trading session: the assembled broker, config, market list and order log.

This is the seam ``oracle.setups_cli`` talks to. It exists so that the triage loop stays a
user interface — it asks a question and prints an answer — while every decision about whether
an order is legal, how big it is and whether it has already been sent lives here.

**``execution`` never imports ``oracle``.** The listing is passed in rather than looked up, so
the dependency runs one way (``oracle`` -> ``execution``) and the package holding the signing
key does not pull in the price stack. ``listing`` is read structurally: ``symbol``, ``scale``
and ``is_proxy``, exactly what ``oracle.venue_map.Listing`` provides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from execution import config as config_module
from execution import guards
from execution import store
from execution.broker import Broker, HyperliquidBroker, dex_of
from execution.config import Config
from execution.guards import Refusal
from execution.plan import Market, OrderPlan, build
from execution.wire import Placement


@dataclass
class Session:
    """A live connection plus everything needed to decide and record.

    Not frozen: ``_equity`` is a cache. Equity is fetched once per margin pool per session
    rather than per candidate — it is a network round-trip, and re-reading it between two
    approvals in the same sitting would size two trades against different balances for no
    reason anyone asked for.
    """
    broker: Broker
    config: Config
    markets: dict[str, Market]
    orders_path: Path
    already_placed: set[str] = field(default_factory=set)
    _equity: dict[str, float] = field(default_factory=dict)
    _liquidity: dict = field(default_factory=dict)

    @property
    def network(self) -> str:
        return self.config.network

    @classmethod
    def open(cls, *, config: Config, dexs: tuple[str, ...] = (),
             orders_path=store.DEFAULT_PATH, credentials=None) -> Session:
        """Connect, load the market list, and read which candidates already have orders.

        Every failure here — missing key, unreachable venue, unknown network — happens before
        the first candidate is shown. That ordering is deliberate and copied from
        ``setups_cli.resolve_vault_note``: a session that dies mid-triage throws away the
        judgement already entered, which is the scarce input.
        """
        creds = credentials or config_module.credentials()
        broker = HyperliquidBroker(creds, network=config.network, dexs=dexs)
        return cls(
            broker=broker,
            config=config,
            markets=broker.markets(),
            orders_path=Path(orders_path),
            # Scoped to this network: a testnet rehearsal must not veto the mainnet trade it
            # was rehearsing for. See ``store.placed_keys``.
            already_placed=store.placed_keys(orders_path, network=config.network),
        )

    def equity(self, coin: str) -> float:
        """Account value backing this coin's margin pool, cached per pool.

        Keyed on the HIP-3 namespace because each builder holds its own collateral — sizing a
        ``xyz:GOLD`` trade against the core book's balance would size it against money that
        cannot back it.
        """
        dex = dex_of(coin)
        if dex not in self._equity:
            self._equity[dex] = self.broker.equity(dex)
        return self._equity[dex]

    def prepare(self, candidate, listing) -> OrderPlan | Refusal:
        """Price, size and vet a candidate — no network write, nothing recorded.

        Safe to call speculatively, which is what lets the confirmation prompt show real
        numbers rather than an estimate the placement might not honour.
        """
        if candidate.key in self.already_placed:
            return Refusal(
                "duplicate",
                f"{candidate.asset} {candidate.direction} already has a live order from an "
                f"earlier session — cancel it before sending another",
            )
        coin = getattr(listing, "symbol", None)
        return build(
            candidate,
            markets=self.markets,
            listing=listing,
            equity=self.equity(coin) if coin else 0.0,
            liquidity=self.liquidity(coin) if coin else None,
            enforce_liquidity=self.config.liquidity_enforced,
            risk_pct=self.config.risk_pct,
            max_notional_frac=self.config.max_notional_frac,
            min_volume=self.config.min_day_volume,
            min_open_interest=self.config.min_open_interest,
        )

    def liquidity(self, coin: str):
        """This market's tradability, fetched once per coin per session.

        Only fetched for coins this account can actually reach — a book snapshot costs a live
        call, and ``build`` refuses on the listing before it ever looks at liquidity.
        """
        if coin not in self.markets:
            return None
        if coin not in self._liquidity:
            self._liquidity[coin] = self.broker.liquidity(coin)
        return self._liquidity[coin]

    def liquidity_verdict(self, plan: OrderPlan) -> Refusal | None:
        """What the liquidity gate *would* say, whether or not it is enforced here.

        Exists so the rehearsal venue stays honest. Testnet books are mock, so enforcing the
        gate there would block everything while protecting nothing — but silently skipping it
        would let a market that mainnet refuses look perfectly fine in rehearsal. This lets
        the caller print "mainnet would refuse this" instead.
        """
        return guards.check_liquidity(
            self.liquidity(plan.coin), plan.notional,
            min_volume=self.config.min_day_volume,
            min_open_interest=self.config.min_open_interest,
        )

    def execute(self, plan: OrderPlan) -> Placement:
        """Send the bracket and record the outcome, successful or not.

        The log write is not conditional on success: a rejected order is the case most worth
        having written down, and it is the one a caller is most likely to forget to record.
        """
        placement = self.broker.place(plan)
        store.record_placement(self.orders_path, plan, placement, network=self.network)
        if placement.ok:
            self.already_placed.add(plan.candidate_key)
        return placement

    def decline(self, candidate, refusal: Refusal) -> None:
        """Record a candidate that was approved but never sent."""
        store.record_refusal(self.orders_path, candidate, refusal, network=self.network)


def describe(plan: OrderPlan) -> str:
    """The confirmation preview — every number that is about to be transmitted.

    Shows realised risk rather than the configured percentage, because flooring the size to a
    whole lot always leaves the two different and occasionally very different. The number a
    person is agreeing to should be the number the venue will act on.
    """
    side = "BUY" if plan.is_buy else "SELL"
    risk_pct = plan.risk / plan.equity if plan.equity else 0.0
    return "\n".join([
        f"  {side} {plan.size:g} {plan.coin} @ {plan.entry:g}  (limit, GTC)",
        f"    take profit {plan.target:g}   stop loss {plan.stop:g}",
        f"    risk ${plan.risk:,.2f} ({risk_pct:.2%} of ${plan.equity:,.2f})"
        f"   notional ${plan.notional:,.2f} ({plan.leverage:.2f}x)",
    ])
