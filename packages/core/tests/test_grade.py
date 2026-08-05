from datetime import date
from types import SimpleNamespace

import pytest
from core.grade import (
    DEFAULT_HORIZONS,
    FLAT_BAND,
    Grade,
    Horizons,
    Pending,
    Ungradeable,
    grade,
)


class _Series:
    """Stand-in for oracle.series.PriceSeries — grade() is duck-typed, like core.rank."""

    def __init__(self, closes: dict[str, float]):
        self._closes = {date.fromisoformat(d): c for d, c in closes.items()}

    def close_on(self, when, **_):
        return self._closes.get(when)


def _row(*, direction="long", timeframe="swing", published_at="2025-01-01", asset="BTC",
         person="TraderMayne", thesis_id="t1"):
    return SimpleNamespace(
        id=thesis_id, asset=asset, person=person, direction=direction,
        timeframe=timeframe, published_at=published_at,
    )


TODAY = date(2026, 7, 24)


# ── horizons ────────────────────────────────────────────────────────────────

def test_horizon_per_timeframe():
    assert DEFAULT_HORIZONS.days_for("scalp") == 7
    assert DEFAULT_HORIZONS.days_for("swing") == 30
    assert DEFAULT_HORIZONS.days_for("position") == 180
    assert DEFAULT_HORIZONS.days_for("macro") == 365


def test_unknown_timeframe_is_ungradeable_not_defaulted():
    """Silently defaulting an unrecognized timeframe would grade a call over a window its
    author never implied."""
    result = grade(_row(timeframe="quarterly"), _Series({}), today=TODAY)
    assert isinstance(result, Ungradeable) and result.reason == "unknown_timeframe"


def test_horizons_are_overridable_for_sweeps():
    series = _Series({"2025-01-01": 100.0, "2025-01-08": 110.0})
    result = grade(_row(), series, today=TODAY, horizons=Horizons(swing=7))
    assert isinstance(result, Grade) and result.exit_date == date(2025, 1, 8)


# ── long / short / neutral arithmetic ───────────────────────────────────────

def test_long_that_rose_is_correct():
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 120.0})
    g = grade(_row(direction="long"), series, today=TODAY)
    assert isinstance(g, Grade)
    assert g.market_return == pytest.approx(0.20)
    assert g.signed_return == pytest.approx(0.20)
    assert g.correct is True


def test_short_that_fell_is_correct_and_earns_positive():
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 80.0})
    g = grade(_row(direction="short"), series, today=TODAY)
    assert g.market_return == pytest.approx(-0.20)
    assert g.signed_return == pytest.approx(0.20)
    assert g.correct is True


def test_short_that_rose_is_wrong_and_earns_negative():
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 120.0})
    g = grade(_row(direction="short"), series, today=TODAY)
    assert g.signed_return == pytest.approx(-0.20)
    assert g.correct is False


def test_neutral_is_correct_only_inside_the_flat_band():
    flat = _Series({"2025-01-01": 100.0, "2025-01-31": 100.0 * (1 + FLAT_BAND / 2)})
    moved = _Series({"2025-01-01": 100.0, "2025-01-31": 130.0})
    assert grade(_row(direction="neutral"), flat, today=TODAY).correct is True
    assert grade(_row(direction="neutral"), moved, today=TODAY).correct is False


def test_neutral_earns_nothing_either_way():
    """A neutral call is a decision *not* to take a side, so it books no directional
    return — it must not be credited with the market's move."""
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 130.0})
    assert grade(_row(direction="neutral"), series, today=TODAY).signed_return == pytest.approx(0.0)


# ── the null model rides along on every grade ───────────────────────────────

def test_null_return_is_always_long_the_same_asset():
    """Scoring compares a person against always-long on their own exact slate, so the
    null has to be captured per-call, not recomputed from a different universe later."""
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 120.0})
    g = grade(_row(direction="short"), series, today=TODAY)
    assert g.null_return == pytest.approx(0.20)
    assert g.null_correct is True


# ── pending / ungradeable ───────────────────────────────────────────────────

def test_call_whose_horizon_has_not_elapsed_is_pending():
    row = _row(timeframe="macro", published_at="2026-06-01")
    result = grade(row, _Series({}), today=TODAY)
    assert isinstance(result, Pending)
    assert result.resolves_on == date(2027, 6, 1)


def test_pending_is_decided_before_prices_are_consulted():
    """A pending call must report as pending even when the series is empty — otherwise
    'not yet resolved' gets miscounted as 'no price data'."""
    row = _row(timeframe="macro", published_at="2026-07-01")
    assert isinstance(grade(row, _Series({}), today=TODAY), Pending)


def test_missing_entry_price_is_ungradeable():
    series = _Series({"2025-01-31": 120.0})
    result = grade(_row(), series, today=TODAY)
    assert isinstance(result, Ungradeable) and result.reason == "no_entry_price"


def test_missing_exit_price_is_ungradeable():
    series = _Series({"2025-01-01": 100.0})
    result = grade(_row(), series, today=TODAY)
    assert isinstance(result, Ungradeable) and result.reason == "no_exit_price"


def test_undated_thesis_is_ungradeable():
    result = grade(_row(published_at=""), _Series({}), today=TODAY)
    assert isinstance(result, Ungradeable) and result.reason == "undated"


def test_zero_entry_price_does_not_divide_by_zero():
    series = _Series({"2025-01-01": 0.0, "2025-01-31": 5.0})
    result = grade(_row(), series, today=TODAY)
    assert isinstance(result, Ungradeable) and result.reason == "no_entry_price"


def test_missing_series_entirely_is_unpriceable():
    result = grade(_row(), None, today=TODAY)
    assert isinstance(result, Ungradeable) and result.reason == "unpriceable"


# ── leak safety ─────────────────────────────────────────────────────────────

def test_entry_is_the_publish_date_close():
    """Entering at the publish-day close is the conservative, leak-free convention: the
    call was made at or before that bar closed."""
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 120.0})
    g = grade(_row(published_at="2025-01-01"), series, today=TODAY)
    assert g.entry_date == date(2025, 1, 1) and g.entry == pytest.approx(100.0)


def test_grading_is_deterministic():
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 120.0})
    assert grade(_row(), series, today=TODAY) == grade(_row(), series, today=TODAY)


# ── benchmark-relative excess ───────────────────────────────────────────────

def test_excess_is_signed_return_minus_benchmark():
    """The headline number: did following this call beat just holding the market?"""
    asset = _Series({"2025-01-01": 100.0, "2025-01-31": 130.0})   # +30%
    bench = _Series({"2025-01-01": 50.0, "2025-01-31": 55.0})     # +10%
    g = grade(_row(direction="long"), asset, today=TODAY, benchmark=bench)
    assert g.benchmark_return == pytest.approx(0.10)
    assert g.excess_return == pytest.approx(0.20)


def test_long_that_underperforms_the_benchmark_has_negative_excess():
    asset = _Series({"2025-01-01": 100.0, "2025-01-31": 105.0})   # +5%
    bench = _Series({"2025-01-01": 50.0, "2025-01-31": 60.0})     # +20%
    g = grade(_row(direction="long"), asset, today=TODAY, benchmark=bench)
    assert g.correct is True            # it did go up, as called...
    assert g.excess_return == pytest.approx(-0.15)   # ...but holding the market beat it


def test_excess_is_none_without_a_benchmark():
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 120.0})
    g = grade(_row(), series, today=TODAY)
    assert g.benchmark_return is None and g.excess_return is None


def test_unpriceable_benchmark_degrades_to_none_without_losing_the_grade():
    """A benchmark gap must not throw away an otherwise-valid grade."""
    series = _Series({"2025-01-01": 100.0, "2025-01-31": 120.0})
    g = grade(_row(), series, today=TODAY, benchmark=_Series({}))
    assert isinstance(g, Grade) and g.excess_return is None
    assert g.signed_return == pytest.approx(0.20)


def test_pending_and_ungradeable_carry_breakdown_fields():
    """Breakdowns bucket the whole outcome stream, so every member of the sum type needs
    the descriptive fields — otherwise `--by timeframe` crashes on unresolved calls."""
    pending = grade(_row(timeframe="macro", published_at="2026-06-01", direction="short"),
                    _Series({}), today=TODAY)
    assert (pending.timeframe, pending.direction) == ("macro", "short")
    bad = grade(_row(published_at=""), _Series({}), today=TODAY)
    assert (bad.timeframe, bad.direction) == ("swing", "long")
