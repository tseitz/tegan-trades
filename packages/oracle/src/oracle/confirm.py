"""Does a guessed route point at the asset the corpus meant?

``route()`` validates that a ``needs_validation`` guess resolves to **a** tradeable symbol,
never that it is **the** one, and the difference is not academic. Measured on the corpus
2026-07-31, six of 188 guesses price something else entirely: ``WTI`` is W&T Offshore rather
than crude (24.9x), ``JPY`` is a Yahoo ticker marking 37 rather than the yen (4.25x), and
``PURR``, ``ROBO``, ``RTX`` and ``STRK`` are each off by two to four orders of magnitude. Every
one of them is a real, liquid, resolvable market, which is exactly why a type check clears
them: ``fetch_cli``'s ``AUTO_OK_TYPES`` asks whether Yahoo calls it an EQUITY, and W&T Offshore
genuinely is one.

**The check is only ever evidence against, never evidence for.** ``None`` means nothing could
be asked — no venue lists the asset — and that is the common case rather than the exception:
121 of the 188 guesses are absent from every venue that can be queried, including ``CAT``,
``CVX``, ``NKE`` and most of the equity corpus. Refusing on silence would remove those 121 to
catch none, so silence passes and is counted. §32 says as much: this hardens the tradeable half
of the corpus, not the tail.

**Alpaca would close that gap and must not be used to.** It lists all 121 and its credentials
are already loaded, but the comparison would be circular — our equity close comes from Yahoo
*by ticker* and Alpaca's comes by the same ticker, so both resolve ``WTI`` to W&T Offshore and
agree. A second source that shares the error turns "unverified" into "verified", which is
strictly worse than not checking. The three perp venues are independent because they list by
the market, not by our spelling of it.

**Curated routes are not second-guessed.** A ``cfg/oracle_map.yaml`` entry was chosen by hand
against the reasoning in that file's header, and a venue disagreeing with a deliberate human
choice is a question for a human, not grounds for an automatic refusal.
"""
from __future__ import annotations

from core.identity import DIFFERS, IN_RANGE, MATCH, NO_PRICE, Comparison, compare
from oracle.route import OracleRef

# Which verdict wins when venues disagree about the same ticker. Most favourable first, and
# that direction is deliberate: Aster lists a token under `BB` at 0.015 while Hyperliquid and
# Lighter carry BlackBerry at 8.50. Taking the worst would refuse a route two independent books
# confirm, on the strength of a third venue's unrelated market having the same three letters —
# which is the collision this module exists to catch, pointed the wrong way.
_PRECEDENCE = (MATCH, IN_RANGE, DIFFERS, NO_PRICE)

# The unpriced tally's reason for an asset whose route a venue contradicts. Its own group
# rather than a flavour of "no route": the route resolved perfectly and produced a real price
# series, which is precisely what makes it dangerous — the asset was priced, graded and offered
# all along, just as something else.
CONTRADICTED = "wrong_instrument"


def check(
    ref,
    *,
    close: float | None,
    marks,
    low: float = 0.0,
    high: float = 0.0,
) -> Comparison | None:
    """The best verdict any venue offers on ``ref``, or ``None`` when none can be asked.

    ``marks`` is ``oracle.marks.index_marks`` output — ``(venue, base ticker) -> markets`` —
    so a venue quoting ``RTXUSDT`` still joins to ``RTX``. ``low``/``high`` are the asset's
    recent band, passed through to ``core.identity`` so a venue running a session ahead of our
    cached close reports as a timing gap rather than as a fault.
    """
    # A ``DerivedRef`` has no symbol of its own — `ETH/BTC` is computed from two series that
    # were each confirmed on their own account, and no venue lists the ratio.
    if not isinstance(ref, OracleRef) or not ref.needs_validation:
        return None

    ticker = ref.asset.upper()
    candidates = [
        compare(mark=mark.price, close=close, low=low, high=high)
        for (_, base), found in marks.items() if base == ticker
        for mark in found
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda c: _PRECEDENCE.index(c.verdict))
