from datetime import date

import pytest
from core.term import STALE_AFTER_DAYS, TERMS, is_stale, term_of

TODAY = date(2026, 11, 10)


@pytest.mark.parametrize("horizon,term", [
    ("scalp", "near"), ("swing", "near"), ("position", "long"), ("macro", "long"),
    (None, "unset"), ("", "unset"), ("decade", "unset"),
])
def test_term_of_maps_horizons_to_terms(horizon, term):
    assert term_of(horizon) == term
    assert term in TERMS


def test_a_view_is_stale_only_past_its_horizons_limit():
    assert not is_stale("2026-09-26", "swing", TODAY)   # 45 days
    assert is_stale("2026-09-25", "swing", TODAY)       # 46 days
    assert not is_stale("2026-09-25", "position", TODAY)


def test_an_unknown_horizon_uses_the_default_limit():
    assert is_stale("2026-07-01", None, TODAY)
    assert not is_stale("2026-09-01", None, TODAY)


def test_nothing_is_stale_without_a_today():
    assert not is_stale("2000-01-01", "scalp", None)


def test_an_undated_view_is_stale():
    assert is_stale(None, "macro", TODAY)


def test_limits_grow_with_the_horizon():
    assert (STALE_AFTER_DAYS["scalp"] < STALE_AFTER_DAYS["swing"]
            < STALE_AFTER_DAYS["position"] < STALE_AFTER_DAYS["macro"])
