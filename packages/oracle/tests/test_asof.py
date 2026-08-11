"""``oracle.asof`` — generating at a past date, and proving nothing after it was read.

The look-ahead test is why this file exists. Everything else is ordinary unit work; that one is
a falsification, and **it was wrong twice before it was right** — which is the argument for
mutation-testing a guard rather than trusting a green tick.

*First wrong fixture:* a smooth sine-plus-drift path. Read the future or don't, the weekly
verdict was ``ranging`` either way, so the test passed with the guard deliberately removed.

*Second wrong comparison:* truncated-series against full-series. Those legitimately differ for
a reason that has nothing to do with look-ahead — ``to_weekly`` drops the trailing partial
group, so cutting at ``as_of`` also drops the last *complete* week. That is a real finding
about the engine, not a leak, and it is why ``asof`` has no truncate helper.

What works is appending a violent reversal *after* ``as_of`` and requiring the Context not to
move: ``uptrend`` at the boundary, ``downtrend_failed_breakdown`` if anything peeks.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from core.setups import build_context, cross_reference, family_of
from core.structure import BEARISH, BULLISH, trend_state
from oracle.asof import (
    ALWAYS_LONG,
    ALWAYS_SHORT,
    FLAT,
    INSUFFICIENT_HISTORY,
    RANDOM,
    TREND,
    SyntheticRow,
    grid,
    live_rows,
    random_direction,
    synthetic_rows,
    trend_direction,
    warmed,
)
from oracle.resample import to_weekly
from oracle.series import Bar, PriceSeries

START = date(2024, 1, 1)          # a Monday, so weeks align to the ISO grid

# The repo's own known-good zigzags, from ``core/tests/test_structure.py``. Reused rather than
# invented so these fixtures trend for the same reason the engine's do.
UP_LEGS = [(10, 5), (11, 6), (9, 4), (11, 6), (13, 8), (16, 9), (13, 8), (12, 7), (11, 6),
           (13, 8), (15, 10), (18, 11), (15, 10), (14, 9), (13, 8), (15, 10), (17, 12),
           (20, 13), (17, 12), (16, 11)]
DOWN_LEGS = [(30, 25), (29, 24), (34, 29), (29, 24), (27, 22), (24, 17), (26, 20), (27, 22),
             (31, 26), (26, 21), (24, 19), (21, 14), (23, 17), (24, 19), (28, 23), (23, 18),
             (21, 16), (18, 11), (20, 14), (21, 16)]


def from_weeks(weeks, *, extra_days: int = 0) -> PriceSeries:
    """One (high, low) pair per week, expanded to seven identical daily bars.

    ``to_weekly`` takes max-high and min-low across a week, so identical days give the weekly
    bar exactly the pair asked for. That is what makes *weekly* structure controllable from a
    daily fixture — the alternative is guessing at resampling and hoping.
    """
    bars, day = [], START
    for high, low in weeks:
        for _ in range(7):
            mid = (high + low) / 2
            bars.append(Bar(date=day, open=mid, high=float(high), low=float(low), close=mid))
            day += timedelta(days=1)
    high, low = weeks[-1]
    for _ in range(extra_days):
        mid = (high + low) / 2
        bars.append(Bar(date=day, open=mid, high=float(high), low=float(low), close=mid))
        day += timedelta(days=1)
    return PriceSeries(symbol="TEST", source="test", bars=tuple(bars))


UP_WEEKS = [(hi * 2, lo * 2) for hi, lo in UP_LEGS]         # scaled so the reversal is unmissable
DOWN_WEEKS = [(hi + 6, lo + 6) for hi, lo in DOWN_LEGS]     # continues down from the top

AS_OF = START + timedelta(days=len(UP_WEEKS) * 7 - 1)   # last day of the final complete week

# NEAR runs three days past AS_OF so its trailing partial week — not a complete one — is what
# ``to_weekly`` discards. FULL carries twenty further weeks of collapse.
NEAR = from_weeks(UP_WEEKS, extra_days=3)
FULL = from_weeks(UP_WEEKS + DOWN_WEEKS)


# The rule tests below are about direction, not history depth. The fixture spans 20 weeks and
# DEFAULT_WARMUP_DAYS is 270, so they pass an explicit short warmup — the guard has its own
# tests above and should not be silently re-asserted by every unrelated case.
RULE_WARMUP = 100


def context_from(series: PriceSeries, as_of: date):
    return build_context(series.bars, to_weekly(series).bars, as_of=as_of)


# ── the look-ahead guard ────────────────────────────────────────────────────

def test_no_bar_after_as_of_reaches_the_context():
    """THE test. Twenty weeks of violent reversal appended after ``as_of`` must not move a
    single field of the Context. Verified to have teeth by mutating the ``as_of`` filter out
    of ``build_context`` — it goes red, reading downtrend_failed_breakdown instead of uptrend.
    """
    assert context_from(NEAR, AS_OF) == context_from(FULL, AS_OF)


def test_the_fixture_would_actually_expose_a_leak():
    """Guards the guard. If the appended future ever stopped changing the unfiltered verdict,
    the test above would pass for the wrong reason and nobody would notice."""
    at_as_of = trend_state(to_weekly(NEAR).bars, as_of=AS_OF)
    if_leaked = trend_state(to_weekly(FULL).bars, as_of=None)
    assert at_as_of != if_leaked, "fixture no longer distinguishes filtered from unfiltered"


def test_the_gates_see_the_same_thing_through_cross_reference():
    """The Context test is the mechanism; this is the observable. Same row, same date, two
    series — the verdict must not depend on how much future was attached."""
    row = SyntheticRow(id="x", asset="TEST", person="p", direction="long",
                       timeframe="swing", published_at=AS_OF.isoformat())
    assert (cross_reference(row, context_from(NEAR, AS_OF), published_close=None)
            == cross_reference(row, context_from(FULL, AS_OF), published_close=None))


def test_no_weekly_bar_passing_the_as_of_filter_contains_a_day_after_as_of():
    """The subtlest available look-ahead, and it turns on one word in ``to_weekly``.

    A weekly bar aggregates up to seven daily bars. Stamped with the week's *start*, a week
    containing ``as_of`` would pass ``on_or_before`` while carrying days nobody could have
    seen. It is stamped ``week[-1].date``, so the stamp bounds its own contents.
    """
    weekly = to_weekly(FULL)
    daily = {b.date for b in FULL.bars}
    for offset in (60, 97, 139, 201, 264):
        as_of = START + timedelta(days=offset)
        for bar in (b for b in weekly.bars if b.date <= as_of):
            week_start = bar.date - timedelta(days=bar.date.weekday())
            constituents = [d for d in daily if week_start <= d <= bar.date]
            assert max(constituents) <= as_of, (
                f"weekly bar {bar.date} passed the as_of {as_of} filter "
                f"but contains {max(constituents)}"
            )


def test_truncating_before_resampling_loses_a_complete_week():
    """Not a leak — the opposite, and the reason ``asof`` has no truncate helper.

    ``to_weekly`` drops the trailing partial group. On a series cut at ``as_of`` the last
    COMPLETE week is that trailing group, so pre-truncating discards a week that was genuinely
    available and the engine's own filter keeps.
    """
    cut = PriceSeries(symbol="TEST", source="test",
                      bars=tuple(b for b in FULL.bars if b.date <= AS_OF))
    assert len(to_weekly(cut).bars) == len(to_weekly(NEAR).bars) - 1
    assert context_from(cut, AS_OF) != context_from(NEAR, AS_OF)


# ── live_rows ───────────────────────────────────────────────────────────────

class Row:
    def __init__(self, published_at):
        self.published_at = published_at
        self.asset = "TEST"


def test_a_row_published_on_the_as_of_date_is_knowable_that_day():
    """``as_of`` means end-of-day everywhere else in the engine; it must mean it here too."""
    assert len(live_rows([Row("2026-01-10")], date(2026, 1, 10))) == 1


def test_a_row_published_after_as_of_is_not():
    assert live_rows([Row("2026-01-11")], date(2026, 1, 10)) == []


def test_an_undated_row_is_dropped_rather_than_admitted():
    """It cannot be placed in time, and admitting it lets a view written afterwards influence
    a date before it — the same look-ahead the bars are guarded against."""
    assert live_rows([Row(""), Row(None)], date(2026, 1, 10)) == []


# ── warmup guard ────────────────────────────────────────────────────────────

def test_a_series_shorter_than_the_warmup_is_not_warmed():
    assert warmed(from_weeks(UP_WEEKS[:4]).bars, START + timedelta(days=27),
                  warmup_days=270) is False


def test_a_series_longer_than_the_warmup_is_warmed():
    assert warmed(FULL.bars, START + timedelta(days=399), warmup_days=270) is True


def test_warmup_counts_history_before_as_of_not_the_whole_series():
    """A 280-week series is not warm at its own third bar. Counting the whole series would
    pass every asset the moment the cache deepened, which is the bug this guards."""
    assert warmed(FULL.bars, START + timedelta(days=3), warmup_days=270) is False


def test_an_unwarmed_asset_refuses_by_its_own_name():
    """Not ``no_dealing_range``. A data verdict wearing a structure verdict's name is what hid
    95 short series until ``probe_price_cache`` measured them."""
    short_series = from_weeks(UP_WEEKS[:4])
    rows, skipped = synthetic_rows({"TEST": short_series}, START + timedelta(days=27),
                                   rule=TREND)
    assert rows == []
    assert skipped == {"TEST": INSUFFICIENT_HISTORY}


# ── direction rules ─────────────────────────────────────────────────────────

def test_trend_direction_agrees_with_the_gate_that_will_judge_it():
    """It must read ``family_of``, not a second copy of the bullish/bearish mapping — a null
    arm judged against a different definition than the thesis arm is not a control."""
    family = family_of(trend_state(to_weekly(FULL).bars, as_of=AS_OF))
    expected = {BULLISH: "long", BEARISH: "short"}.get(family)
    assert trend_direction(FULL, AS_OF) == expected


def test_trend_direction_reads_the_uptrend_that_was_live_at_as_of():
    """Pins the fixture's meaning: at as_of this asset is in an uptrend, and the collapse that
    follows is invisible. If this ever flips, the look-ahead tests above are testing nothing."""
    assert trend_direction(FULL, AS_OF) == "long"


def test_random_direction_is_reproducible():
    """The arm has to replay bar for bar, so it is hashed rather than drawn from a stream."""
    assert random_direction("BTC", AS_OF, seed=3) == random_direction("BTC", AS_OF, seed=3)


def test_random_direction_varies_across_seeds_and_assets():
    per_seed = {random_direction("BTC", AS_OF, seed=s) for s in range(12)}
    per_asset = {random_direction(a, AS_OF, seed=0) for a in ("BTC", "ETH", "SOL", "DOGE")}
    assert per_seed == {"long", "short"}
    assert len(per_asset) == 2


def test_random_takes_a_direction_for_every_warmed_asset():
    """Unlike TREND it never abstains, which is what gives it the same gate exposure the
    thesis arm has — including the chance to disagree with the weekly."""
    rows, skipped = synthetic_rows({"TEST": FULL}, AS_OF, rule=RANDOM, warmup_days=RULE_WARMUP)
    assert len(rows) == 1
    assert skipped == {}


def test_always_long_and_always_short_are_unconditional():
    long_rows, _ = synthetic_rows({"TEST": FULL}, AS_OF, rule=ALWAYS_LONG, warmup_days=RULE_WARMUP)
    short_rows, _ = synthetic_rows({"TEST": FULL}, AS_OF, rule=ALWAYS_SHORT, warmup_days=RULE_WARMUP)
    assert [r.direction for r in long_rows] == ["long"]
    assert [r.direction for r in short_rows] == ["short"]


def test_flat_yields_no_rows_because_it_takes_no_position():
    rows, skipped = synthetic_rows({"TEST": FULL}, AS_OF, rule=FLAT, warmup_days=RULE_WARMUP)
    assert rows == []
    assert skipped == {"TEST": "no_direction"}


def test_an_unknown_rule_raises_rather_than_silently_producing_an_empty_arm():
    """An arm that silently produced nothing would read as 'the null lost' in the report."""
    with pytest.raises(ValueError, match="unknown direction rule"):
        synthetic_rows({"TEST": FULL}, AS_OF, rule="vibes")


# ── synthetic rows are degenerate by construction, on purpose ───────────────

def test_a_synthetic_row_is_published_on_the_as_of_date():
    """So freshness is maximal in every arm and cannot explain a difference between them."""
    rows, _ = synthetic_rows({"TEST": FULL}, AS_OF, rule=ALWAYS_LONG, warmup_days=RULE_WARMUP)
    assert rows[0].published_at == AS_OF.isoformat()


def test_a_synthetic_row_carries_no_levels_so_targets_fall_back_to_structure():
    """The arm's whole point on targets: 'do their targets beat the structural level' means
    something only if the null uses the structural one."""
    rows, _ = synthetic_rows({"TEST": FULL}, AS_OF, rule=ALWAYS_LONG, warmup_days=RULE_WARMUP)
    assert rows[0].key_levels == ()


def test_rows_are_ordered_deterministically():
    rows, _ = synthetic_rows({"B": FULL, "A": FULL, "C": FULL}, AS_OF, rule=ALWAYS_LONG, warmup_days=RULE_WARMUP)
    assert [r.asset for r in rows] == ["A", "B", "C"]


# ── the grid ────────────────────────────────────────────────────────────────

def test_the_grid_is_weekly_by_default():
    assert grid(date(2025, 8, 1), date(2025, 8, 29)) == [
        date(2025, 8, 1), date(2025, 8, 8), date(2025, 8, 15),
        date(2025, 8, 22), date(2025, 8, 29),
    ]


def test_the_grid_refuses_a_zero_step_rather_than_looping_forever():
    with pytest.raises(ValueError):
        grid(date(2025, 8, 1), date(2025, 9, 1), step_days=0)
