"""Confirming an instrument's identity from price, in the abstract.

Cases are the real ones. `scripts/probe_venue_coverage.py` found the collisions and
`cfg/oracle_map.yaml` the proxies; this file pins the arithmetic that separates them.
"""
from __future__ import annotations

import pytest

from core import identity


# ── the same instrument ─────────────────────────────────────────────────────────────────────

def test_two_prices_within_tolerance_are_one_instrument():
    result = identity.compare(mark=166.9, close=166.8)
    assert result.verdict == identity.MATCH
    assert result.confirmed is True


def test_the_ratio_is_reported_whatever_the_verdict():
    """A human reading a refusal needs the number that caused it — 10.03 is a proxy and
    4e-5 is a memecoin, and the verdict alone cannot say which."""
    assert identity.compare(mark=0.33, close=7403.0).ratio == pytest.approx(4.457e-5, rel=1e-3)


# ── the collisions this exists to catch ─────────────────────────────────────────────────────

@pytest.mark.parametrize("mark, close", [
    pytest.param(159.18, 37.27, id="JPY: the yen against a Yahoo ticker marking 37"),
    pytest.param(81.75, 3.51, id="WTI: crude against W&T Offshore, the E&P company"),
    pytest.param(0.0624, 6.24, id="PURR: our close is exactly 100x Hyperliquid's"),
])
def test_a_bare_ticker_that_resolved_to_the_wrong_thing_differs(mark, close):
    assert identity.compare(mark=mark, close=close).verdict == identity.DIFFERS


def test_a_differing_instrument_is_never_confirmed():
    assert identity.compare(mark=81.75, close=3.51).confirmed is False


# ── proxies: right instrument, declared ratio ───────────────────────────────────────────────

def test_a_declared_proxy_confirms_against_the_scaled_price():
    """SPX maps to Aster's SPYUSDT at scale 10.03 — SPY *is* the S&P, at a tenth of it. The
    comparison must apply the ratio the map already records rather than reading it as a fault."""
    result = identity.compare(mark=738.0, close=7403.0, scale=10.03)
    assert result.verdict == identity.MATCH


def test_the_same_pairing_without_its_scale_is_a_fault():
    """RUT carried IWM with no scale, and an order quoted on the index would have gone out at
    a tenth of the price. Absent the declaration there is nothing to distinguish that from a
    collision, and it must not pass."""
    assert identity.compare(mark=738.0, close=7403.0).verdict == identity.DIFFERS


def test_a_scale_of_one_is_the_same_as_none():
    assert identity.compare(mark=166.9, close=166.8, scale=1.0).verdict == identity.MATCH


# ── the timing gap, which is not a fault ────────────────────────────────────────────────────

def test_a_mark_inside_our_recent_band_is_a_timing_gap():
    """BE marked 187.4 on all three venues against our 166.8 close, and 187.4 was BE's own
    previous session — a name that moved 12% in a day. Three independent books do not share a
    collision, and refusing this would refuse honest movement."""
    result = identity.compare(mark=187.4, close=166.8, low=160.0, high=190.0)
    assert result.verdict == identity.IN_RANGE
    assert result.confirmed is False


def test_the_band_is_scaled_with_the_price():
    """Otherwise a proxy's timing gap reads as a collision: the band is in our units and the
    mark is in the venue's. 19.5 is outside tolerance of the 16.68 expected, inside the scaled
    band — and outside the raw one, so an unscaled band check would call this a fault."""
    result = identity.compare(mark=19.5, close=166.8, low=140.0, high=200.0, scale=10.0)
    assert result.verdict == identity.IN_RANGE


def test_a_mark_outside_the_band_is_still_a_fault():
    assert identity.compare(mark=3.51, close=81.75, low=79.0, high=84.0).verdict == identity.DIFFERS


# ── nothing to compare against ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("close", [None, 0.0])
def test_no_close_means_unconfirmable_not_agreement(close):
    """The distinction the gate is built on. 'We could not check' must never arrive at the
    same place as 'we checked and it agreed'."""
    result = identity.compare(mark=81.75, close=close)
    assert result.verdict == identity.NO_PRICE
    assert result.ratio is None
    assert result.confirmed is False


def test_a_venue_that_returned_no_mark_is_unconfirmable_too():
    """A mapped symbol no venue answers for reads as curated fact and would refuse at order
    time. Zero is absence, not a price near zero."""
    assert identity.compare(mark=0.0, close=81.75).verdict == identity.NO_PRICE
