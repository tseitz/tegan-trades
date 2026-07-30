"""The glue between the triage queue and the order book. Lives here, not in ``execution``.

``execution`` deliberately knows nothing about ``venue_map``, theses or the corpus — it takes
a listing and a candidate and does arithmetic. Something has to hold the two together, and
this is it: the one module that imports both.

It is a separate file rather than more of ``setups_cli`` because that module is already at
745 lines against an 800 ceiling, and because the triage loop reads better when the approve
branch is one call rather than thirty lines of order handling.

**Nothing here runs unless ``--execute`` was passed.** The nightly job calls
``setups --list``, which returns before the triage loop exists, so the scheduled path cannot
reach this module at all.
"""
from __future__ import annotations

from dataclasses import replace

from execution import config as config_module
from execution import desk as desk_module
from execution import store, venues
from execution.broker import dex_of
from execution.config import load as load_config
from execution.desk import Desk, Unroutable
from execution.guards import Refusal
from execution.session import describe

from oracle import liveness, venue_map

# Typed rather than a keystroke. ``--network mainnet`` sits one arrow-key away from
# ``--network testnet`` in shell history — and ``live`` is one word from ``paper`` — so the
# barrier has to be something a stray press cannot produce.
#
# Deliberately venue-neutral wording. The phrase names what is true of every real-money
# network rather than one venue's spelling of it, which is the mistake this whole path made
# for a week: the gate compared against ``MAINNET``, so Alpaca's ``live`` walked through it.
REAL_MONEY_CONFIRMATION = "yes, real money"

DECLINED = "declined"

# Listed, correctly named, and not trading. Its own refusal code rather than ``unlisted``,
# because they call for opposite responses: an unlisted asset needs a venue that carries it,
# a dormant one is carried by a venue where nobody shows up. Defined here and not in
# ``execution.guards`` because there is no pure check behind it — the evidence is the funding
# log, which ``execution`` neither reads nor should learn about.
DORMANT = "dormant"

# Routed somewhere this run cannot place. Its own code, and not ``unlisted`` or ``dormant``:
# those two are facts about the asset and the venue, while this one is a fact about *tonight* —
# a key that is absent, a venue that is down, a real-money confirmation that was declined. The
# trade is fine; re-run it once the venue is back.
UNROUTABLE = "unroutable"


def hyperliquid_dexs(venue: str = "hyperliquid", *, path=venue_map.CFG_PATH) -> tuple[str, ...]:
    """Every HIP-3 builder the venue map references, so their metas get loaded.

    Without this the SDK loads only the core book and ``xyz:GOLD`` fails to resolve to an
    asset index — an error raised before the order is sent, on exactly the non-crypto assets
    the roster talks about most.
    """
    dexs = set()
    for entry in venue_map.load(path).values():
        if not isinstance(entry, dict):
            continue
        symbol = entry.get(venue)
        if symbol:
            dexs.add(dex_of(symbol))
    return tuple(sorted(dexs - {""}))


def confirm_real_money(venue: str, network: str, input_fn, out) -> bool:
    """Make real money an explicit, typed act. Returns False unless the phrase matches.

    The banner names the venue and the network because they are what differ, and because a
    prompt reading ``*** MAINNET ***`` over an Alpaca brokerage account is one a reader would
    be right to distrust — and distrusting this particular prompt is the entire failure mode
    it exists to prevent.
    """
    out("")
    out(f"  *** {venue.upper()} {network.upper()} — orders will use real funds ***")
    typed = input_fn(f'  type "{REAL_MONEY_CONFIRMATION}" to continue: ').strip().lower()
    if typed == REAL_MONEY_CONFIRMATION:
        return True
    out("  not confirmed — no session opened")
    return False


def open_desk(*, wanted, network: str | None = None, config_path=None, input_fn=input,
              out=print, session_factory=None) -> Desk | None:
    """Load config, take the real-money barrier per venue, and connect to each. None if nothing
    is reachable.

    Every failure here lands before the first candidate is shown, mirroring
    ``setups_cli.resolve_vault_note``: a session that dies mid-triage discards the judgement
    already entered, and judgement is the scarce input. That is why the desk is opened over
    every venue the queue *might* route to rather than lazily on first use — one extra
    authentication is cheaper than learning about a missing key after nine decisions.

    **The barrier is per venue and declining one leaves the others.** A run at the real-money
    tier reaches real money on every venue it opens (``desk.network_for`` translates the tier,
    it does not carry the word), so each one is asked separately — and "real funds on the perp
    book tonight, not on the brokerage account" is an answer worth being able to give.
    """
    config = load_config(config_path) if config_path else load_config()
    if network is not None:
        # ``replace`` and not a field-by-field rebuild. The rebuild listed four of the eight
        # settings, so passing --network silently reset the liquidity floors, the enforcement
        # override and the participation ceiling to their defaults — a flag about *where* to
        # trade quietly changing *how much* to risk. Every field added since would have had
        # to remember to appear here, and none of them would have failed a test by not.
        config = replace(config, network=network)
        config.validate()

    # The configured venue is always opened, whether or not the queue routed anything to it:
    # it is what a candidate with no routing answer falls back to, and discovering it is
    # unreachable at that point would be discovering it too late.
    order = tuple(dict.fromkeys((config.venue, *wanted)))

    declined: dict[str, Unroutable] = {}
    for venue in order:
        if venue not in venues.NETWORKS:
            continue  # no adapter — ``Desk.open`` reports it, and there is nothing to confirm
        venue_network = desk_module.network_for(config, venue)
        # Asked of the venue table, never of one venue's spelling. ``requires_typed_confirmation``
        # is the single place that knows which networks move real money — it existed and was
        # tested throughout, but nothing called it, and the call site compared against
        # ``MAINNET`` instead. Alpaca's real-money network is ``live``, which did not match, so
        # a funded brokerage account opened a session with nothing typed.
        if config_module.requires_typed_confirmation(venue_network) and not confirm_real_money(
            venue, venue_network, input_fn, out
        ):
            declined[venue] = Unroutable(
                venue, desk_module.REASON_NOT_CONFIRMED,
                f"real money on {venue} {venue_network} was not confirmed",
            )

    desk = Desk.open(
        config=config, wanted=order, unroutable=declined,
        # Always asked of Hyperliquid, whatever the configured venue is. Without these the SDK
        # loads only the core book and ``xyz:GOLD`` fails to resolve to an asset index.
        dexs=hyperliquid_dexs(),
        **({} if session_factory is None else {"session_factory": session_factory}),
    )
    if not desk.routable:
        out("  no venue is reachable — no session opened")
        out(desk_module.describe(desk))
        return None

    out(f"  execution ON — {desk.network} tier, risking {config.risk_pct:.2%} per trade")
    out(desk_module.describe(desk))
    out(f"  orders log to {desk.orders_path}")
    return desk


def offer_routed(desk, candidate, venue: str | None, *, input_fn=input, out=print,
                 is_dormant=liveness.dormant) -> None:
    """Offer ``candidate`` on the venue routing chose, or record why it could not be.

    **A candidate is never placed anywhere but where the queue said it would be.** The routing
    line has already told the reader which venue was cheaper and by how much; substituting the
    runner-up because the winner is unreachable would make that line a false statement about an
    order that exists, and the whole point of pricing both venues was to stop paying the
    difference by accident. So an unreachable winner is a refusal — recorded, not just printed,
    because "approved and not sent" is the class of outcome the order log exists to keep.

    ``venue=None`` is routing having no answer at all, and that is what the configured venue
    is for.
    """
    target = desk.default_venue if venue is None else venue
    session = desk.session_for(target)
    if session is None:
        dropped = desk.refusal_for(target)
        detail = (f"{candidate.asset} routes to {target}, which this run cannot reach"
                  + (f" ({dropped.reason}) — {dropped.detail}" if dropped is not None else ""))
        out(f"  ! not executable — {detail}")
        # Recorded against the run's own network. The field scopes the duplicate guard, and an
        # order that was never sent has nothing to guard — what matters is that the log can
        # answer "what did tonight refuse, and why" without the venue being reachable to ask.
        store.record_refusal(desk.orders_path, candidate, Refusal(UNROUTABLE, detail),
                             network=desk.network)
        return
    # The pooled risk book, read here because only the desk spans venues. Built per candidate
    # rather than per sitting: ``Session.equity`` caches the round-trips, and the total has to
    # move as orders go out — the whole failure it prevents is the sixth approval not seeing the
    # first five.
    offer(session, candidate, book=desk.book(), input_fn=input_fn, out=out,
          is_dormant=is_dormant)


def offer(session, candidate, *, book=None, input_fn=input, out=print,
          is_dormant=liveness.dormant) -> None:
    """Offer to execute one approved candidate. Never raises past the caller.

    A failure to place must not take down a triage session — the approval is already durable
    in the decisions sidecar, and losing the remaining queue to a venue timeout would cost
    more than the order was worth.
    """
    venue = session.config.venue

    # Before the listing is even resolved, because this refusal needs nothing from the venue:
    # the map plus the funding log settle it offline. That matters twice — it survives the
    # venue outage during which a dead market is least likely to be noticed, and it is the
    # one liquidity-shaped check that stays honest on testnet, where the mock book makes
    # ``check_liquidity`` unenforceable. See ``oracle.liveness`` for why the log can answer.
    if is_dormant(candidate.asset, venue):
        refusal = Refusal(
            DORMANT,
            f"{candidate.asset} is listed on {venue} but has reported no funding in "
            f"{liveness.DEFAULT_WINDOW_DAYS}d while the rest of its venue did — "
            f"a stop there has nothing to fill against",
        )
        out(f"  ! not executable — {refusal.detail}")
        session.decline(candidate, refusal)
        return

    listing = venue_map.listing(candidate.asset, venue)
    outcome = session.prepare(candidate, listing, book=book)

    if isinstance(outcome, Refusal):
        out(f"  ! not executable — {outcome.detail}")
        session.decline(candidate, outcome)
        return

    # The account goes in beside the plan so the preview can show the running total. Per-trade
    # numbers alone are what let eight approvals in one sitting each read as 1% while together
    # wanting 123.6% of the account — see `docs/IMPROVEMENTS.md` §40.
    out(describe(outcome, session.account, book))

    # The perp liquidity gate is measured but not enforced on the rehearsal network. Saying so
    # keeps it honest — otherwise a market mainnet would never allow looks perfectly healthy
    # in rehearsal, which is the opposite of what a rehearsal is for.
    #
    # ONLY ON HYPERLIQUID. This whole block used to run for Alpaca too, where it printed a
    # constant: ``AlpacaBroker.liquidity`` returns None for every equity, so the "could not
    # read this market's liquidity" line fired identically on a fund trading 175 times a day
    # and on one trading 39,000 — a warning that cannot distinguish them teaches you to skip
    # warnings. The equity check is the participation cap, which ``describe`` prints as part
    # of the order because it changes the order.
    if not session.config.liquidity_enforced and session.config.venue != venues.ALPACA:
        verdict = session.liquidity_verdict(outcome)
        if verdict is not None:
            # Careful with the claim. The verdict is computed from *this* network's book, so
            # on testnet it says nothing about mainnet — xyz:SP500 has no testnet book and
            # $457M/day of real one. Phrasing it as "mainnet would refuse" would be a
            # confident falsehood, and a warning that cries wolf teaches you to skip warnings.
            out(f"  ! would fail the liquidity gate on {session.network} data — "
                f"{verdict.detail}")
            # True of Hyperliquid testnet and false of Alpaca paper, which is why this line is
            # now unreachable from Alpaca: paper reads the same market data as live — there is
            # no paper price — and only the *fill* is simulated. Claiming otherwise told you
            # to discount the one number that was real.
            out(f"    not enforced here: {session.network} books are mock, so this is not a "
                f"verdict on the real market")

    answer = input_fn(f"  execute on {session.network}? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        # Recorded, not just printed. Otherwise the order log answers "what did I send"
        # but not "what did I approve and then think better of", and only one of those is
        # reconstructible from the venue.
        out("  declined — approval stands, no order sent")
        session.decline(candidate, Refusal(DECLINED, "declined at the confirmation prompt"))
        return

    try:
        placement = session.execute(outcome)
    except Exception as exc:  # noqa: BLE001 - a venue error must not end the session
        out(f"  ! placement failed: {type(exc).__name__}: {exc}")
        session.decline(candidate, Refusal("error", f"{type(exc).__name__}: {exc}"))
        return

    if placement.ok:
        ids = ", ".join(str(i) for i in placement.order_ids) or "none returned"
        out(f"  + placed on {session.network} — order ids {ids}")
    else:
        # Loud, because a partially-filled bracket is the worst outcome available: an entry
        # can be resting with no stop behind it, and only a human can decide what to do.
        out(f"  ! REJECTED by the venue — {placement.error}")
        if placement.order_ids:
            out(f"    but {len(placement.order_ids)} leg(s) DID rest: "
                f"{', '.join(str(i) for i in placement.order_ids)} — check the book")
