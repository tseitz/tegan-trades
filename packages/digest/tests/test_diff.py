"""Comparing two queue snapshots.

Most of these are about *refusing to claim things*. A trigger that moved off ``None`` is not an
arrival, a departure with no knowable cause must not borrow one, and a night where the filters
moved cannot report departures as if the corpus changed. Being silent in those cases is the
feature; a digest that cries arrival is worse than no digest.
"""
from __future__ import annotations

import pytest
from core.trigger import ARMED, FIRED, NO_TRIGGER, NO_ZONE_TAG, UNREADABLE
from digest import diff


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
        "rejections": {},
        "rows": rows,
    }
    snap.update(over)
    return snap


# ── choosing what to compare against ──────────────────────────────────────────

def test_previous_nightly_skips_re_runs_from_the_same_day():
    """A day of manual ``setups`` runs leaves several same-day rows. Comparing against the last
    one answers "what changed in the last ten minutes", which is not the question."""
    snaps = [_snap("2026-08-19", []), _snap("2026-08-20", []), _snap("2026-08-20", [])]
    assert diff.previous_nightly(snaps)["as_of"] == "2026-08-19"


def test_previous_nightly_is_none_on_the_very_first_run():
    assert diff.previous_nightly([_snap("2026-08-20", [])]) is None
    assert diff.previous_nightly([]) is None


def test_current_is_the_last_snapshot_written():
    snaps = [_snap("2026-08-19", []), _snap("2026-08-20", [], run="LATEST")]
    assert diff.current(snaps)["run"] == "LATEST"


# ── entering and leaving ──────────────────────────────────────────────────────

def test_a_key_absent_last_night_has_entered():
    delta = diff.compare(_snap("2026-08-19", [_entry("a")]),
                         _snap("2026-08-20", [_entry("a"), _entry("b", asset="ETH")]))
    assert [e["key"] for e in delta.entered] == ["b"]
    assert delta.departed == ()


def test_a_key_present_last_night_and_gone_now_has_departed():
    delta = diff.compare(_snap("2026-08-19", [_entry("a"), _entry("b")]),
                         _snap("2026-08-20", [_entry("a")]))
    assert [d.row["key"] for d in delta.departed] == ["b"]
    assert delta.entered == ()


def test_a_departure_a_human_ruled_on_is_named_as_decided():
    delta = diff.compare(
        _snap("2026-08-19", [_entry("b")]), _snap("2026-08-20", []),
        decided={"b": {"decision": "approved", "decided_at": "2026-08-19T20:00:00Z"}})
    assert delta.departed[0].reason == diff.DECIDED
    assert "approved" in delta.departed[0].detail


def test_a_departure_on_an_excluded_asset_is_named_as_excluded():
    delta = diff.compare(
        _snap("2026-08-19", [_entry("b", asset="PNUT")]), _snap("2026-08-20", []),
        excluded={"PNUT": "I don't trade memecoins"})
    assert delta.departed[0].reason == diff.EXCLUDED
    assert "memecoin" in delta.departed[0].detail


def test_an_unexplained_departure_says_so_rather_than_guessing():
    """``BuildStats.rejections`` is corpus-wide and cannot be attributed to a row. Borrowing a
    reason from it would put a specific, confident, wrong cause next to a real departure."""
    delta = diff.compare(_snap("2026-08-19", [_entry("b")]), _snap("2026-08-20", []))
    assert delta.departed[0].reason == diff.UNKNOWN
    assert delta.departed[0].detail is None


def test_a_decision_older_than_the_previous_snapshot_does_not_explain_tonight():
    """The row was already decided last night and still qualified, so whatever removed it
    tonight was something else. Reusing the stale decision would mask a real departure."""
    delta = diff.compare(
        _snap("2026-08-19", [_entry("b")]), _snap("2026-08-20", []),
        decided={"b": {"decision": "later", "decided_at": "2026-08-01T00:00:00Z"}})
    assert delta.departed[0].reason == diff.UNKNOWN


# ── the trigger ───────────────────────────────────────────────────────────────

def test_price_reaching_the_zone_is_an_arrival():
    delta = diff.compare(_snap("2026-08-19", [_entry("a", trigger_state=NO_ZONE_TAG)]),
                         _snap("2026-08-20", [_entry("a", trigger_state=NO_TRIGGER)]))
    assert [m.kind for m in delta.arrived] == [diff.TAGGED]
    assert delta.arrived[0].was == NO_ZONE_TAG and delta.arrived[0].now == NO_TRIGGER


def test_a_setup_that_armed_or_fired_is_the_stronger_event():
    for state in (ARMED, FIRED):
        delta = diff.compare(_snap("2026-08-19", [_entry("a", trigger_state=NO_TRIGGER)]),
                             _snap("2026-08-20", [_entry("a", trigger_state=state)]))
        assert [m.kind for m in delta.arrived] == [diff.TRIGGERED], state


def test_a_trigger_pass_that_did_not_run_last_night_cannot_produce_an_arrival():
    """``None`` means the pass never ran — ``--no-triggers``, or no intraday series. Treating it
    as a state would announce an arrival for every candidate the first night triggers came back
    on, which is a fleet of false signals on exactly the section that must be trusted."""
    delta = diff.compare(_snap("2026-08-19", [_entry("a", trigger_state=None)]),
                         _snap("2026-08-20", [_entry("a", trigger_state=ARMED)]))
    assert delta.arrived == ()


def test_a_trigger_pass_that_did_not_run_tonight_produces_nothing():
    delta = diff.compare(_snap("2026-08-19", [_entry("a", trigger_state=NO_ZONE_TAG)]),
                         _snap("2026-08-20", [_entry("a", trigger_state=None)]))
    assert delta.arrived == ()


def test_an_unreadable_trigger_is_not_an_arrival():
    """``UNREADABLE`` is a thin instrument with no computable structure, not a setup waiting.
    ``core.trigger`` keeps it distinct from ``NO_TRIGGER`` precisely so it cannot be mistaken
    for a healthy one that has not fired yet."""
    delta = diff.compare(_snap("2026-08-19", [_entry("a", trigger_state=NO_ZONE_TAG)]),
                         _snap("2026-08-20", [_entry("a", trigger_state=UNREADABLE)]))
    assert delta.arrived == ()


def test_an_unchanged_trigger_is_not_an_event():
    delta = diff.compare(_snap("2026-08-19", [_entry("a", trigger_state=ARMED)]),
                         _snap("2026-08-20", [_entry("a", trigger_state=ARMED)]))
    assert delta.arrived == ()


def test_a_candidate_that_entered_tonight_already_armed_is_reported():
    """New *and* armed is the most actionable row the digest can carry. It has no previous
    state, so the arrival rule cannot see it — it has to come from the entry itself."""
    delta = diff.compare(_snap("2026-08-19", []),
                         _snap("2026-08-20", [_entry("a", trigger_state=ARMED)]))
    assert [m.kind for m in delta.arrived] == [diff.TRIGGERED]
    assert delta.arrived[0].was is None


# ── things that make a whole night's departures untrustworthy ─────────────────

def test_a_moved_filter_is_flagged_because_it_explains_departures_by_itself():
    """Tighten ``--min-score`` and candidates leave for a reason that has nothing to do with
    them. Every departure that night is suspect and the digest has to say so."""
    delta = diff.compare(
        _snap("2026-08-19", [_entry("b")], filters={"min_score": None, "tiers": None}),
        _snap("2026-08-20", [], filters={"min_score": 0.4, "tiers": None}))
    assert delta.filters_changed == {"min_score": (None, 0.4)}


def test_unchanged_filters_are_not_flagged():
    delta = diff.compare(_snap("2026-08-19", []), _snap("2026-08-20", []))
    assert delta.filters_changed == {}


def test_a_score_version_bump_is_flagged_because_scores_change_scale():
    """The decisions sidecar partitions on ``score_version`` for this reason. A diff that
    compared 0.61 on v8 against 0.61 on v9 would be comparing two different scales."""
    delta = diff.compare(_snap("2026-08-19", [], score_version=8),
                         _snap("2026-08-20", [], score_version=9))
    assert delta.score_version_changed == (8, 9)


# ── the corpus-wide counter ───────────────────────────────────────────────────

def test_rejection_deltas_are_reported_as_movement_not_as_totals():
    """The absolute counts are meaningless to a reader — 2170 ``weekly_disagrees`` is just how
    big the corpus is. The change is the information."""
    delta = diff.compare(_snap("2026-08-19", [], rejections={"weekly_disagrees": 2000}),
                         _snap("2026-08-20", [], rejections={"weekly_disagrees": 2170,
                                                              "price_past_stop": 11}))
    assert delta.rejection_delta == {"weekly_disagrees": 170, "price_past_stop": 11}


def test_an_unchanged_rejection_reason_is_dropped_from_the_delta():
    delta = diff.compare(_snap("2026-08-19", [], rejections={"weekly_disagrees": 2000}),
                         _snap("2026-08-20", [], rejections={"weekly_disagrees": 2000}))
    assert delta.rejection_delta == {}


# ── the first night ───────────────────────────────────────────────────────────

def test_the_first_ever_run_has_nothing_to_diff_and_says_so():
    """Not an error. The digest prints the population and waits for tomorrow."""
    delta = diff.compare(None, _snap("2026-08-20", [_entry("a")]))
    assert delta.bootstrap is True
    assert delta.entered == () and delta.departed == () and delta.arrived == ()
    assert delta.population["qualified"] == 1


# ── choosing a comparison that is actually earlier ────────────────────────────

def test_a_snapshot_from_a_later_day_is_never_taken_as_last_night():
    """``setups --as-of`` is refused a snapshot now, but this log is append-only and may already
    hold a replay row written before that guard existed. Selected as "last night", every arrival
    and departure computed against it is fiction."""
    snaps = [_snap("2026-09-01", []), _snap("2026-08-19", []), _snap("2026-08-20", [])]
    assert diff.previous_nightly(snaps)["as_of"] == "2026-08-19"


def test_an_unparseable_as_of_is_skipped_rather_than_compared():
    snaps = [_snap("nonsense", []), _snap("2026-08-19", []), _snap("2026-08-20", [])]
    assert diff.previous_nightly(snaps)["as_of"] == "2026-08-19"


# ── the guard that was defeated by its own default ────────────────────────────

def test_without_a_previous_run_stamp_no_decision_is_attributed():
    """This defaulted to the empty string, against which every decision ever recorded compares
    as newer — so one snapshot missing ``run`` made every departure claim a stale decision as
    its cause."""
    previous = _snap("2026-08-19", [_entry("b")])
    del previous["run"]
    delta = diff.compare(previous, _snap("2026-08-20", []),
                         decided={"b": {"decision": "rejected",
                                        "decided_at": "2024-01-01T00:00:00+00:00"}})
    assert delta.departed[0].reason == diff.UNKNOWN


def test_the_two_timestamp_formats_are_parsed_not_string_compared():
    """``decided_at`` is written ``+00:00`` and ``run`` is written ``Z``. Inside the same second
    those sort the wrong way round as text, which reports the stale cause."""
    delta = diff.compare(
        _snap("2026-08-19", [_entry("b")], run="2026-08-19T06:20:11Z"),
        _snap("2026-08-20", []),
        decided={"b": {"decision": "approved", "decided_at": "2026-08-19T06:20:11+00:00"}})
    # Same instant, so the decision is NOT after the snapshot and cannot explain the departure.
    assert delta.departed[0].reason == diff.UNKNOWN


# ── the type refuses to hold a contradiction ──────────────────────────────────

def test_a_named_cause_with_no_text_is_refused():
    """A reason with nothing to print, or text beside UNKNOWN, both put a confident wrong
    explanation next to a real event."""
    with pytest.raises(ValueError):
        diff.Departure(row=_entry("a"), reason=diff.EXCLUDED, detail=None)
    with pytest.raises(ValueError):
        diff.Departure(row=_entry("a"), reason=diff.UNKNOWN, detail="something")


def test_an_unexplained_departure_still_prints_something():
    assert diff.Departure(row=_entry("a"),
                          reason=diff.UNKNOWN).explanation == "no longer qualifies"


# ── new tonight is stated, not inferred ───────────────────────────────────────

def test_a_candidate_new_tonight_says_so_on_the_move():
    """``was is None`` means something DIFFERENT on the source row, where it means the trigger
    pass never ran. A renderer should not have to know which module's meaning applies."""
    delta = diff.compare(_snap("2026-08-19", []),
                         _snap("2026-08-20", [_entry("a", trigger_state=ARMED)]))
    assert delta.arrived[0].new_tonight is True


def test_a_candidate_that_was_here_last_night_is_not_new():
    delta = diff.compare(_snap("2026-08-19", [_entry("a", trigger_state=NO_ZONE_TAG)]),
                         _snap("2026-08-20", [_entry("a", trigger_state=ARMED)]))
    assert delta.arrived[0].new_tonight is False


# ── one setup rolling to a new zone is one event, not two ─────────────────────
#
# The candidate key is content-addressed on the zone — asset, direction, block date, top and
# bottom (``core.setups.Candidate.key``). So a setup that moves to a different order block
# leaves under its old key and arrives under a new one, and the digest reported both: the same
# asset in NEW IN QUEUE and in FELL OUT, in the same email, in the same direction. Read
# literally that is a contradiction, and it happened on two of nine departures the night this
# was written.

def _pair(before_rows, after_rows, **kw):
    return diff.compare(_snap("2026-08-20", before_rows), _snap("2026-08-21", after_rows), **kw)


def test_a_setup_that_moved_to_a_new_zone_is_one_event():
    delta = _pair([_entry("old", asset="JTO", entry=0.60)],
                  [_entry("new", asset="JTO", entry=0.52)])
    assert len(delta.rolled) == 1
    roll = delta.rolled[0]
    assert roll.asset == "JTO"
    assert roll.was["entry"] == 0.60 and roll.now["entry"] == 0.52


def test_a_rolled_zone_is_removed_from_both_of_the_other_lists():
    """Leaving it in either would restate the same event as a second, contradictory one."""
    delta = _pair([_entry("old", asset="JTO", entry=0.60)],
                  [_entry("new", asset="JTO", entry=0.52)])
    assert delta.entered == ()
    assert delta.departed == ()


def test_the_other_arrivals_and_departures_are_untouched(): 
    delta = _pair([_entry("old", asset="JTO"), _entry("gone", asset="AI")],
                  [_entry("new", asset="JTO", entry=0.52), _entry("fresh", asset="RKLB")])
    assert [r["asset"] for r in delta.entered] == ["RKLB"]
    assert [d.row["asset"] for d in delta.departed] == ["AI"]
    assert [r.asset for r in delta.rolled] == ["JTO"]


def test_the_same_asset_in_two_directions_is_not_a_roll():
    """A long leaving and a short arriving is a reversal, which is a different and much louder
    event than a zone moving. Pairing them would hide it."""
    delta = _pair([_entry("old", asset="JTO", direction="long")],
                  [_entry("new", asset="JTO", direction="short")])
    assert delta.rolled == ()
    assert len(delta.entered) == 1 and len(delta.departed) == 1


def test_two_zones_leaving_and_two_arriving_is_refused_rather_than_guessed():
    """Nothing says which replaced which, and inventing a pairing would print a confident wrong
    "entry moved X to Y" beside a real event — the failure this module exists to avoid."""
    delta = _pair([_entry("o1", asset="JTO", entry=0.60), _entry("o2", asset="JTO", entry=0.70)],
                  [_entry("n1", asset="JTO", entry=0.52), _entry("n2", asset="JTO", entry=0.55)])
    assert delta.rolled == ()
    assert len(delta.entered) == 2 and len(delta.departed) == 2


def test_a_departure_you_ruled_on_is_not_re_read_as_a_roll():
    """You rejecting it is the better explanation, and it is the one you would act on. A new
    zone arriving on the same asset does not undo the ruling."""
    decided = {"old": {"decision": "rejected", "decided_at": "2026-08-21T01:00:00+00:00"}}
    delta = _pair([_entry("old", asset="JTO")],
                  [_entry("new", asset="JTO", entry=0.52)], decided=decided)
    assert delta.rolled == ()
    assert delta.departed[0].detail == "you marked it rejected"
    assert len(delta.entered) == 1


def test_an_excluded_asset_is_not_re_read_as_a_roll():
    delta = _pair([_entry("old", asset="JTO")],
                  [_entry("new", asset="JTO", entry=0.52)],
                  excluded={"JTO": "I don't trade this"})
    assert delta.rolled == ()
    assert delta.departed[0].detail == "I don't trade this"


def test_a_roll_does_not_count_as_quiet():
    delta = _pair([_entry("old", asset="JTO")], [_entry("new", asset="JTO", entry=0.52)])
    assert not delta.is_quiet
