"""Turning a night's movement into the thing that lands in the vault and the inbox.

Pure — string in, string out, nothing model-generated, the same contract ``brain.report``
states. The tests that matter are about what is *absent*: an empty section must not print, and
a night the diff could not trust must carry its warning above the rows it applies to, not
buried under them.
"""
from __future__ import annotations

from core.trigger import ARMED, NO_TRIGGER, NO_ZONE_TAG
from digest import diff, render


def _entry(key: str, **over) -> dict:
    row = {
        "key": key, "asset": over.pop("asset", "BTC"), "direction": "long",
        "score": 0.5, "entry": 110.0, "stop": 100.0, "target": 140.0, "price": 105.0,
        "reward_risk": 3.0, "zone_timeframe": "daily", "agreement": 2,
        "trigger_state": NO_ZONE_TAG,
    }
    row.update(over)
    return row


def _snap(as_of: str, rows, **over) -> dict:
    snap = {
        "run": f"{as_of}T06:20:00Z", "as_of": as_of, "score_version": 8,
        "filters": {"min_score": None, "tiers": None},
        "population": {"qualified": len(rows), "candidates": len(rows),
                       "assets_priced": 300, "assets_unpriced": 200, "duplicate_zones": 0},
        "rejections": {}, "rows": rows,
    }
    snap.update(over)
    return snap


def _quiet() -> diff.QueueDelta:
    return diff.compare(_snap("2026-08-19", [_entry("a")]), _snap("2026-08-20", [_entry("a")]))


# ── sections appear only when they have something to say ──────────────────────

def test_a_quiet_night_prints_no_empty_sections():
    body = render.markdown(_quiet())
    for heading in ("AT THE TRIGGER", "NEW IN QUEUE", "FELL OUT"):
        assert heading not in body, heading


def test_a_quiet_night_still_says_what_the_population_is():
    """Silence has to be distinguishable from a broken run. The count is the proof of life."""
    assert "1 qualified" in render.markdown(_quiet())


def test_the_trigger_section_names_the_asset_and_the_levels():
    delta = diff.compare(_snap("2026-08-19", [_entry("a", trigger_state=NO_ZONE_TAG)]),
                         _snap("2026-08-20", [_entry("a", asset="HYPE",
                                                     trigger_state=ARMED)]))
    body = render.markdown(delta)
    assert "AT THE TRIGGER" in body
    assert "HYPE" in body and "110" in body


def test_an_unexplained_departure_does_not_invent_a_reason():
    delta = diff.compare(_snap("2026-08-19", [_entry("b", asset="AVAX")]),
                         _snap("2026-08-20", []))
    body = render.markdown(delta)
    assert "AVAX" in body
    assert "no longer qualifies" in body


def test_a_decided_departure_says_what_you_did():
    delta = diff.compare(
        _snap("2026-08-19", [_entry("b", asset="GOOG")]), _snap("2026-08-20", []),
        decided={"b": {"decision": "approved", "decided_at": "2026-08-19T20:00:00Z"}})
    assert "you marked it approved" in render.markdown(delta)


# ── warnings that make a whole section untrustworthy ──────────────────────────

def test_a_moved_filter_warns_above_the_departures_it_explains():
    """Below them it reads as a footnote about something else. The reader has to meet the
    caveat before the rows it applies to, or the rows land as fact first."""
    delta = diff.compare(
        _snap("2026-08-19", [_entry("b")], filters={"min_score": None, "tiers": None}),
        _snap("2026-08-20", [], filters={"min_score": 0.4, "tiers": None}))
    body = render.markdown(delta)
    assert "min_score" in body
    assert body.index("min_score") < body.index("FELL OUT")


def test_a_score_version_bump_is_called_out():
    delta = diff.compare(_snap("2026-08-19", [_entry("a")], score_version=8),
                         _snap("2026-08-20", [_entry("a")], score_version=9))
    assert "score_version" in render.markdown(delta)


def test_the_first_run_explains_itself_rather_than_printing_empty_sections():
    body = render.markdown(diff.compare(None, _snap("2026-08-20", [_entry("a")])))
    assert "first run" in body.lower()
    assert "FELL OUT" not in body


# ── the subject line is the digest for anyone who does not open it ────────────

def test_the_subject_carries_the_counts_that_matter():
    delta = diff.compare(
        _snap("2026-08-19", [_entry("a", trigger_state=NO_ZONE_TAG), _entry("c")]),
        _snap("2026-08-20", [_entry("a", trigger_state=NO_TRIGGER),
                             _entry("b", asset="ETH")]))
    subject = render.subject(delta)
    assert "1 at trigger" in subject
    assert "1 new" in subject
    assert "1 out" in subject


def test_a_quiet_night_gets_an_explicitly_quiet_subject():
    """An empty subject reads as a broken job. Saying "quiet" is information."""
    assert "quiet" in render.subject(_quiet()).lower()


def test_the_first_run_subject_says_so():
    assert "first run" in render.subject(
        diff.compare(None, _snap("2026-08-20", [_entry("a")]))).lower()


# ── run health rides along, and only speaks up when it should ─────────────────

def test_a_clean_run_is_one_line():
    body = render.markdown(_quiet(), run={"exit": 0, "steps": [{"name": "setups",
                                                                "status": "ok"}]})
    assert "clean" in body


def test_a_failed_step_is_named():
    body = render.markdown(_quiet(), run={"exit": 2, "steps": [
        {"name": "setups", "status": "ok"}, {"name": "brain-extract", "status": "fail"}]})
    assert "brain-extract" in body


def test_spend_is_silent_until_it_approaches_the_cap():
    quiet = render.markdown(_quiet(), run={"exit": 0, "steps": [],
                                           "cost": {"xai": 0.67}}, xai_month=5.0, xai_cap=20.0)
    assert "20.00" not in quiet
    loud = render.markdown(_quiet(), run={"exit": 0, "steps": [],
                                          "cost": {"xai": 0.67}}, xai_month=18.07, xai_cap=20.0)
    assert "18.07" in loud and "20.00" in loud


# ── the stale banner and provenance ───────────────────────────────────────────

def test_a_stale_snapshot_is_announced_above_everything():
    """Rendered quietly this is a real, internally consistent diff of the WRONG night, with
    entry and stop levels, that the vault note then stamps with today's date."""
    body = render.markdown(_quiet(), stale_as_of="2026-08-20")
    assert body.startswith("STALE")
    assert "2026-08-20" in body


def test_a_stale_subject_says_nothing_else():
    """Counts from the wrong night are worse than no counts. The subject carries one fact."""
    subject = render.subject(_quiet(), stale_as_of="2026-08-20")
    assert "STALE" in subject and "quiet" not in subject


def test_the_body_always_carries_the_two_run_stamps():
    body = render.markdown(_quiet())
    assert "2026-08-19T06:20:00Z → 2026-08-20T06:20:00Z" in body


# ── problems reach the reader ─────────────────────────────────────────────────

def test_problems_print_in_the_body():
    body = render.markdown(_quiet(), problems=["warning: could not read exclusions"])
    assert "PROBLEMS" in body and "exclusions" in body


def test_problems_mark_the_subject():
    assert render.subject(_quiet(), problems=2).startswith("!!")
    assert "2 problem(s)" in render.subject(_quiet(), problems=2)


def test_a_clean_night_has_no_problems_section():
    assert "PROBLEMS" not in render.markdown(_quiet())


# ── the abstention is not printed as movement ─────────────────────────────────

def test_a_withheld_roster_never_prints_under_a_heading_claiming_movement():
    """The whole design of the abstention is undone by a heading asserting the opposite. On a
    surface read in under a minute the heading is what registers."""
    body = render.markdown(_quiet(), roster_withheld="  withheld — 40 extractions span 300 days")
    assert "ROSTER — WITHHELD" in body
    assert "ROSTER MOVED" not in body


def test_narration_and_abstention_are_different_channels():
    body = render.markdown(_quiet(), roster="  ETH went unanimous.")
    assert "ROSTER MOVED" in body and "WITHHELD" not in body


# ── the book count stays honest ───────────────────────────────────────────────

def test_the_truncation_notice_is_not_counted_as_an_event():
    """It lived in the events list once, so a capped night reported one more event than
    existed — wrong rather than absent, on the line that has to be dependable."""
    subject = render.subject(_quiet(), book=(["closed BTC", "closed ETH"], "(9 older not shown)"))
    assert "2 book" in subject


def test_the_notice_still_prints_in_the_body():
    body = render.markdown(_quiet(), book=(["closed BTC"], "WINDOW UNKNOWN — no previous run"))
    assert "WINDOW UNKNOWN" in body


# ── run health leads with the authoritative field ─────────────────────────────

def test_a_nonzero_exit_is_reported_even_when_no_step_was_flagged():
    """A step the nightly SKIPPED is absent from ``steps`` rather than present-and-failed, so a
    truncated run and a clean one both read as ``clean`` off that list alone. ``exit`` is the
    nightly's own verdict."""
    body = render.markdown(_quiet(), run={"exit": 2, "steps": [{"name": "setups",
                                                               "status": "ok"}]})
    assert "exit 2" in body
    assert "clean" not in body


def test_being_over_the_cap_reads_differently_from_being_near_it():
    """"Near the cap" printed beside a number visibly above it teaches the eye to skip both."""
    over = render.markdown(_quiet(), run={"exit": 0, "steps": []},
                           xai_month=21.41, xai_cap=20.0)
    assert "OVER the cap" in over
    near = render.markdown(_quiet(), run={"exit": 0, "steps": []},
                           xai_month=19.0, xai_cap=20.0)
    assert "near the cap" in near
