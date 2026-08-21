"""Parsing and formatting primitives.

Small, but load-bearing on the most-skimmed surface in the repo, and both had two copies that
had already begun to disagree with each other before this module existed.
"""
from __future__ import annotations

from datetime import UTC, datetime

from digest import fmt

# ── instant ───────────────────────────────────────────────────────────────────

def test_both_clocks_this_repo_writes_parse_to_the_same_instant():
    """``execution.store`` writes ``+00:00``; ``oracle.setups_cli`` writes ``Z``."""
    assert fmt.instant("2026-08-20T06:20:11+00:00") == fmt.instant("2026-08-20T06:20:11Z")


def test_the_two_formats_do_not_sort_as_strings():
    """The reason parsing is mandatory rather than tidy: ``Z`` (0x5A) sorts after ``+`` (0x2B),
    so a text compare mis-orders anything inside the same second."""
    assert "2026-08-20T06:20:11Z" > "2026-08-20T06:20:11+00:00"
    assert fmt.instant("2026-08-20T06:20:11Z") == fmt.instant("2026-08-20T06:20:11+00:00")


def test_a_naive_stamp_is_assumed_utc_rather_than_returned_naive():
    """Returned naive it raises ``TypeError`` at the first comparison, a long way from the
    parse that produced it."""
    parsed = fmt.instant("2026-08-20T06:20:11")
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed == datetime(2026, 8, 20, 6, 20, 11, tzinfo=UTC)


def test_unparseable_input_is_none_not_an_exception():
    for value in ("not a date", "", None, 12345, []):
        assert fmt.instant(value) is None


def test_the_undated_sentinel_compares_against_real_stamps():
    """It sorts directly against aware datetimes, so a naive sentinel would raise."""
    assert fmt.instant("2026-08-20T06:20:11Z") > fmt.UNDATED


# ── num ───────────────────────────────────────────────────────────────────────

def test_large_prices_drop_the_decimals():
    assert fmt.num(160290.0) == "160,290"


def test_ordinary_prices_keep_two_decimals():
    assert fmt.num(57.99) == "57.99"


def test_tiny_prices_keep_significant_figures():
    """A crypto price of 6.68e-08 formatted at two decimals is ``0.00`` — the whole reason one
    fixed precision is wrong for this corpus."""
    assert fmt.num(0.0000000668) == "6.68e-08"
    assert fmt.num(0.00632) == "0.00632"


def test_unrenderable_values_are_a_question_mark_not_a_crash():
    """This runs inside a nightly step, where a formatting crash costs the run's tail for a
    cosmetic line."""
    for value in (None, "x", [], {}):
        assert fmt.num(value) == "?"


def test_a_bool_is_not_treated_as_a_number():
    """``True`` is an ``int`` in Python, so an unset flag would otherwise render as ``1.00``
    where a price belongs."""
    assert fmt.num(True) == "?"


def test_negative_values_render():
    assert fmt.num(-928.0) == "-928.00"
