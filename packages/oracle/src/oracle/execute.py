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

from execution import venues
from execution.broker import MAINNET, dex_of
from execution.config import load as load_config
from execution.guards import Refusal
from execution.session import Session, describe

from oracle import liveness, venue_map

# Typed rather than a keystroke. ``--network mainnet`` sits one arrow-key away from
# ``--network testnet`` in shell history, so the barrier has to be something a stray press
# cannot produce.
MAINNET_CONFIRMATION = "yes, real money"

DECLINED = "declined"

# Listed, correctly named, and not trading. Its own refusal code rather than ``unlisted``,
# because they call for opposite responses: an unlisted asset needs a venue that carries it,
# a dormant one is carried by a venue where nobody shows up. Defined here and not in
# ``execution.guards`` because there is no pure check behind it — the evidence is the funding
# log, which ``execution`` neither reads nor should learn about.
DORMANT = "dormant"


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


def confirm_mainnet(input_fn, out) -> bool:
    """Make real money an explicit, typed act. Returns False unless the phrase matches."""
    out("")
    out("  *** MAINNET — orders will use real funds ***")
    typed = input_fn(f'  type "{MAINNET_CONFIRMATION}" to continue: ').strip().lower()
    if typed == MAINNET_CONFIRMATION:
        return True
    out("  not confirmed — no session opened")
    return False


def open_session(*, network: str | None = None, config_path=None, input_fn=input, out=print):
    """Load config, take the mainnet barrier if needed, and connect. None if not executing.

    Every failure here lands before the first candidate is shown, mirroring
    ``setups_cli.resolve_vault_note``: a session that dies mid-triage discards the judgement
    already entered, and judgement is the scarce input.
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

    if config.network == MAINNET and not confirm_mainnet(input_fn, out):
        return None

    session = Session.open(config=config, dexs=hyperliquid_dexs(config.venue))
    out(f"  execution ON — {config.network}, risking {config.risk_pct:.2%} per trade")
    out(f"  {len(session.markets)} markets available; orders log to {session.orders_path}")
    return session


def offer(session, candidate, *, input_fn=input, out=print, is_dormant=liveness.dormant) -> None:
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
    outcome = session.prepare(candidate, listing)

    if isinstance(outcome, Refusal):
        out(f"  ! not executable — {outcome.detail}")
        session.decline(candidate, outcome)
        return

    out(describe(outcome))

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
