"""Turning a night's movement into the thing that lands in the vault and the inbox.

Pure — string in, string out, nothing model-generated, the same contract ``brain.report``
states. The tests that matter are about what is *absent*: an empty section must not print, and
a night the diff could not trust must carry its warning above the rows it applies to, not
buried under them.
"""
from __future__ import annotations

from core.trigger import ARMED, NO_TRIGGER, NO_ZONE_TAG
from digest import book as diff_book
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


def _quiet(**over) -> diff.QueueDelta:
    """A night where nothing moved, with any one section pushed back in by keyword. Lets a test
    about one section build exactly that section and nothing else."""
    from dataclasses import replace
    delta = diff.compare(_snap("2026-08-19", [_entry("a")]), _snap("2026-08-20", [_entry("a")]))
    return replace(delta, **over) if over else delta


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


def test_a_spend_total_that_did_not_move_stays_quiet():
    """`ingest-x` is off by default, so the month total freezes and the cap line fires every
    night on the same number until the month rolls over. Four identical red lines is how a
    reader learns to skip the run-health section, which is where real failures show."""
    frozen = render.markdown(_quiet(), run={"exit": 0, "steps": []},
                             xai_month=21.41, xai_cap=20.0, xai_changed=False)
    assert "OVER the cap" not in frozen
    assert "21.41" not in frozen


def test_spending_while_over_the_cap_still_speaks_up():
    """Suppression is about a number that CANNOT move, never about one that did."""
    moved = render.markdown(_quiet(), run={"exit": 0, "steps": []},
                            xai_month=21.42, xai_cap=20.0, xai_changed=True)
    assert "OVER the cap" in moved


def test_the_run_line_itself_is_never_suppressed_by_a_quiet_spend():
    """Only the spend line is conditional. Step health is the reason the section exists."""
    body = render.markdown(_quiet(), run={"exit": 1, "steps": [
        {"name": "verify-roster", "status": "warn"}]}, xai_month=21.41, xai_cap=20.0,
        xai_changed=False)
    assert "verify-roster" in body


# ── a zone moving, a departure's last state, and plain words for trigger states ──

def _row(asset="JTO", **over):
    row = {"key": over.pop("key", "k"), "asset": asset, "direction": "long", "score": 0.54,
           "entry": 0.52, "stop": 0.46, "target": 0.80, "price": 0.55, "reward_risk": 6.05,
           "zone_timeframe": "daily", "agreement": 2}
    row.update(over)
    return row


def test_a_zone_that_moved_prints_both_sides():
    """Reported as an arrival plus a departure this was the same asset in NEW IN QUEUE and in
    FELL OUT of one email — a flat contradiction on the page."""
    delta = _quiet(rolled=(diff.ZoneRoll(was=_row(entry=0.5971, reward_risk=3.93),
                                         now=_row(entry=0.5165, reward_risk=6.05)),))
    body = render.markdown(delta)
    assert "ZONE MOVED" in body
    assert "0.5971" in body and "0.5165" in body
    assert "3.93" in body and "6.05" in body


def test_a_zone_move_does_not_read_as_a_quiet_night():
    body = render.markdown(_quiet(rolled=(diff.ZoneRoll(was=_row(), now=_row()),)))
    assert "Quiet night" not in body


def test_an_unexplained_departure_carries_what_it_last_was():
    """"No longer qualifies" alone is the emptiest line in the digest. Nothing here claims a
    cause — the numbers are what the row last recorded, which is the only honest thing
    available and still tells you whether losing it mattered."""
    delta = _quiet(departed=(diff.Departure(row=_row(asset="AI", score=0.44, reward_risk=2.66),
                                            reason=diff.UNKNOWN),))
    body = render.markdown(delta)
    assert "no longer qualifies" in body
    assert "0.44" in body and "2.66" in body


def test_a_departure_with_a_real_cause_does_not_get_the_numbers():
    """The cause is the point, and it is what you would act on. Padding it with score and R:R
    buries the one word that matters."""
    delta = _quiet(departed=(diff.Departure(row=_row(asset="QCOM"), reason=diff.DECIDED,
                                            detail="you marked it approved"),))
    body = render.markdown(delta)
    assert "you marked it approved" in body
    assert "0.54" not in body


def test_a_trigger_state_is_printed_in_plain_words():
    """`no_zone_tag` is an internal enum name. It reached the inbox unchanged."""
    move = diff.TriggerMove(row=_row(), was=NO_ZONE_TAG, now=ARMED, kind=diff.TRIGGERED)
    body = render.markdown(_quiet(arrived=(move,)))
    assert "no_zone_tag" not in body
    assert "price had not reached the zone" in body


def test_an_unknown_trigger_state_is_printed_as_itself_rather_than_dropped():
    """A state this map has not been taught is still a fact about the night. Printing the raw
    value is ugly; silently printing nothing would hide that the map went stale."""
    move = diff.TriggerMove(row=_row(), was="something_new", now=ARMED, kind=diff.TRIGGERED)
    assert "something_new" in render.markdown(_quiet(arrived=(move,)))


def test_a_repeat_says_so_on_the_subject_line():
    """Two digests went out on 2026-08-25 with byte-identical subjects. Nothing in either said
    it was the second."""
    assert render.subject(_quiet(), repeat=True).startswith("again ·")


def test_a_repeat_marker_does_not_displace_the_problem_marker():
    """`!!` is the louder of the two and has to stay first, where the eye lands."""
    line = render.subject(_quiet(), repeat=True, problems=2)
    assert line.startswith("!!") and "again" in line


def test_a_stale_subject_is_not_relabelled_as_a_repeat():
    """STALE means the numbers describe the wrong night, which outranks how many times it was
    sent. Prefixing it would bury the one warning that invalidates everything below."""
    line = render.subject(_quiet(), repeat=True, stale_as_of="2026-08-20")
    assert line.startswith("STALE")


# ── what the account is holding ───────────────────────────────────────────────

def _held(asset="HOOD", **over):
    kw = {"direction": "long", "qty": 63.0, "fill_price": 87.788413, "stop": 72.29,
          "target": 117.0, "settled_at": "2026-08-05T03:10:27+00:00", "paper": True}
    kw.update(over)
    return diff_book.Holding(asset=asset, **kw)


def test_an_open_position_is_named_with_its_stop():
    """The BOOK section is a diff, so a position opened three weeks ago produces no line. On
    2026-08-26 the account held META and HOOD and no digest had ever mentioned either."""
    body = render.markdown(_quiet(), holding=(_held(),))
    assert "HOLDING" in body
    assert "HOOD" in body and "72.29" in body and "117" in body


def test_holding_nothing_prints_no_section():
    assert "HOLDING" not in render.markdown(_quiet(), holding=())


def test_a_paper_position_is_labelled():
    """A fill that never had to find a buyer reads exactly like a real one. Same rule the
    closed line follows: the flag travels with the number."""
    assert "paper" in render.markdown(_quiet(), holding=(_held(),)).lower()


def test_a_live_position_is_not_labelled_paper():
    body = render.markdown(_quiet(), holding=(_held(paper=False),))
    assert "paper" not in body.lower()


def test_a_position_missing_its_fill_still_prints_its_stop():
    """Open either way, and the stop is the number worth reading."""
    body = render.markdown(_quiet(), holding=(_held(qty=None, fill_price=None),))
    assert "HOOD" in body and "72.29" in body


def test_resting_entries_are_counted_not_listed():
    """Five orders waiting at a price is worth knowing. Listing them would double the section
    to say what `uv run book` says better."""
    body = render.markdown(_quiet(), holding=(_held(),), resting=5)
    assert "5" in body and "resting" in body


def test_no_resting_entries_adds_nothing():
    assert "resting" not in render.markdown(_quiet(), holding=(_held(),), resting=0)


# ── how old the agreement is ─────────────────────────────────────────────────

def _aged(newest_at, **over):
    return diff.compare(
        _snap("2026-08-19", []),
        _snap("2026-08-20", [_entry("a", asset="RKLB", newest_at=newest_at, **over)]))


def test_a_new_row_says_how_old_its_newest_view_is():
    """`agreement 3` reads as three people agreeing. On 2026-08-26 RKLB showed agreement 3 and
    every one of those views was months old."""
    body = render.markdown(_aged("2026-06-06"))
    assert "agreement 2" in body and "75d" in body


def test_the_age_is_measured_against_the_snapshot_not_the_clock():
    """`render` is pure and has no clock. Measuring against today would make a stale snapshot's
    ages drift every time the digest re-ran over it."""
    body = render.markdown(_aged("2026-08-18"))
    assert "2d" in body


def test_a_row_with_no_recorded_view_date_says_nothing():
    """Snapshots written before this field existed. Absent is not zero."""
    body = render.markdown(_aged(None))
    assert "0d" not in body and "d ·" not in body


def test_an_unparseable_view_date_says_nothing_rather_than_guessing():
    assert "d" not in render.markdown(_aged("not-a-date")).split("agreement 2")[1].split("\n")[0]


def test_the_trigger_section_carries_the_age_too():
    """The one section with entry and stop levels on it is the one where a dead consensus costs
    the most."""
    delta = diff.compare(
        _snap("2026-08-19", [_entry("a", trigger_state=NO_ZONE_TAG)]),
        _snap("2026-08-20", [_entry("a", asset="RKLB", trigger_state=ARMED,
                                    newest_at="2026-06-06")]))
    assert "75d" in render.markdown(delta)


# ── run health names the reason, not just the exit code ──────────────────────

def _run(exit_code, *, steps=None, reasons=None):
    row = {"run": "2026-08-27T13:29:48Z", "exit": exit_code,
           "steps": steps if steps is not None else [{"name": "setups", "status": "ok"}]}
    if reasons is not None:
        row["reasons"] = reasons
    return row


def _health(row):
    return [ln for ln in render._run_section(row, xai_month=None, xai_cap=None) if ln.strip()]


def test_a_clean_run_stays_one_line():
    assert _health(_run(0)) == ["RUN  clean · 1 steps"]


def test_a_reason_is_printed_when_no_step_carries_the_blame():
    """The 2026-08-27 shape: ingest-roster aborted on a YouTube block but exits 0, so every
    step read ``ok`` while the run exited 1 — and the mail could only say "see the log"."""
    lines = _health(_run(1, reasons=["ingest-roster — 1 item(s) failed, see log"]))
    assert lines[0] == "RUN  exit 1 · 1 steps"
    assert lines[1].strip() == "ingest-roster — 1 item(s) failed, see log"


def test_reasons_are_printed_alongside_a_flagged_step():
    """Both can be true in one night; reporting only the step would hide the other."""
    lines = _health(_run(1,
                         steps=[{"name": "verify-roster", "status": "warn"}],
                         reasons=["claude auth expired — run `claude /login`"]))
    assert "verify-roster" in lines[0]
    assert any("claude auth expired" in ln for ln in lines[1:])


def test_an_exit_without_reasons_still_admits_it_does_not_know():
    """Older rows predate the field. Silence must not read as a clean run."""
    lines = _health(_run(1))
    assert "exit 1" in lines[0]
    assert "see the log" in lines[0]
