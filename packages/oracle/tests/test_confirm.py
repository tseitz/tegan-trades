"""Whether a guessed route points at the asset the corpus meant."""
from __future__ import annotations

from core.identity import DIFFERS, IN_RANGE, MATCH
from oracle import confirm
from oracle.marks import Mark, index_marks
from oracle.route import OracleRef


def _ref(asset, *, guessed=True):
    return OracleRef(asset=asset, source="yahoo", symbol=asset, needs_validation=guessed)


def _index(*marks):
    return index_marks(list(marks))


# ── the six this exists to catch ────────────────────────────────────────────────────────────

def test_a_contradicted_guess_is_reported():
    """Yahoo's bare WTI is W&T Offshore at 3.51; Lighter's WTI is crude at 87.40."""
    result = confirm.check(_ref("WTI"), close=3.51, marks=_index(Mark("lighter", "WTI", 87.40)))
    assert result is not None and result.verdict == DIFFERS


def test_a_confirmed_guess_passes():
    result = confirm.check(_ref("BTC"), close=64000.0,
                           marks=_index(Mark("lighter", "BTC", 64100.0)))
    assert result is not None and result.verdict == MATCH


# ── the distinction the whole gate rests on ─────────────────────────────────────────────────

def test_an_asset_no_venue_lists_is_not_a_verdict():
    """CAT, CVX and 119 others are absent from every venue that can be asked, and that is a
    permanent property of the venue rather than evidence about the route. Returning a verdict
    here would refuse 121 correct assets to catch none."""
    assert confirm.check(_ref("CAT"), close=350.0, marks=_index()) is None


def test_a_curated_route_is_never_second_guessed():
    """`oracle_map` entries are chosen by hand against the reasoning in that file's header.
    Only a guess off a transcript is what this checks — see §32."""
    curated = OracleRef(asset="OIL", source="yahoo", symbol="CL=F", curated=True)
    assert confirm.check(curated, close=81.75, marks=_index(Mark("lighter", "OIL", 0.01))) is None


# ── one venue's memecoin must not condemn a route the others confirm ────────────────────────

def test_the_most_favourable_venue_decides():
    """Aster lists a token under `BB` at 0.015 while Hyperliquid and Lighter carry BlackBerry.
    Taking the worst verdict would refuse a route two independent books confirm — the check is
    for evidence that the route is *right*, and one venue's collision is not evidence it is
    wrong."""
    result = confirm.check(_ref("BB"), close=8.495, marks=_index(
        Mark("aster", "BBUSDT", 0.0151),
        Mark("lighter", "BB", 8.50),
    ))
    assert result.verdict == MATCH


def test_a_ticker_the_venue_quotes_with_a_suffix_still_joins():
    result = confirm.check(_ref("RTX"), close=214.92,
                           marks=_index(Mark("aster", "RTXUSDT", 1.0248)))
    assert result.verdict == DIFFERS


# ── the band ────────────────────────────────────────────────────────────────────────────────

def test_the_recent_band_is_passed_through_so_a_timing_gap_is_not_a_fault():
    """187.4 against a 166.8 close is 1.12x — outside tolerance, so this is IN_RANGE only if
    the band actually reached the comparison. Asserting `!= DIFFERS` would also pass if it
    came back MATCH, which would mean the tolerance had swallowed it and the band was dead
    code."""
    result = confirm.check(_ref("BE"), close=166.8, low=160.0, high=190.0,
                           marks=_index(Mark("aster", "BEUSDT", 187.4)))
    assert result.verdict == IN_RANGE
