"""Tests for the runaway walk — the part of ``probe_stale_entries`` that can be silently wrong.

Three things here invert a conclusion if they are backwards, and none of them fails an eyeball
test: which direction "away" points (it is *toward the target*, and it mirrors between a long
and a short), that a bar which fills the entry ends the measurement rather than contributing to
it, and that the run-length rule counts *consecutive* closes rather than any two.

The sign error is the dangerous one. A long's limit rests below the market, so an unfilled bar
is one that closed *above* the entry — and a short is the exact mirror. Get it backwards and the
probe reports that entries never run away, which is the answer that kills the feature.

Synthetic bars on purpose, matching ``test_probe_replay``: the probe reads ``data/prices/``,
which is gitignored ore, so a test that depended on it would pass on one laptop and be testing
the corpus rather than the arithmetic.
"""
from __future__ import annotations

from datetime import date

import pytest
from oracle.series import Bar, PriceSeries
from probe_stale_entries import Runaway, derived_threshold, retired_at, runaway

DECIDED = "2026-07-01T12:00:00+00:00"


def go(r: dict, s: PriceSeries) -> Runaway:
    """``runaway`` with the degenerate-zone return asserted away. Every row below has a real R;
    the zero-width case has its own test against ``derived_threshold``."""
    result = runaway(r, s)
    assert result is not None
    return result


def threshold(r: dict) -> float:
    """``derived_threshold`` likewise — the ``None`` branch is tested by name, not everywhere."""
    result = derived_threshold(r)
    assert result is not None
    return result


def series(*ohlc: tuple[str, float, float, float]) -> PriceSeries:
    """A series from ``(iso_date, low, high, close)`` quadruples. Close is explicit here —
    unlike ``test_probe_replay``'s mid-range convention — because the close is the measurement."""
    return PriceSeries(
        symbol="TEST",
        source="test",
        bars=tuple(
            Bar(date=date.fromisoformat(d), open=(lo + hi) / 2, high=hi, low=lo, close=c)
            for d, lo, hi, c in ohlc
        ),
    )


def row(direction: str = "long", *, entry=100.0, stop=95.0, target=120.0) -> dict:
    """R = 5.0 for the default long, so a close at 103 is +0.6R of runaway."""
    return {"decided_at": DECIDED, "direction": direction,
            "entry": entry, "stop": stop, "target": target}


# ── direction ───────────────────────────────────────────────────────────────

def test_a_long_measures_closes_above_the_unfilled_entry():
    result = go(row(), series(("2026-07-02", 101.0, 106.0, 103.0)))
    assert result.excursions == pytest.approx([0.6])
    assert not result.filled


def test_a_short_is_the_mirror_and_measures_closes_below_its_entry():
    result = go(
        row("short", entry=100.0, stop=105.0, target=80.0),
        series(("2026-07-02", 94.0, 99.0, 97.0)),
    )
    assert result.excursions == pytest.approx([0.6])
    assert not result.filled


def test_runaway_is_denominated_in_r_not_in_price():
    """A wider stop is a wider R, so the same price move is less runaway. This is the whole
    reason the threshold can be compared across a $9 alt and a 50,000-point index."""
    wide = go(row(entry=100.0, stop=90.0), series(("2026-07-02", 101.0, 106.0, 103.0)))
    assert wide.excursions == pytest.approx([0.3])


# ── the fill ends the measurement ───────────────────────────────────────────

def test_a_bar_that_fills_the_entry_stops_the_walk():
    result = go(row(), series(
        ("2026-07-02", 101.0, 106.0, 103.0),   # unfilled, +0.6R
        ("2026-07-03", 99.0, 104.0, 102.0),    # low trades through 100 — filled here
        ("2026-07-04", 108.0, 112.0, 110.0),   # after the fill; must not be counted
    ))
    assert result.excursions == pytest.approx([0.6])
    assert result.filled and result.filled_on == date(2026, 7, 3)
    assert result.bars_before_fill == 1


def test_an_entry_filled_immediately_has_no_runaway_at_all():
    result = go(row(), series(("2026-07-02", 98.0, 104.0, 101.0)))
    assert result.excursions == ()
    assert result.filled and result.bars_before_fill == 0


def test_the_decision_day_bar_is_excluded():
    """Same look-ahead rule ``probe_replay.walk`` enforces: the sitting happened partway through
    its own session, so that bar's close was not knowable when the entry was chosen."""
    result = go(row(), series(
        ("2026-07-01", 101.0, 130.0, 125.0),   # the decision's own day — ignored entirely
        ("2026-07-02", 101.0, 106.0, 103.0),
    ))
    assert result.excursions == pytest.approx([0.6])


def test_an_unfilled_bar_cannot_close_below_a_longs_entry():
    """An invariant worth pinning rather than an edge case. A long fills when the low touches
    the entry, so a close below it implies the low was below it too — the bar filled. Every
    excursion on an unfilled bar is therefore positive, and a negative one means the fill test
    and the excursion test disagree about which side the limit rests on."""
    result = go(row(), series(
        ("2026-07-02", 101.0, 106.0, 103.0),
        ("2026-07-03", 97.0, 104.0, 98.0),     # closes below entry, so it filled
    ))
    assert all(x > 0 for x in result.excursions)
    assert result.filled


def test_a_limit_never_reached_yields_every_bar():
    result = go(row(), series(
        ("2026-07-02", 101.0, 106.0, 103.0),
        ("2026-07-03", 104.0, 109.0, 108.0),
    ))
    assert result.excursions == pytest.approx([0.6, 1.6])
    assert not result.filled and result.filled_on is None


# ── the run-length rule ─────────────────────────────────────────────────────

def test_a_single_close_beyond_the_line_does_not_retire_with_a_run_of_two():
    """The wick guard. One close past the threshold is the move this rule must not act on."""
    assert retired_at([0.7, 0.2, 0.3], 0.6, consecutive=2) is None


def test_two_consecutive_closes_beyond_the_line_retire_on_the_second():
    assert retired_at([0.2, 0.7, 0.8, 0.1], 0.6, consecutive=2) == 2


def test_the_run_must_be_consecutive_not_merely_frequent():
    assert retired_at([0.7, 0.1, 0.7, 0.1, 0.7], 0.6, consecutive=2) is None


def test_a_run_of_one_retires_on_the_first_close_beyond():
    assert retired_at([0.2, 0.7], 0.6, consecutive=1) == 1


def test_a_close_exactly_on_the_line_counts_as_beyond():
    assert retired_at([0.6, 0.6], 0.6, consecutive=2) == 1


def test_nothing_beyond_the_line_never_retires():
    assert retired_at([0.1, 0.2, 0.3], 0.6, consecutive=2) is None


# ── the derived threshold ───────────────────────────────────────────────────

def test_the_derived_threshold_is_where_a_chase_would_halve_the_advertised_rr():
    """m/(m+2), and the arithmetic it comes from: entering at spot keeps the same stop and
    target, so reward falls to (m-x)R while risk grows to (1+x)R. A 3:1 setup halves at 0.6R."""
    assert threshold(row(entry=100.0, stop=95.0, target=115.0)) == pytest.approx(0.6)


def test_a_thinner_edge_dies_sooner():
    """The property that makes this worth deriving rather than choosing: a 2:1 setup has less
    room to give away than a 5:1 one, and a flat constant would treat them identically."""
    thin = threshold(row(entry=100.0, stop=95.0, target=110.0))    # m = 2
    fat = threshold(row(entry=100.0, stop=95.0, target=125.0))     # m = 5
    assert thin == pytest.approx(0.5)
    assert fat == pytest.approx(5 / 7)
    assert thin < fat


def test_the_derived_threshold_never_reaches_one():
    """m/(m+2) < 1 for every finite m, so this can never wait for the whole target to be taken
    without a fill — the upper bound holds without being imposed."""
    assert threshold(row(entry=100.0, stop=99.9, target=1000.0)) < 1.0


def test_a_degenerate_zone_has_no_threshold_rather_than_a_wrong_one():
    """A zero-width stop makes R zero and m infinite. SILVER's 0.05% stop is the live example;
    returning None keeps it out of the sweep instead of retiring it at 1.0R."""
    assert derived_threshold(row(entry=100.0, stop=100.0, target=120.0)) is None
