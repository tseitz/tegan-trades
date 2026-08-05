"""Tests for the replay walk — the part of ``probe_replay`` that can be silently wrong.

The report is a display and the statistics are borrowed from ``probe_freshness_weight``, but
``walk`` encodes four things that invert a conclusion if they are backwards: which side a limit
rests on, which side a stop rests on, that the decision-day bar is excluded, and that a bar
touching both levels is refused rather than guessed. A long and a short are mirror images, so a
sign error passes every eyeball test and fails only here.

These use synthetic bars on purpose. The probe reads ``data/prices/``, which is gitignored ore
(hence ``needs_ore`` elsewhere in this repo) — a test that depended on it could only pass on
one laptop, and would be testing the corpus rather than the arithmetic.
"""
from __future__ import annotations

from datetime import date

import pytest
from oracle.series import Bar, PriceSeries
from probe_replay import (
    AMBIGUOUS,
    NOFILL,
    OPEN,
    STOP,
    TARGET,
    identity_ok,
    realized_r,
    walk,
)

DECIDED = "2026-07-01T12:00:00+00:00"


def series(*ohlc: tuple[str, float, float]) -> PriceSeries:
    """A series from ``(iso_date, low, high)`` triples; open/close sit mid-range."""
    return PriceSeries(
        symbol="TEST",
        source="test",
        bars=tuple(
            Bar(date=date.fromisoformat(d), open=(lo + hi) / 2,
                high=hi, low=lo, close=(lo + hi) / 2)
            for d, lo, hi in ohlc
        ),
    )


def row(direction: str = "long", *, entry=100.0, stop=95.0, target=120.0) -> dict:
    return {"decided_at": DECIDED, "direction": direction,
            "entry": entry, "stop": stop, "target": target}


# ── fills ───────────────────────────────────────────────────────────────────

def test_long_fills_when_price_trades_down_to_the_limit():
    result = walk(row(), series(("2026-07-02", 99.0, 105.0)))
    assert result["state"] == OPEN
    assert result["filled_on"] == date(2026, 7, 2)


def test_long_does_not_fill_while_price_stays_above_the_limit():
    assert walk(row(), series(("2026-07-02", 101.0, 110.0)))["state"] == NOFILL


def test_short_fills_when_price_trades_up_to_the_limit():
    """The mirror image, and the case a sign error passes silently."""
    short = row("short", entry=100.0, stop=105.0, target=80.0)
    result = walk(short, series(("2026-07-02", 95.0, 101.0)))
    assert result["state"] == OPEN
    assert result["filled_on"] == date(2026, 7, 2)


def test_short_does_not_fill_while_price_stays_below_the_limit():
    short = row("short", entry=100.0, stop=105.0, target=80.0)
    assert walk(short, series(("2026-07-02", 90.0, 99.0)))["state"] == NOFILL


# ── exits ───────────────────────────────────────────────────────────────────

def test_long_reaching_the_target_is_a_win():
    result = walk(row(), series(("2026-07-02", 99.0, 100.5), ("2026-07-03", 110.0, 121.0)))
    assert result["state"] == TARGET


def test_long_reaching_the_stop_is_a_loss():
    result = walk(row(), series(("2026-07-02", 99.0, 100.5), ("2026-07-03", 94.0, 99.0)))
    assert result["state"] == STOP


def test_short_exits_are_mirrored():
    short = row("short", entry=100.0, stop=105.0, target=80.0)
    bars = series(("2026-07-02", 99.0, 100.5), ("2026-07-03", 79.0, 90.0))
    assert walk(short, bars)["state"] == TARGET
    bars = series(("2026-07-02", 99.0, 100.5), ("2026-07-03", 101.0, 106.0))
    assert walk(short, bars)["state"] == STOP


def test_a_bar_touching_both_levels_is_refused_not_guessed():
    """Daily data cannot order two touches, and picking one would invent a result."""
    result = walk(row(), series(("2026-07-02", 94.0, 121.0)))
    assert result["state"] == AMBIGUOUS


# ── the two properties that are easy to lose in a refactor ──────────────────

def test_the_decision_day_bar_is_never_read():
    """It contains ticks that had not happened when the entry was chosen.

    This bar reaches the target on the decision date itself. Counting it would be look-ahead
    and would score a win that no one could have taken.
    """
    result = walk(row(), series(("2026-07-01", 94.0, 125.0)))
    assert result["state"] == OPEN
    assert result["bars"] == 0


def test_a_stop_reached_on_the_fill_bar_is_flagged():
    """The 69% result depends entirely on this flag being set on the right bar."""
    result = walk(row(), series(("2026-07-02", 94.0, 105.0)))
    assert result["state"] == STOP
    assert result["same_bar"] is True


def test_a_stop_reached_later_is_not_flagged():
    result = walk(row(), series(("2026-07-02", 99.0, 100.5), ("2026-07-03", 94.0, 99.0)))
    assert result["state"] == STOP
    assert result["same_bar"] is False


def test_max_wait_days_gives_up_on_an_unfilled_limit():
    bars = series(*[(f"2026-07-{d:02d}", 101.0, 110.0) for d in range(2, 12)])
    assert walk(row(), bars, max_wait_days=3)["state"] == NOFILL


# ── identity ────────────────────────────────────────────────────────────────

def test_identity_accepts_a_mark_from_anywhere_in_the_local_range():
    """An intraday mark is routinely off the close; that is not a wrong instrument."""
    bars = series(("2026-07-01", 95.0, 105.0))
    assert identity_ok({**row(), "price": 104.0}, bars) is True


def test_identity_rejects_a_different_order_of_magnitude():
    """^DJI at 50,000 against DIA at 500 — the only error this can usefully catch."""
    bars = series(("2026-07-01", 95.0, 105.0))
    assert identity_ok({**row(), "price": 50_000.0}, bars) is False


def test_identity_cannot_check_a_row_with_no_recorded_spot():
    """None, not False — 'not checked' must never be reported as 'checked and passed'."""
    bars = series(("2026-07-01", 95.0, 105.0))
    assert identity_ok(row(), bars) is None


def test_identity_cannot_check_when_no_bars_sit_near_the_decision():
    bars = series(("2026-01-01", 95.0, 105.0))
    assert identity_ok({**row(), "price": 100.0}, bars) is None


# ── realized R ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("state", "expected"),
    [(TARGET, 4.0), (STOP, -1.0), (AMBIGUOUS, -1.0), (OPEN, None), (NOFILL, None)],
)
def test_realized_r_charges_ambiguity_as_a_loss(state, expected):
    """Consistent with every other rate in the probe: ambiguity never flatters the result."""
    assert realized_r(row(), state) == expected
