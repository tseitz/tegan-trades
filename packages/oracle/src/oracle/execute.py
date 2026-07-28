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

from execution.broker import MAINNET, dex_of
from execution.config import Config
from execution.config import load as load_config
from execution.guards import Refusal
from execution.session import Session, describe

from oracle import venue_map

# Typed rather than a keystroke. ``--network mainnet`` sits one arrow-key away from
# ``--network testnet`` in shell history, so the barrier has to be something a stray press
# cannot produce.
MAINNET_CONFIRMATION = "yes, real money"

DECLINED = "declined"


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
        config = Config(
            network=network,
            risk_pct=config.risk_pct,
            max_notional_frac=config.max_notional_frac,
            venue=config.venue,
        )
        config.validate()

    if config.network == MAINNET and not confirm_mainnet(input_fn, out):
        return None

    session = Session.open(config=config, dexs=hyperliquid_dexs(config.venue))
    out(f"  execution ON — {config.network}, risking {config.risk_pct:.2%} per trade")
    out(f"  {len(session.markets)} markets available; orders log to {session.orders_path}")
    return session


def offer(session, candidate, *, input_fn=input, out=print) -> None:
    """Offer to execute one approved candidate. Never raises past the caller.

    A failure to place must not take down a triage session — the approval is already durable
    in the decisions sidecar, and losing the remaining queue to a venue timeout would cost
    more than the order was worth.
    """
    listing = venue_map.listing(candidate.asset, session.config.venue)
    outcome = session.prepare(candidate, listing)

    if isinstance(outcome, Refusal):
        out(f"  ! not executable — {outcome.detail}")
        session.decline(candidate, outcome)
        return

    out(describe(outcome))

    # On the rehearsal venue the liquidity gate is measured but not enforced, because testnet
    # books are mock and enforcing would refuse everything while protecting nothing. Saying so
    # here is what keeps that honest — otherwise a market mainnet would never allow looks
    # perfectly healthy in rehearsal, which is the opposite of what a rehearsal is for.
    if not session.config.liquidity_enforced:
        verdict = session.liquidity_verdict(outcome)
        if verdict is not None:
            # Careful with the claim. The verdict is computed from *this* network's book, so
            # on testnet it says nothing about mainnet — xyz:SP500 has no testnet book and
            # $457M/day of real one. Phrasing it as "mainnet would refuse" would be a
            # confident falsehood, and a warning that cries wolf teaches you to skip warnings.
            out(f"  ! would fail the liquidity gate on {session.network} data — "
                f"{verdict.detail}")
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
