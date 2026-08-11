"""``oracle.replay`` — the walk that turns a bracket into an outcome.

Every test here is a hand-built bar sequence, because the point of the module is the ordering
rules and those are invisible in aggregate. Bars are duck-typed on date/high/low/close.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from oracle.replay import (
    AMBIGUOUS,
    NOFILL,
    OPEN,
    OPTIMISTIC,
    STOP,
    TARGET,
    Outcome,
    mark_to_market,
    planned_r,
    resolve,
)

DECIDED = date(2026, 1, 1)


@dataclass(frozen=True)
class B:
    date: date
    high: float
    low: float
    close: float


def bars(*rows) -> list[B]:
    """(day_offset, high, low[, close]) -> bars. Close defaults to the midpoint."""
    out = []
    for row in rows:
        day, high, low = row[0], row[1], row[2]
        close = row[3] if len(row) > 3 else (high + low) / 2
        out.append(B(DECIDED + timedelta(days=day), high, low, close))
    return out


def long(bars, **kw) -> Outcome:
    """Entry 100, stop 90, target 120 — risk 10, reward 20, so a target pays exactly 2R."""
    return resolve(entry=100.0, stop=90.0, target=120.0, direction="long", bars=bars, **kw)


def short(bars, **kw) -> Outcome:
    """The mirror: stop above, target below, same 10 risk and 20 reward."""
    return resolve(entry=100.0, stop=110.0, target=80.0, direction="short", bars=bars, **kw)


# ── the terminal states ─────────────────────────────────────────────────────

def test_a_limit_never_traded_through_is_nofill_not_a_loss():
    """The quietest way for the whole pipeline to produce nothing, and it must not be
    counted as a stop — no risk was taken."""
    out = long(bars((1, 115, 105), (2, 118, 108)), from_date=DECIDED)
    assert out.state == NOFILL
    assert out.r is None


def test_a_long_fills_when_price_trades_down_to_it():
    out = long(bars((1, 105, 99), (2, 125, 118)), from_date=DECIDED)
    assert out.state == TARGET
    assert out.filled_on == DECIDED + timedelta(days=1)


def test_target_pays_the_planned_r():
    out = long(bars((1, 105, 99), (2, 125, 118)), from_date=DECIDED)
    assert out.r == pytest.approx(2.0)      # 20 reward / 10 risk


def test_a_stop_is_minus_one_r_by_construction():
    out = long(bars((1, 105, 99), (2, 101, 88)), from_date=DECIDED)
    assert out.state == STOP
    assert out.r == -1.0


def test_a_short_fills_upward_and_its_levels_invert():
    out = short(bars((1, 101, 95), (2, 90, 79)), from_date=DECIDED)
    assert out.state == TARGET
    assert out.r == pytest.approx(2.0)      # 20 reward / 10 risk


# ── ordering: what daily bars can and cannot say ────────────────────────────

def test_one_bar_touching_both_levels_is_ambiguous_not_a_win():
    """Daily OHLC cannot order two touches inside one session. Guessing the good one is how
    a replay reports an edge it never had."""
    out = long(bars((1, 105, 99), (2, 125, 88)), from_date=DECIDED)
    assert out.state == AMBIGUOUS
    assert out.r == -1.0                     # pessimistic by default


def test_ambiguity_is_a_valuation_not_a_state_change():
    """The optimistic bound must move only ``r``. If the state moved too, the count of how
    much of a result rests on the convention would be unrecoverable."""
    optimistic = long(bars((1, 105, 99), (2, 125, 88)), from_date=DECIDED,
                      ambiguity=OPTIMISTIC)
    assert optimistic.state == AMBIGUOUS
    assert optimistic.r == pytest.approx(2.0)


def test_filling_and_stopping_in_one_bar_is_a_stop_not_ambiguous():
    """A long's stop is below its entry, so price had to trade through the entry to reach it.
    Fill-then-stop is the only available ordering — this is not a coin flip."""
    out = long(bars((1, 104, 88),), from_date=DECIDED)
    assert out.state == STOP
    assert out.same_bar is True


def test_same_bar_is_flagged_because_it_indicts_the_stop_distance():
    """A stop reached inside the fill session says the stop sits within one day's noise, which
    is a statement about STOP_PAD_ATR and nothing else here would surface it."""
    inside = long(bars((1, 104, 88),), from_date=DECIDED)
    later = long(bars((1, 105, 99), (2, 101, 88)), from_date=DECIDED)
    assert inside.same_bar is True
    assert later.same_bar is False


# ── look-ahead ──────────────────────────────────────────────────────────────

def test_the_decision_days_own_bar_is_never_read():
    """That session contains ticks that had not happened when the trade was chosen. Reading it
    lets a target hit before the decision count as a win."""
    same_day = bars((0, 125, 88))            # would be an instant target on day zero
    out = long(same_day, from_date=DECIDED)
    assert out.state == OPEN
    assert out.detail == "no bars after the decision"


def test_bars_beyond_the_tail_are_not_read():
    """The tail is the measurement boundary. A target reached after it has not been observed
    yet, and counting it would be look-ahead wearing a different hat."""
    late_target = bars((1, 105, 99), (40, 130, 120))
    out = long(late_target, from_date=DECIDED, tail_days=10)
    assert out.state == OPEN


# ── open rows are valued, not dropped ───────────────────────────────────────

def test_an_open_row_is_marked_to_market_rather_than_discarded():
    """Dropping unresolved rows biases toward whatever resolves fastest, which is tight stops
    — a construction difference, not an edge."""
    out = long(bars((1, 105, 99), (2, 112, 104, 110.0)), from_date=DECIDED)
    assert out.state == OPEN
    assert out.r == pytest.approx(1.0)       # +10 on 10 of risk


def test_an_open_row_can_be_underwater():
    out = long(bars((1, 105, 99), (2, 99, 96, 95.0)), from_date=DECIDED)
    assert out.state == OPEN
    assert out.r == pytest.approx(-0.5)


def test_mark_to_market_uses_the_same_denominator_as_a_realized_r():
    """An open row and a closed one must be denominated identically or they cannot sit in the
    same mean."""
    assert mark_to_market(entry=100, stop=90, close=120, direction="long") == pytest.approx(2.0)
    assert mark_to_market(entry=100, stop=110, close=80, direction="short") == pytest.approx(2.0)


# ── degenerate brackets refuse rather than manufacture an edge ──────────────

def test_a_zero_risk_bracket_has_no_r_rather_than_an_infinite_one():
    assert planned_r(entry=100, stop=100, target=120) is None
    assert mark_to_market(entry=100, stop=100, close=120, direction="long") is None


def test_a_gap_straight_through_the_stop_still_stops():
    """Price never traded at the stop, but the bar's low is beyond it. A replay that required
    an exact touch would report this as still open forever."""
    out = long(bars((1, 105, 99), (2, 95, 60)), from_date=DECIDED)
    assert out.state == STOP


# ── fill deadline is a separate question from the tail ──────────────────────

def test_fill_within_gives_up_on_a_resting_limit():
    never = bars(*[(i, 115, 105) for i in range(1, 12)])
    out = long(never, from_date=DECIDED, fill_within=5)
    assert out.state == NOFILL
    assert out.bars == 5


def test_without_a_deadline_an_unfilled_limit_stays_nofill_with_the_whole_span_counted():
    never = bars(*[(i, 115, 105) for i in range(1, 12)])
    out = long(never, from_date=DECIDED)
    assert out.state == NOFILL
    assert out.bars == 11


def test_outcome_is_frozen_so_a_replayed_result_cannot_be_edited_after_the_fact():
    out = long(bars((1, 105, 99), (2, 125, 118)), from_date=DECIDED)
    assert isinstance(out, Outcome)
    with pytest.raises(AttributeError):
        out.state = TARGET       # type: ignore[misc]
