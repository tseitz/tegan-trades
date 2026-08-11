"""``probe_evidence`` — the statistics, which are where a silent bug changes a conclusion.

The grid itself is exercised by running the probe; these cover the parts that turn rows into a
claim. Synthetic rows on purpose: a clustered interval computed over real data cannot be checked
against a known answer, and the failure mode being guarded — an interval that is too narrow — is
invisible without one.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from oracle import replay
from probe_evidence import (
    Row,
    cluster_bootstrap,
    contrast,
    direction_agreement,
    summarise,
)

DAY = date(2026, 1, 5)


def row(arm, asset, r, *, state=replay.TARGET, as_of=DAY, filled=True, resolved=True,
        same_bar=False):
    return Row(arm=arm, as_of=as_of, asset=asset, state=state, r=r,
               filled=filled, resolved=resolved, same_bar=same_bar)


# ── clustering is the whole point ───────────────────────────────────────────

def test_repeating_one_asset_does_not_narrow_the_interval():
    """THE reason this is clustered. Forty rows on one asset carry no more information than
    one; resampling rows would shrink the interval by ~sqrt(40) and report a confident number
    built from nothing. BTC alone is over a quarter of the corpus, so this is the live case."""
    concentrated = [row("A", "BTC", 1.0) for _ in range(40)] + [row("A", "ETH", -1.0)]
    lo, hi = cluster_bootstrap(concentrated, rounds=2000)
    assert hi - lo > 1.0, "two assets cannot produce a tight interval however many rows they hold"


def test_more_distinct_assets_do_narrow_it():
    """The other direction, or the test above would pass on a function that always returns a
    wide interval."""
    few = [row("A", f"X{i}", 0.5) for i in range(4)] + [row("A", "Y", -0.5)]
    many = [row("A", f"X{i}", 0.5) for i in range(40)] + [row("A", "Y", -0.5)]
    wide = cluster_bootstrap(few, rounds=2000)
    tight = cluster_bootstrap(many, rounds=2000)
    assert (tight[1] - tight[0]) < (wide[1] - wide[0])


def test_a_single_asset_yields_no_interval_rather_than_a_fake_one():
    assert cluster_bootstrap([row("A", "BTC", 1.0) for _ in range(50)]) is None


def test_the_interval_brackets_the_point_estimate():
    rows = [row("A", f"X{i}", float(i % 5) - 2) for i in range(30)]
    lo, hi = cluster_bootstrap(rows, rounds=2000)
    point = sum(r.r for r in rows) / len(rows)
    assert lo <= point <= hi


def test_the_bootstrap_is_deterministic():
    """A seeded interval, so a re-run reproduces the number a decision was made on."""
    rows = [row("A", f"X{i}", float(i % 7) - 3) for i in range(25)]
    assert cluster_bootstrap(rows, rounds=1500) == cluster_bootstrap(rows, rounds=1500)


# ── contrast: over the union, not the intersection ──────────────────────────

def test_a_contrast_counts_assets_only_one_arm_reached():
    """The bug the first version shipped with. Restricting to shared assets restricts to where
    the arms agree — and agreeing arms draw the same zone, so the difference is a literal zero.
    Here B never reaches Z, and that is a real difference between the arms."""
    rows = {
        "A": [row("A", "X", 1.0), row("A", "Z", 3.0)],
        "B": [row("B", "X", 1.0)],
    }
    out = contrast(rows, "A", "B")
    assert out is not None
    diff, _lo, _hi, n = out
    assert n == 2, "Z must be in the asset pool even though only one arm reached it"
    assert diff > 0, "A reached a winner B never saw; the contrast must see that"


def test_identical_arms_contrast_to_zero():
    rows = {
        "A": [row("A", "X", 1.0), row("A", "Y", -1.0)],
        "B": [row("B", "X", 1.0), row("B", "Y", -1.0)],
    }
    diff, lo, hi, _ = contrast(rows, "A", "B")
    assert diff == pytest.approx(0.0)
    assert lo <= 0 <= hi


def test_an_empty_arm_produces_no_contrast_rather_than_a_divide_by_zero():
    assert contrast({"A": [row("A", "X", 1.0)], "B": []}, "A", "B") is None


def test_a_contrast_needs_two_assets_to_bootstrap():
    rows = {"A": [row("A", "X", 1.0)], "B": [row("B", "X", 0.0)]}
    assert contrast(rows, "A", "B") is None


# ── overlap explains a small contrast ───────────────────────────────────────

def test_overlap_counts_date_asset_cells_both_arms_produced():
    """The diagnostic that stops a near-zero contrast being read as 'differing does not
    matter'. Where arms agree they draw the same zone and resolve identically, so overlap caps
    how large any difference can be."""
    later = DAY + timedelta(days=7)
    rows = {
        "A": [row("A", "X", 1.0), row("A", "Y", 1.0), row("A", "X", 1.0, as_of=later)],
        "B": [row("B", "X", 1.0), row("B", "Z", 1.0)],
    }
    agreed, total = direction_agreement(rows, "A", "B")
    assert agreed == 1          # only (DAY, X) is in both
    assert total == 4           # (DAY,X) (DAY,Y) (later,X) (DAY,Z)


# ── summarise ───────────────────────────────────────────────────────────────

def test_a_nofill_counts_as_a_generated_candidate_worth_zero():
    """The headline is mean R per candidate GENERATED. An entry that never traded is what the
    queue actually delivered, so it is 0R rather than a missing value — dropping it would
    flatter every arm by the share of its candidates that never filled."""
    rows = [row("A", "X", 2.0), row("A", "Y", 0.0, state=replay.NOFILL,
                                    filled=False, resolved=False)]
    s = summarise(rows)
    assert s["n"] == 2
    assert s["fill_rate"] == pytest.approx(0.5)
    assert s["mean_r"] == pytest.approx(1.0)      # (2 + 0) / 2
    assert s["mean_r_filled"] == pytest.approx(2.0)


def test_rates_are_reported_over_the_population_they_belong_to():
    """Ambiguity is a property of filled rows; fill rate is a property of all of them. Mixing
    the denominators is how a rate lies."""
    rows = [
        row("A", "X", -1.0, state=replay.AMBIGUOUS),
        row("A", "Y", 0.0, state=replay.NOFILL, filled=False, resolved=False),
    ]
    s = summarise(rows)
    assert s["fill_rate"] == pytest.approx(0.5)
    assert s["ambiguity_rate"] == pytest.approx(1.0)   # 1 of 1 FILLED row


def test_an_empty_arm_summarises_to_nothing_rather_than_zeroes():
    """Zeroes would print as a real measurement of an arm that produced nothing."""
    assert summarise([]) is None
