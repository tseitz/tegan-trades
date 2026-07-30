"""Several venues open at once, so the router's choice can actually be honoured.

``Session`` is one venue: one broker, one network, one market list, one running notional total.
That was the right object and it still is — nothing here changes it. What was missing is the
thing that holds more than one of them, because ``Config.venue`` was *the decision* and every
candidate in a run therefore went to the same place regardless of what it cost there.

**A venue that cannot be reached drops out alone.** This is the whole reason the class exists.
``Session.open`` raises on a missing key, so one unconfigured venue took the entire run with
it — including the run whose trade was routed to the venue that *was* configured. Here every
failure becomes an ``Unroutable`` with a reason, the remaining venues open, and the caller
reports which ones dropped and why. A venue with no ``Broker`` at all (Kraken, priced by
``core.routing`` and not placeable) is the same answer rather than an exception: not routable is
a fact about this repo, not a bug in the caller.

**Failures still land before the first candidate is shown.** Sessions are opened eagerly, for
the venues the queue actually routes to, not lazily on first use. Lazy would authenticate less,
but it would surface a missing key *mid-triage* — and a session that dies mid-triage throws away
the judgement already entered, which is the scarce input. ``Session.open`` is built on that
ordering (see its docstring) and this preserves it across venues.

WHY THE NETWORK IS TRANSLATED AND NOT COPIED. ``mainnet`` is Hyperliquid's spelling of real
money and ``live`` is Alpaca's — the fact they share is the *tier*, and ``venues.NETWORKS`` is
the table that pairs them. Carrying the word across would open Alpaca *paper* for a run the
user typed a real-money confirmation for, reporting rehearsal fills as though they were real;
carrying it the other way would be worse. So the configured venue keeps the network it was
given and every other venue resolves to its own spelling of the same tier.

``Config.venue`` survives as two smaller things: the tier anchor above, and the fallback for a
candidate routing has no answer for.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from execution import portfolio, store, venues
from execution.account import Account
from execution.config import Config, MissingCredentials
from execution.session import Session

# Why a venue is not routable tonight. Separate codes because they call for different acts:
# add a key, wait for the venue, write an adapter, or type the phrase.
REASON_NO_CREDENTIALS = "no_credentials"
REASON_UNREACHABLE = "unreachable"
REASON_NO_ADAPTER = "no_adapter"
REASON_NOT_CONFIRMED = "not_confirmed"


@dataclass(frozen=True, slots=True)
class Unroutable:
    """A venue this run cannot place on, and what would change that.

    Carries the venue's own message in ``detail`` rather than a summary of it: the missing
    credentials error names the two environment variables and how to load them, which is the
    only actionable part and the easiest thing to lose in translation.
    """
    venue: str
    reason: str
    detail: str


def network_for(config: Config, venue: str) -> str:
    """The network to open ``venue`` on, at the tier ``config`` was set to.

    Raises on a venue with no entry in ``venues.NETWORKS`` — a venue nobody recognises must not
    resolve to somewhere money can move. ``Desk.open`` catches that and reports it; callers
    reaching here directly are asking about a venue they named themselves.
    """
    if venue not in venues.NETWORKS:
        raise ValueError(f"unknown venue {venue!r}; expected one of {sorted(venues.NETWORKS)}")
    if venue == config.venue:
        return config.network
    rehearsal, real = venues.NETWORKS[venue]
    return real if venues.is_real_money(config.network) else rehearsal


def config_for(config: Config, venue: str) -> Config:
    """``config`` as it applies to one venue: same risk settings, that venue's network.

    ``replace`` and not a field-by-field rebuild, for the reason ``execute.open_session``
    records — a rebuild that named four of the settings let a flag about *where* to trade
    quietly reset *how much* to risk, and every field added afterwards would have had to
    remember to appear in it.

    A per-venue ``Config`` is also what keeps the existing per-venue branches working
    untouched: ``liquidity_enforced`` reads ``self.venue``, so an Alpaca session built this way
    turns the perp liquidity gate off without anybody passing a flag.
    """
    derived = replace(config, venue=venue, network=network_for(config, venue))
    derived.validate()
    return derived


@dataclass
class Desk:
    """One ``Session`` per reachable venue, plus a reason for every venue that is not.

    Not frozen for the same reason ``Session`` is not: the sessions it holds carry running
    totals, and ``_shortable`` caches one account read per venue.
    """
    # The config the desk was opened from, kept whole. It is the tier anchor for
    # ``network_for``, the fallback venue for ``resolve``, and the run's own network for
    # anything that has to be recorded without a session to record it against.
    config: Config = field(default_factory=Config)
    orders_path: Path = store.DEFAULT_PATH
    sessions: dict[str, Session] = field(default_factory=dict)
    unroutable: dict[str, Unroutable] = field(default_factory=dict)
    _shortable: dict[str, bool | None] = field(default_factory=dict)

    @property
    def default_venue(self) -> str:
        """``Config.venue`` — no longer the decision, still the fallback."""
        return self.config.venue

    @property
    def network(self) -> str:
        """The run's own network, in the configured venue's spelling. Every other venue is on
        the same *tier* under a different name — see ``network_for``."""
        return self.config.network

    @property
    def routable(self) -> tuple[str, ...]:
        return tuple(sorted(self.sessions))

    def session_for(self, venue: str) -> Session | None:
        return self.sessions.get(venue)

    def refusal_for(self, venue: str) -> Unroutable | None:
        return self.unroutable.get(venue)

    def resolve(self, venue: str | None) -> Session | None:
        """The session a candidate should be placed through. ``None`` means it cannot be.

        **No silent fallback for a routed venue.** A candidate the router sent to Alpaca must
        not be placed on Hyperliquid because Alpaca happened to be unreachable — the queue has
        already shown a line saying which venue was cheaper and by how much, and quietly using
        the other one makes that line a lie about an order that exists. ``None`` here is a
        refusal the caller reports; the trade can be re-run once the venue is back.

        ``venue=None`` means routing had no answer at all, and *that* is what the configured
        venue is for.
        """
        return self.sessions.get(self.default_venue if venue is None else venue)

    def equity_by_venue(self) -> dict[str, float | None]:
        """Each routable venue's account value, or ``None`` where it could not be read.

        ``None`` rather than 0.0, and the distinction is the whole reason this returns a dict of
        optionals: a venue that failed to answer must not shrink the denominator of the pooled
        ceiling as though it were empty. See ``portfolio.combine``.

        Asked with the empty dex, which is the core pool on Hyperliquid and the only pool on
        Alpaca. Cached by ``Session.equity``, so calling this per candidate costs one round-trip
        per venue per sitting.
        """
        out: dict[str, float | None] = {}
        for venue, session in self.sessions.items():
            try:
                out[venue] = session.equity("")
            except Exception:  # noqa: BLE001 - a silent venue is data, not an error
                out[venue] = None
        return out

    def book(self) -> portfolio.Book:
        """The portfolio's risk state, pooled across every routable venue.

        **This is where risk stops being a per-venue quantity.** Each ``Session`` tracks its own
        risk for the same reason it tracks its own notional — it is the only one that can — and
        this sums them, because losing 1% on each of two books is losing 2% of one person's
        account. ``cfg/execution.yaml``'s note on ``venue`` names running both venues at
        ``risk_pct`` as the thing to revisit before treating them as one account; this is that.

        Buying power is pointedly **not** summed anywhere: there is no transfer path between a
        perp margin pool and equity buying power, so headroom stays per session.
        """
        return portfolio.Book(
            pool=portfolio.combine(self.equity_by_venue()),
            spent=sum(s.risk_at_stake for s in self.sessions.values()),
            max_risk=self.config.max_portfolio_risk,
            unpriced=sum(s.risk_unpriced for s in self.sessions.values()),
        )

    def can_short(self, venue: str) -> bool | None:
        """Whether ``venue`` will let this account sell short. ``None`` means it did not say.

        Asked of the venue it is a fact *about*, which is the point. A single-broker run read
        whichever account happened to be connected, so a Hyperliquid session answered a
        question about Alpaca with "no account" — reported downstream as "not asked" for
        something Alpaca would have answered plainly.

        ``None`` is deliberately distinct from ``False``: ``core.routing`` renders "not asked"
        as a different refusal from a measured "cannot short", because Alpaca has been observed
        reporting ``no_shorting: false`` in its config while ``shorting_enabled`` was false in
        its state. The field read here is the state one — see ``account.parse_account``.

        Cached, so a sitting costs one round-trip per venue. Unlike ``Session.read_account``,
        which is deliberately fresh per candidate because headroom moves; shortability is an
        account *type* and does not change between two approvals.
        """
        if venue not in self._shortable:
            session = self.sessions.get(venue)
            account: Account | None = session.read_account() if session is not None else None
            self._shortable[venue] = account.can_short if account is not None else None
        return self._shortable[venue]

    @classmethod
    def open(cls, *, config: Config, wanted, dexs: tuple[str, ...] = (),
             orders_path=store.DEFAULT_PATH, unroutable=None,
             session_factory=Session.open) -> Desk:
        """Connect to each venue in ``wanted``, degrading rather than raising.

        ``unroutable`` is pre-seeded by the caller for venues already ruled out before any
        connection was attempted — today that is a declined real-money confirmation. The typed
        barrier is a UI act and stays in the UI layer, so the desk is *told* the outcome rather
        than prompting from inside the package that holds the key.

        One ``orders_path`` for every venue, scoped per network by ``store.placed_keys``. Two
        logs would make "what did I send tonight" a question with two answers.
        """
        sessions: dict[str, Session] = {}
        refused: dict[str, Unroutable] = dict(unroutable or {})
        for venue in dict.fromkeys(wanted):  # de-duplicated, order preserved
            if venue in refused:
                continue
            if venue not in venues.NETWORKS:
                refused[venue] = Unroutable(
                    venue, REASON_NO_ADAPTER,
                    f"{venue} has no broker in this repo — it can be priced but not traded. "
                    f"Placing there is a venue_map row plus an adapter.",
                )
                continue
            # Outside the try, deliberately. A venue/network pair that fails validation is a
            # bad *config*, not a venue that is down, and reporting it as ``unreachable`` would
            # send someone to check the network for a typo in a yaml file.
            venue_config = config_for(config, venue)
            try:
                sessions[venue] = session_factory(
                    config=venue_config, dexs=dexs, orders_path=orders_path)
            except MissingCredentials as exc:
                refused[venue] = Unroutable(venue, REASON_NO_CREDENTIALS, str(exc))
            except Exception as exc:  # noqa: BLE001 - see below
                # Deliberately broad. Everything between here and the venue is a network stack
                # and an SDK, and the failure modes are not enumerable — but none of them are a
                # reason to lose the other venue's session or the queue. The type name is kept
                # in the message so a genuine bug in this package is still identifiable rather
                # than reading as "the venue was down".
                refused[venue] = Unroutable(
                    venue, REASON_UNREACHABLE, f"{type(exc).__name__}: {exc}")
        return cls(config=config, orders_path=Path(orders_path),
                   sessions=sessions, unroutable=refused)


def describe(desk: Desk) -> str:
    """What opened, where, and why anything did not. One line per venue.

    Printed before the first candidate, because a venue that dropped out changes which trades
    are placeable and that is worth knowing while there are still decisions to make.
    """
    lines = []
    for venue in desk.routable:
        session = desk.sessions[venue]
        lines.append(f"  {venue} {session.network} — {len(session.markets)} markets")
    for venue in sorted(desk.unroutable):
        refusal = desk.unroutable[venue]
        lines.append(f"  {venue} not routable ({refusal.reason}) — {refusal.detail}")
    return "\n".join(lines)
