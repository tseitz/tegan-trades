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
from execution import guards, store, venues
from execution import portfolio as portfolio_module
from execution.account import Account
from execution.alpaca_broker import AlpacaBroker
from execution.broker import Broker, HyperliquidBroker, dex_of
from execution.config import Config
from execution.guards import Refusal
from execution.plan import Market, OrderPlan, build
from execution.sizing import (
    CAP_BUDGET,
    CAP_CONCENTRATION,
    CAP_LEVERAGE,
    CAP_PARTICIPATION,
    CAP_VENUE_LEVERAGE,
)
from execution.wire import Placement


def open_broker(config: Config, credentials, *, dexs: tuple[str, ...] = ()) -> Broker:
    """The venue's live connection. Separate from ``Session.open`` so the mapping from a
    config to a class is one readable statement rather than a branch inside a constructor.

    ``dexs`` is a HIP-3 concept and is accepted-and-ignored elsewhere; Alpaca has one account
    and one pool, so there is nothing for it to select.
    """
    if config.venue == venues.ALPACA:
        return AlpacaBroker(credentials, network=config.network)
    if config.venue == venues.HYPERLIQUID:
        return HyperliquidBroker(credentials, network=config.network, dexs=dexs)
    raise ValueError(
        f"unknown venue {config.venue!r}; expected one of {sorted(venues.NETWORKS)}"
    )


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
    # Notional this session has successfully sent. Not a cache — a running total, and the
    # reason the eighth approval of a sitting can see the first seven. See ``Account.headroom``
    # for why the venue's own buying power cannot answer this alone before the open.
    _committed: float = 0.0
    # Dollars at risk on this venue: what everything still live would cost if it stopped out.
    # SEEDED FROM THE ORDER LOG at open, not zero — a position from last night is still at risk,
    # and the pooled ceiling starting fresh each sitting would be a ceiling that resets itself
    # every evening. See ``store.risk_by_key``: the log is the only record of this, because a
    # venue reports a position and its stop as two unrelated objects.
    _risk: float = 0.0
    # Live orders whose risk the log cannot state. Kept because ``_risk`` is then a LOWER bound,
    # and an under-counted total loosens the pooled ceiling — the dangerous direction.
    _risk_unpriced: int = 0
    _account: Account | None = None

    @property
    def network(self) -> str:
        return self.config.network

    @property
    def risk_at_stake(self) -> float:
        """USD this venue would lose if everything live stopped out. A lower bound when
        ``risk_unpriced`` is non-zero."""
        return self._risk

    @property
    def risk_unpriced(self) -> int:
        return self._risk_unpriced

    @property
    def account(self) -> Account | None:
        """The last account read, for the confirmation preview. ``None`` before the first
        ``prepare`` and on any venue that does not report one."""
        return self._account

    @classmethod
    def open(cls, *, config: Config, dexs: tuple[str, ...] = (),
             orders_path=store.DEFAULT_PATH, credentials=None) -> Session:
        """Connect, load the market list, and read which candidates already have orders.

        Every failure here — missing key, unreachable venue, unknown network — happens before
        the first candidate is shown. That ordering is deliberate and copied from
        ``setups_cli.resolve_vault_note``: a session that dies mid-triage throws away the
        judgement already entered, which is the scarce input.
        """
        creds = credentials or config_module.credentials_for(config.venue)
        broker = open_broker(config, creds, dexs=dexs)
        # The log says which candidates an order was ever sent for; the venue says which of
        # those still have something working. Only the second is the duplicate guard's actual
        # question — a bracket that filled through its own stop on a gapped open round-trips
        # flat in seconds, and blocking that candidate forever burns a setup that never
        # traded. Scoped to this network, so a testnet rehearsal cannot veto the mainnet trade
        # it was rehearsing for (see ``store.placed_keys``).
        placed = store.placed_keys(orders_path, network=config.network)
        live = broker.live_keys(placed)
        # The opening risk balance, from the same log and the same live set — no extra call. A
        # live key includes a *filled* bracket whose exit legs are still working, which is
        # exactly the position that is still at risk, so the two questions share one answer.
        recorded = store.risk_by_key(orders_path, network=config.network)
        return cls(
            broker=broker,
            config=config,
            markets=broker.markets(),
            orders_path=Path(orders_path),
            already_placed=live,
            _risk=sum(recorded[key] for key in live if key in recorded),
            _risk_unpriced=sum(1 for key in live if key not in recorded),
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

    def prepare(self, candidate, listing, *,
                book: portfolio_module.Book | None = None) -> OrderPlan | Refusal:
        """Price, size and vet a candidate — no network write, nothing recorded.

        Safe to call speculatively, which is what lets the confirmation prompt show real
        numbers rather than an estimate the placement might not honour.

        ``book`` is the *portfolio's* risk state and is passed in rather than built here,
        because a session knows only its own venue and the pooled ceiling spans all of them.
        ``None`` leaves that ceiling off — which is the state of every caller that has not been
        given a ``Desk``.
        """
        if candidate.key in self.already_placed:
            return Refusal(
                "duplicate",
                f"{candidate.asset} {candidate.direction} already has a live order from an "
                f"earlier session — cancel it before sending another",
            )
        coin = getattr(listing, "symbol", None)
        account = self.read_account()
        return build(
            candidate,
            markets=self.markets,
            listing=listing,
            equity=self.equity(coin) if coin else 0.0,
            liquidity=self.liquidity(coin) if coin else None,
            enforce_liquidity=self.config.liquidity_enforced,
            depth=self.depth(coin) if coin else None,
            max_participation=self.config.max_participation,
            risk_pct=self.config.risk_pct,
            max_notional_frac=self.config.max_notional_frac,
            max_position_frac=self.config.max_position_frac,
            venue_multiplier=account.overnight_multiplier if account else None,
            headroom=account.headroom(self._committed) if account else None,
            book=book,
            min_budget_fill=self.config.min_budget_fill,
            can_short=account.can_short if account else None,
            min_volume=self.config.min_day_volume,
            min_open_interest=self.config.min_open_interest,
        )

    def read_account(self) -> Account | None:
        """The venue's current view of the account. Fetched fresh, deliberately.

        The opposite decision to ``equity``, which is cached per pool so that two approvals in
        the same sitting are not sized against different balances. Headroom is the number that
        *must* move between candidates — that is the entire point of it — and a position
        filling mid-session changes it without anyone placing anything.

        ``None`` on a venue that reports no account, which leaves the budget gate off rather
        than refusing everything. See ``account.parse_account``.
        """
        self._account = self.broker.account()
        return self._account

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

    def depth(self, coin: str):
        """A typical session in this market, for the participation ceiling. Broker-cached.

        Guarded by the same universe check as ``liquidity``: a coin this account cannot reach
        is refused by ``check_listing`` long before a size exists to cap, so paying for the
        bars would be spending a network call to learn nothing.
        """
        if coin not in self.markets:
            return None
        return self.broker.depth(coin)

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
            # Counted only on success, and counted here rather than at the venue: before the
            # open, an accepted bracket does not necessarily reduce buying power, so the next
            # candidate would otherwise be sized against room this order has already spent.
            self._committed += plan.notional
            # The same argument for risk, one level up: this total spans venues once a ``Desk``
            # sums it, so the sixth approval of a sitting sees the first five wherever they went.
            self._risk += plan.risk
        return placement

    def decline(self, candidate, refusal: Refusal) -> None:
        """Record a candidate that was approved but never sent."""
        store.record_refusal(self.orders_path, candidate, refusal, network=self.network)


def cap_explanation(plan: OrderPlan) -> str:
    """Why this order is smaller than the risk budget asked for, in the reader's terms.

    Derived from the plan's own numbers rather than stored, so there is no second copy of a
    fact to fall out of step with the first. Each phrasing names the thing to *do* about it,
    because that is what differs between the four: raise a ceiling, accept a smaller slice,
    trade something more liquid, or cancel a resting order.
    """
    if plan.cap_reason == CAP_PARTICIPATION and plan.depth is not None:
        return f"{plan.size / plan.depth.median_volume:.1%} of a median session"
    if plan.cap_reason == CAP_CONCENTRATION:
        return f"one position may be at most {plan.leverage:.0%} of equity"
    if plan.cap_reason == CAP_LEVERAGE:
        return f"notional held to {plan.leverage:.2f}x equity"
    if plan.cap_reason == CAP_VENUE_LEVERAGE:
        # Named as the venue's rule, not as a setting, because it is not one: Reg T caps an
        # overnight position at 2x however the account is configured, and telling the reader to
        # raise a ceiling they cannot raise is worse than saying nothing.
        return (f"the venue will hold {plan.leverage:.2f}x overnight — Reg T, not a setting")
    if plan.cap_reason == CAP_BUDGET:
        return f"all that ${plan.notional:,.2f} of remaining buying power pays for"
    return "a ceiling applied"


def describe_book(plan: OrderPlan, book: portfolio_module.Book) -> str | None:
    """The pooled risk total, and what this order does to it. ``None`` when there is no ceiling.

    Printed on every order rather than only when it binds, for the same reason the account line
    is: the point is to make the running total visible while there is still a decision to make
    about it. On 2026-07-29 eight orders each read 1% and together risked 7.94%, and that number
    was on nobody's screen until the venue rejected three at the open.

    **The caveats ride along with the number.** A total that cannot see one venue's equity, or
    cannot price a live order's risk, is a lower bound — and a lower bound presented as a total
    is exactly how "unmeasured" comes to read as "zero".
    """
    if book.remaining is None:
        return None
    after = (book.spent + plan.risk) / book.pool.equity if book.pool.equity else 0.0
    caveats = []
    if book.pool.silent:
        caveats.append(f"{', '.join(book.pool.silent)} not counted")
    if book.unpriced:
        caveats.append(f"{book.unpriced} live order(s) with no recorded risk")
    ceiling = f", ceiling {book.max_risk:.0%}" if book.max_risk is not None else ""
    return (
        f"    book {book.at_stake:.2%} of ${book.pool.equity:,.2f} at risk across "
        f"{', '.join(book.pool.answered)} — {after:.2%} after this{ceiling}"
        + (f"   ! {'; '.join(caveats)}" if caveats else "")
    )


def describe(plan: OrderPlan, account: Account | None = None,
             book: portfolio_module.Book | None = None) -> str:
    """The confirmation preview — every number that is about to be transmitted.

    Shows realised risk rather than the configured percentage, because flooring the size to a
    whole lot always leaves the two different and occasionally very different. The number a
    person is agreeing to should be the number the venue will act on.
    """
    side = "BUY" if plan.is_buy else "SELL"
    risk_pct = plan.risk / plan.equity if plan.equity else 0.0
    lines = [
        f"  {side} {plan.size:g} {plan.coin} @ {plan.entry:g}  (limit, GTC)",
        f"    take profit {plan.target:g}   stop loss {plan.stop:g}",
        f"    risk ${plan.risk:,.2f} ({risk_pct:.2%} of ${plan.equity:,.2f})"
        f"   notional ${plan.notional:,.2f} ({plan.leverage:.2f}x)",
    ]

    # **On an instrument that stops trading overnight, that risk figure is an intent.** A stop
    # is a market order once triggered, so a gapped open fills it at the open and not at the
    # stop, and everything past it is loss the budget never authorised. ``core.gaps`` measures
    # how often: 2.36% of sessions gap adversely past a 4.53% stop — a 39% chance of at least
    # one over a 21-session hold — with a 46x spread across assets. ``venue_routing`` already
    # prices that into which venue wins; the person approving the order was the last one not
    # told. Said in words rather than modelled here on purpose: sizing off gap-adjusted risk is
    # a real decision and should not be reached for before this cheap one has been tried.
    #
    # Equities only. Perps fund continuously and do not gap, and a caveat on every order is the
    # warning nobody reads.
    if plan.depth is not None:
        lines.append(
            "    that stop is an intent, not a bound — a gapped open fills it at the open"
        )

    # Only when a ceiling actually bound. A cut order is the one case where the numbers above
    # understate what is going on — the risk line will read 0.15% where 1% was configured, and
    # without this that looks like a bug rather than an answer. The cause is named because
    # "why is this small" is the question that follows, and its four answers are unrelated.
    if plan.capped_from is not None:
        lines.append(
            f"    ! capped from {plan.capped_from:g} to {plan.size:g} — "
            f"{cap_explanation(plan)}"
        )

    # **Printed on every equity order, not only when the participation ceiling bound** — the
    # remaining half of `docs/IMPROVEMENTS.md` §36. It used to appear only under a cap, so a
    # market thin enough to shrink the order showed its numbers and a market merely thin showed
    # nothing: 175 trades/day and 26,862 trades/day rendered identically as long as neither
    # tripped the 1% ceiling. The ceiling answers "should this order shrink"; this line answers
    # "what am I about to stand in", and only the first of those needs a threshold.
    #
    # Deliberately unmarked rather than a warning. The numbers separate INTL from SBSW by 150x
    # on their own, whereas a `!` on every equity is the failure `oracle.execute` names for the
    # old Alpaca liquidity line — a warning that cannot distinguish teaches you to skip it.
    # ``None`` on perps by construction (``HyperliquidBroker.depth``), where ``check_liquidity``
    # answers the same question live and better.
    if plan.depth is not None:
        d = plan.depth
        # The order as a share of a typical session — the one figure here that is about *this*
        # order rather than the market, and the continuum the ceiling is a threshold on. Shown
        # so the line that is chosen later can be read off accumulated orders rather than
        # picked: 6.6% and 0.04% are the two the sizing measurement found.
        share = plan.size / d.median_volume if d.median_volume else None
        lines.append(
            f"    market {d.median_volume:,.0f} sh/day over {d.sessions} sessions, "
            f"{d.median_trades:,.0f} trades/day, ~${d.dollars_per_trade:,.0f} per trade"
            + (f" — this order is {share:.2%} of one" if share is not None else "")
        )

    # The total that nothing computed (`docs/IMPROVEMENTS.md` §40). Sizing is per-trade, so
    # eight approvals in one sitting each looked like 1% and together wanted 123.6% of the
    # account — a fact that was on nobody's screen until the venue rejected three of them at
    # the open. Printed on every order, not only when it binds, because the point is to make
    # the running total visible while there is still a decision to make about it.
    if account is not None and account.buying_power is not None:
        after = max(0.0, account.buying_power - plan.notional)
        lines.append(
            f"    account ${account.buying_power:,.2f} free"
            + (f" of ${account.equity:,.2f} (${account.committed:,.2f} committed)"
               if account.committed is not None else "")
            + f" — ${after:,.2f} left after this"
        )

    # The line above is this venue's buying power; this one is the whole book's risk. They are
    # deliberately two lines because they are two quantities — one pools across venues and one
    # cannot — and a reader who confuses them will cancel the wrong order.
    if book is not None:
        pooled = describe_book(plan, book)
        if pooled is not None:
            lines.append(pooled)
    return "\n".join(lines)
