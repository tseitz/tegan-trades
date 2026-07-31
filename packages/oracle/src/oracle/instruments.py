"""Which corpus labels are the same tradeable instrument under two spellings.

The corpus names one instrument more than one way, and ``setups`` draws every number a human
approves — zone, stop, target — on ``OracleRef.trade_symbol``. So two labels that resolve to the
same ``(source, trade_symbol)`` produce **digit-for-digit identical candidates**: the same order
block off the same bars, offered as two separate decisions on one trade. Measured on the live
corpus 2026-07-30, three pairs were doing this::

    RUT / IWM        yahoo IWM        4 + 4 theses    (curated `tradeable`)
    EUR / EURUSD     yahoo EURUSD=X   14 + 14         (two curated rows, one symbol)
    GBP / GBPUSD     yahoo GBPUSD=X   3 + 4

Both shapes matter and only one of them involves ``tradeable``, which is why the identity here
is the resolved pair rather than the curated key. **The source is half of it**: ``LINK`` is
Chainlink on Coinbase and Interlink Electronics on Yahoo, and matching on the bare symbol is the
exact failure ``oracle.route``'s opening paragraph exists to prevent.

The consequence of merging is not only a shorter queue — it is the agreement count. Split across
two spellings, three people supporting one zone read as 2 and 2. ``core.setups.collapse`` already
folds a zone's supporters into one candidate; this supplies the label map that lets it see them
as one zone.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from oracle.route import OracleRef, RoutingTable, route


def alias_map(
    assets: Iterable[str],
    table: RoutingTable,
    *,
    venues_for: Callable[[str], list[str]],
) -> dict[str, str]:
    """``{folded label: surviving label}`` for every instrument named more than one way.

    Identity entries are deliberately absent: a caller reading this map needs to tell "this was
    folded into something else" from "this was seen", and a full map cannot say the difference.

    **The surviving label is the one that reaches the most venues**, and that rule is doing real
    work rather than breaking a tie tidily. The label *is* the key that ``cfg/venue_map.yaml`` is
    written in, so folding ``IWM`` into ``RUT`` would leave the merged row reaching Alpaca alone
    when ``IWM`` reaches Lighter and Aster as well — a dedupe that silently narrows where the
    trade can be placed. The reverse case is live too: no venue carries a Dow, so ``DJI`` (which
    has an Alpaca row, via ``tradeable: DIA``) must survive a hypothetical bare ``DIA`` that has
    none.

    Ties resolve alphabetically, not by iteration order. ``Candidate.key`` is built from the
    label, so a map that depended on the order assets arrived in would re-key decisions already
    on disk whenever the corpus grew. **Adding a pair here re-keys the folded label's own
    decisions once** — those rows now hash under the survivor — so a zone judged as ``RUT`` and
    never as ``IWM`` is asked once more. That is the cost of the merge being correct, and it is
    paid a single time per pair rather than every sitting.
    """
    instruments: dict[tuple[str, str], list[str]] = defaultdict(list)
    for asset in assets:
        resolved = route(asset, table)
        # Only a resolved reference has an instrument. A ``DerivedRef`` is computed from two
        # other series and has no ``(source, symbol)`` of its own — reading one off it is the
        # crash ``plan_fetches`` already took — and an ``Unpriceable`` shares a *reason* with
        # its neighbours, never an instrument.
        if isinstance(resolved, OracleRef):
            instruments[(resolved.source, resolved.trade_symbol)].append(asset)

    aliases = {}
    for labels in instruments.values():
        if len(labels) < 2:
            continue
        survivor, *folded = sorted(labels, key=lambda a: (-len(venues_for(a)), a))
        aliases.update({label: survivor for label in folded})
    return aliases
