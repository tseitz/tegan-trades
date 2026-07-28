from __future__ import annotations

import json
import random
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from core.canon import Registry
from core.setups import (
    DAILY,
    SCORE_VERSION,
    STRUCTURAL,
    WEEKLY,
    TIER_LARGE,
    TIER_MAJOR,
    Candidate,
    View,
)
from core.structure import BULLISH, SWING_HIGH, SWING_LOW, Break, OrderBlock, Swing

from oracle import exclusions, queue, setups_cli
from oracle.queue import build_queue
from oracle.setups_cli import format_unpriced


def _swing(price, kind, *, index=0, day=1):
    when = date(2025, 1, day)
    return Swing(date=when, price=price, kind=kind, confirmed_at=when, index=index)


def _block(*, top=110.0, bottom=100.0, invalidation=90.0, day=5, confirmed_day=6):
    """A hand-built order block, same approach as core/tests/test_setups.py — keeps these
    tests focused on the CLI plumbing rather than on re-deriving structure from bars."""
    broken = _swing(120.0, SWING_HIGH)
    origin = _swing(invalidation, SWING_LOW, index=3, day=4)
    confirmed_at = date(2025, 1, confirmed_day)
    bos = Break(kind=BULLISH, level=broken.price, swing=broken, origin=origin,
                date=confirmed_at, index=5)
    return OrderBlock(kind=BULLISH, top=top, bottom=bottom, date=date(2025, 1, day), index=4,
                      confirmed_at=confirmed_at, bos=bos, invalidation=invalidation)


def _candidate(**overrides) -> Candidate:
    block = overrides.pop("block", None) or _block()
    base = dict(
        asset="BTC", direction="long", block=block,
        entry=110.0, entry_top=110.0, entry_bottom=100.0,
        stop=100.0, invalidation=90.0,
        target=140.0, target_source=STRUCTURAL,
        reward_risk=3.0, reward_risk_from_price=3.5, approach=1.0, price=105.0,
        weekly_trend="uptrend", daily_trend="uptrend", zone="discount",
        zone_timeframe=DAILY,
        tier=TIER_MAJOR,
        freshness=1.0, trend_alignment=1.0,
        views=(View(person="Mayne", published_at="2026-07-20"),), thesis_ids=("t1",),
        score=0.5,
    )
    base.update(overrides)
    return Candidate(**base)


def _queue(candidates):
    """The candidates as an unsampled sitting. ``triage`` takes a ``Queue`` rather than a list
    because every decision records what else was on screen — see ``oracle.queue``."""
    return build_queue(candidates, limit=None)


# ── filter_candidates ────────────────────────────────────────────────────────

def test_filter_by_min_score_drops_below_threshold():
    low, high = _candidate(score=0.2), _candidate(score=0.8)
    assert setups_cli.filter_candidates([low, high], min_score=0.5) == [high]


def test_filter_by_tier_keeps_only_requested_tiers():
    major = _candidate(tier=TIER_MAJOR)
    large = _candidate(tier=TIER_LARGE)
    assert setups_cli.filter_candidates([major, large], tiers=(TIER_MAJOR,)) == [major]


def test_filtering_never_caps_however_many_qualify():
    """Two places that both truncate is how "the top N by score" became the only population
    anyone ever judged (§4). ``filter_candidates`` only drops what the run asked not to see;
    which of the survivors fit on screen is ``build_queue``'s call, and it records that it
    made one. See test_queue.py."""
    cands = [_candidate(score=0.9) for _ in range(50)]
    assert len(setups_cli.filter_candidates(cands, min_score=0.5)) == 50


def test_the_queue_is_capped_by_default_so_a_soft_gate_cannot_produce_a_wall():
    assert setups_cli._parse_args([]).limit == setups_cli.DEFAULT_LIMIT


def test_limit_zero_means_no_cap():
    """The escape hatch from the default cap — distinct from omitting the flag, which takes
    the default rather than meaning 'everything'."""
    assert setups_cli._parse_args(["--limit", "0"]).limit == 0


def test_filter_with_no_arguments_is_a_passthrough():
    cands = [_candidate(), _candidate()]
    assert setups_cli.filter_candidates(cands) == cands


# ── decision records + sidecar round trip ────────────────────────────────────

def test_decision_record_stamps_the_scoring_generation():
    """The sidecar stores the score a candidate carried when it was judged, and the whole
    point of that store is correlating decisions against scores later. A re-weighting changes
    the scale underneath those numbers, so the generation has to travel with them or the
    correlation silently compares two different things."""
    record = setups_cli.decision_record(_candidate(), setups_cli.APPROVED,
                                        decided_at="2026-07-26T00:00:00+00:00")
    assert record["score_version"] == SCORE_VERSION


def test_decision_record_carries_the_zone_timeframe():
    """The open question weekly zones were added to answer is whether they actually beat daily
    ones. That stays answerable only if every decision records which kind it judged."""
    record = setups_cli.decision_record(_candidate(zone_timeframe=WEEKLY), setups_cli.APPROVED,
                                        decided_at="2026-07-26T00:00:00+00:00")
    assert record["zone_timeframe"] == WEEKLY


def test_the_queue_labels_which_timeframe_a_zone_came_from():
    """The same asset can now appear twice, once per timeframe, and the two differ in exactly
    the numbers a glance skips. Unlabelled they read as a duplicate rather than as two setups
    with different risk."""
    weekly = setups_cli.format_candidate(_candidate(zone_timeframe=WEEKLY))
    daily = setups_cli.format_candidate(_candidate(zone_timeframe=DAILY))
    assert "weekly zone" in weekly
    assert "daily zone" in daily


def test_an_approved_note_records_which_timeframe_it_was():
    note = setups_cli.render_note(_candidate(zone_timeframe=WEEKLY), decided_on="2026-07-26")
    assert "weekly zone" in note


def test_decision_record_carries_freshness_so_it_can_be_correlated_later():
    record = setups_cli.decision_record(_candidate(freshness=0.31), setups_cli.APPROVED,
                                        decided_at="2026-07-26T00:00:00+00:00")
    assert record["freshness"] == 0.31


def test_decision_record_carries_what_else_was_on_screen():
    """A verdict is relative to the queue that produced it — measured, the 19:53 sitting
    approved at a median score of 0.484 while 18:38 *rejected* at a median of 0.518. The
    range has to travel with the row or the two look like one labelled dataset (§4)."""
    q = build_queue([_candidate(score=s) for s in (0.9, 0.5, 0.1, 0.3, 0.7)],
                    limit=3, head=1, rng=random.Random(0))
    record = setups_cli.decision_record(q.rows[0].candidate, setups_cli.APPROVED,
                                        decided_at="2026-07-28T00:00:00+00:00",
                                        queue=q.position(1))
    assert record["queue_mode"] == queue.STRATIFIED
    assert record["queue_band"] in (queue.BAND_HEAD, queue.BAND_TAIL)
    assert record["queue_rank"] == 1
    assert record["queue_size"] == 3
    assert record["queue_population"] == 5
    assert record["queue_score_min"] == q.score_min
    assert record["queue_score_max"] == q.score_max


def test_a_record_written_without_a_queue_omits_the_queue_fields_rather_than_guessing():
    """The 77 rows written before this existed genuinely have no queue context, and §4's
    do-not-backfill rule is that a re-run yields today's queue rather than the one that was
    on screen. A null would read as "recorded, and empty"; absent reads as what it is."""
    record = setups_cli.decision_record(_candidate(), setups_cli.APPROVED,
                                        decided_at="2026-07-28T00:00:00+00:00")
    assert not [k for k in record if k.startswith("queue_")]


def test_recording_the_queue_does_not_move_the_score_version():
    """These are additive fields and ``core.setups._score`` is untouched. Bumping the version
    would re-partition the sidecar and strand the v5 cohort this is meant to grow — the same
    reasoning that kept §21's carry fields at 5."""
    q = build_queue([_candidate()], limit=None)
    record = setups_cli.decision_record(q.rows[0].candidate, setups_cli.APPROVED,
                                        decided_at="2026-07-28T00:00:00+00:00",
                                        queue=q.position(1))
    assert record["score_version"] == SCORE_VERSION


def test_triage_records_the_queue_context_on_every_decision(tmp_path):
    """The wiring, not just the record builder — the sidecar is what §4 mines, so a position
    that never reaches ``append_decision`` is the same as not having built one."""
    cands = [_candidate(asset=f"A{i}", score=s) for i, s in enumerate((0.9, 0.4, 0.6))]
    q = build_queue(cands, limit=None)
    path = tmp_path / "decisions.jsonl"
    answers = iter(["a", "r", "t", "note", ""])
    setups_cli.triage(q, decisions_path=path, vault_path=None,
                      input_fn=lambda _: next(answers), out=lambda *_: None)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(rows) == 3
    assert [r["queue_rank"] for r in rows] == [1, 2, 3]
    assert {r["queue_mode"] for r in rows} == {queue.FULL}
    assert {r["queue_size"] for r in rows} == {3}
    assert {r["queue_score_min"] for r in rows} == {0.4}
    assert {r["queue_score_max"] for r in rows} == {0.9}


def test_decision_record_roundtrips_through_the_jsonl_sidecar(tmp_path):
    c = _candidate()
    record = setups_cli.decision_record(
        c, setups_cli.APPROVED, decided_at="2026-01-01T00:00:00+00:00"
    )
    path = tmp_path / "decisions.jsonl"
    setups_cli.append_decision(path, record)

    loaded = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert loaded == record
    assert loaded["candidate_key"] == c.key
    assert loaded["decision"] == setups_cli.APPROVED
    assert loaded["thesis_ids"] == list(c.thesis_ids)


def test_load_decisions_reads_back_the_whole_record(tmp_path):
    """The verdict alone isn't enough — deciding whether a deferral has become actionable needs
    the state captured at the time."""
    c = _candidate()
    path = tmp_path / "decisions.jsonl"
    setups_cli.append_decision(
        path, setups_cli.decision_record(c, setups_cli.LATER, decided_at="x")
    )
    loaded = setups_cli.load_decisions(path)
    assert loaded[c.key]["decision"] == setups_cli.LATER
    assert loaded[c.key]["agreement"] == c.agreement
    assert loaded[c.key]["inside_zone"] is True


def test_a_later_line_supersedes_an_earlier_one_for_the_same_zone(tmp_path):
    """Re-deciding a zone wins without rewriting history — the file stays append-only."""
    c = _candidate()
    path = tmp_path / "decisions.jsonl"
    for verdict in (setups_cli.LATER, setups_cli.REJECTED):
        setups_cli.append_decision(
            path, setups_cli.decision_record(c, verdict, decided_at="x")
        )
    assert setups_cli.load_decisions(path)[c.key]["decision"] == setups_cli.REJECTED


def test_load_decisions_on_missing_file_is_empty(tmp_path):
    assert setups_cli.load_decisions(tmp_path / "nope.jsonl") == {}


# ── already-decided candidates are skipped on re-runs ────────────────────────

def _decided(candidate, verdict, **overrides):
    record = setups_cli.decision_record(candidate, verdict, decided_at="x")
    record.update(overrides)
    return {candidate.key: record}


def test_a_settled_candidate_does_not_come_back():
    c = _candidate()
    for verdict in (setups_cli.APPROVED, setups_cli.REJECTED, setups_cli.ARCHIVED):
        assert setups_cli.drop_decided([c], _decided(c, verdict)) == []
    assert setups_cli.drop_decided([c], {}) == [c]


def test_a_legacy_skip_is_still_honoured_as_permanent():
    """Earlier runs recorded 'skipped', which was permanent. The sidecar is append-only and
    never rewritten, so those must not all come flooding back."""
    c = _candidate()
    assert setups_cli.drop_decided([c], _decided(c, setups_cli.SKIPPED)) == []


def test_a_deferred_candidate_stays_hidden_while_nothing_has_changed():
    c = _candidate(approach=0.4)
    assert setups_cli.drop_decided([c], _decided(c, setups_cli.LATER)) == []


def test_a_deferred_candidate_returns_once_price_reaches_the_zone():
    """The reason deferral had to become reversible: on the first live run every candidate sat
    outside its zone, so burying them permanently would discard each setup at exactly the
    moment it became actionable."""
    away = _candidate(approach=0.4)
    arrived = _candidate(approach=1.0)
    assert arrived.key == away.key       # same zone, different moment
    assert setups_cli.drop_decided([arrived], _decided(away, setups_cli.LATER)) == [arrived]


def test_a_deferred_candidate_returns_when_another_person_backs_it():
    before = _candidate(approach=0.4)
    after = _candidate(
        approach=0.4,
        views=(View(person="Mayne", published_at="2026-07-20"),
               View(person="Cowen", published_at="2026-07-19")),
    )
    assert setups_cli.drop_decided([after], _decided(before, setups_cli.LATER)) == [after]


def test_a_rejected_candidate_does_not_come_back_even_when_price_arrives():
    """Rejection is a judgment about the trade, not about its timing."""
    away = _candidate(approach=0.4)
    arrived = _candidate(approach=1.0)
    assert setups_cli.drop_decided([arrived], _decided(away, setups_cli.REJECTED)) == []


def test_two_candidates_with_different_zones_have_distinct_keys_and_are_tracked_independently():
    """Two zones differing only in price must not collide onto one decision — each is
    tracked (and can be individually already-decided) on its own key."""
    a = _candidate(block=_block(top=110.0, bottom=100.0))
    b = _candidate(block=_block(top=120.0, bottom=112.0))
    assert a.key != b.key
    assert setups_cli.drop_decided([a, b], _decided(a, setups_cli.REJECTED)) == [b]


# ── display formatter ─────────────────────────────────────────────────────────

def test_format_candidate_shows_stop_and_invalidation_as_distinct_labelled_values():
    """They answer different questions — "where is this trade wrong" vs "where does the zone
    itself die" — so when they differ they must occupy two rungs, not be blurred into one."""
    c = _candidate(stop=100.0, invalidation=90.0)
    rungs = [ln for ln in setups_cli.format_candidate(c).splitlines() if "100" in ln or "90" in ln]
    stop_rung = next(ln for ln in rungs if "stop" in ln)
    inval_rung = next(ln for ln in rungs if "invalidation" in ln)
    assert stop_rung != inval_rung
    assert "100" in stop_rung and "90" in inval_rung


def test_format_candidate_collapses_stop_and_invalidation_onto_one_rung_when_equal():
    """The far edge is often the origin swing too. Two rungs carrying the same number implies
    two distinct places the trade can be wrong, when there is only one."""
    text = setups_cli.format_candidate(_candidate(stop=100.0, invalidation=100.0))
    assert "stop = invalidation" in text
    assert len([ln for ln in text.splitlines() if "100 " in ln]) == 1


def test_format_candidate_always_shows_target_source():
    stated = setups_cli.format_candidate(_candidate(target_source="stated"))
    structural = setups_cli.format_candidate(_candidate(target_source=STRUCTURAL))
    assert "stated" in stated
    assert STRUCTURAL in structural


def test_format_candidate_shows_freshness_so_an_old_view_reads_as_one():
    """Age no longer removes a candidate, so the queue has to *show* it — otherwise a
    two-year-old call and a fresh one look identical at the moment of judgement, which is
    exactly the trade the soft gate makes."""
    text = setups_cli.format_candidate(_candidate(freshness=0.08))
    assert "freshness 0.08" in text


def test_format_candidate_flags_a_ranging_weekly_rather_than_hiding_it():
    aligned = setups_cli.format_candidate(_candidate(trend_alignment=1.0))
    ranging = setups_cli.format_candidate(_candidate(trend_alignment=0.0,
                                                    weekly_trend="ranging"))
    assert "no macro alignment" in ranging
    assert "no macro alignment" not in aligned


def test_format_candidate_shows_entry_zone_and_entry_price():
    text = setups_cli.format_candidate(_candidate(entry=110.0, entry_top=110.0, entry_bottom=100.0))
    assert "100" in text and "110" in text


# ── the price ladder ──────────────────────────────────────────────────────────

def _rungs(text: str) -> list[str]:
    """Ladder rows that carry a label — the rail-only spacer rows are not rungs."""
    return [ln for ln in text.splitlines()
            if any(w in ln for w in ("target", "entry", "stop", "invalidation", "price now"))
            and "trend" not in ln and "who" not in ln and "zone" not in ln]


def test_the_ladder_is_ordered_by_price_not_by_field_name():
    """The whole reason for a ladder: levels read in the order a chart shows them."""
    text = setups_cli.format_candidate(_candidate(
        target=140.0, entry=110.0, price=105.0, stop=100.0, invalidation=90.0))
    rungs = _rungs(text)
    order = [next(w for w in ("target", "invalidation", "stop", "entry", "price now")
                  if w in ln) for ln in rungs]
    assert order == ["target", "entry", "price now", "stop", "invalidation"]


def test_a_short_reads_correctly_without_special_casing():
    """A short's target sits *below* its stop. Sorting by price is what makes that free —
    a layout that hardcoded target-on-top would render every short upside down."""
    text = setups_cli.format_candidate(_candidate(
        direction="short", target=80.0, entry=100.0, price=102.0, stop=110.0,
        invalidation=120.0, entry_top=110.0, entry_bottom=100.0))
    rungs = _rungs(text)
    order = [next(w for w in ("target", "invalidation", "stop", "entry", "price now") if w in ln)
             for ln in rungs]
    assert order.index("invalidation") < order.index("stop") < order.index("target")


def test_each_level_carries_its_percent_move_from_entry():
    """What position sizing actually needs. Entry itself carries none — a move measured from
    entry to entry is zero by definition and printing it invites reading it as a real number."""
    text = setups_cli.format_candidate(_candidate(entry=100.0, target=150.0, stop=90.0))
    target_rung = next(ln for ln in _rungs(text) if "target" in ln)
    stop_rung = next(ln for ln in _rungs(text) if "stop" in ln)
    entry_rung = next(ln for ln in _rungs(text) if "entry" in ln)
    assert "+50.0%" in target_rung
    assert "-10.0%" in stop_rung
    assert "%" not in entry_rung


def test_prices_align_on_the_decimal_point_without_inventing_precision():
    """Right-alignment alone puts the tens column of one number under the hundredths of
    another when precision varies within a ladder — real on CL (109.47 / 97 / 88.45 / 50)."""
    text = setups_cli.format_candidate(_candidate(
        target=50.0, entry=88.45, price=89.31, stop=97.0, invalidation=109.47,
        entry_top=97.0, entry_bottom=88.45, direction="short"))
    rungs = _rungs(text)
    columns = {ln.index(".") for ln in rungs if "." in ln.strip().split()[0]}
    assert len(columns) == 1, "every decimal point sits in the same column"
    assert "97.00" not in text and " 97 " in text


def test_the_ladder_shows_where_price_actually_is():
    """``approach`` derives from price but cannot be read back as one, so without this the
    queue states where a trade is wrong but never where the market is."""
    text = setups_cli.format_candidate(_candidate(price=107.5))
    assert "107.5" in text and "price now" in text


def test_age_in_days_is_shown_beside_the_date():
    """A bare date makes the reader do subtraction to notice a zone is months old, and
    staleness is what rejections actually get written about."""
    text = setups_cli.format_candidate(
        _candidate(views=(View(person="Mayne", published_at="2026-04-23"),)),
        as_of=date(2026, 7, 26))
    assert "(94d ago)" in text


def test_age_is_omitted_rather_than_guessed_when_as_of_is_unknown():
    text = setups_cli.format_candidate(_candidate())
    assert "d ago)" not in text


def test_a_view_past_its_half_life_is_flagged_stale():
    """0.50 is where ``freshness_signal`` sits at exactly one half-life — the curve's own
    midpoint, not a threshold invented for the display."""
    assert "STALE" in setups_cli.format_candidate(_candidate(freshness=0.50))
    assert "STALE" not in setups_cli.format_candidate(_candidate(freshness=0.51))


def test_the_rank_carries_the_queue_depth():
    assert "[3/25]" in setups_cli.format_candidate(_candidate(), rank=3, total=25)
    assert "[3]" in setups_cli.format_candidate(_candidate(), rank=3)


def test_color_is_off_by_default_so_piped_output_stays_clean():
    """Escape codes in a redirected queue would corrupt every downstream reader of it."""
    assert "\033" not in setups_cli.format_candidate(_candidate(), rank=1)
    assert "\033" in setups_cli.format_candidate(_candidate(), rank=1, color=True)


def test_color_does_not_disturb_column_alignment():
    """Alignment is computed on painted strings, so measuring their raw length would push
    every coloured row out of line by exactly the width of its escape codes."""
    import re
    painted = setups_cli.format_candidate(_candidate(), rank=1, color=True)
    plain = setups_cli.format_candidate(_candidate(), rank=1)
    assert re.sub(r"\033\[[0-9;]*m", "", painted) == plain


def test_no_color_env_var_disables_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert setups_cli.supports_color() is False


# ── vault note is optional ────────────────────────────────────────────────────

def test_triage_skips_the_vault_write_when_vault_path_is_none(tmp_path):
    c = _candidate()
    decisions_path = tmp_path / "decisions.jsonl"
    answers = iter(["a"])
    counts = setups_cli.triage(
        _queue([c]), decisions_path=decisions_path, vault_path=None,
        input_fn=lambda _: next(answers), out=lambda *_: None,
    )
    assert counts[setups_cli.APPROVED] == 1
    assert setups_cli.load_decisions(decisions_path)[c.key]["decision"] == setups_cli.APPROVED
    # nothing else was written under tmp_path — no vault note file appeared anywhere
    assert list(tmp_path.iterdir()) == [decisions_path]


# ── the four verdicts actually differ ─────────────────────────────────────────

def _run(answers, candidate, tmp_path):
    it = iter(answers)
    path = tmp_path / "decisions.jsonl"
    counts = setups_cli.triage(
        _queue([candidate]), decisions_path=path, vault_path=None,
        input_fn=lambda _: next(it), out=lambda *_: None,
    )
    return counts, setups_cli.load_decisions(path).get(candidate.key)


def test_blank_input_defers_rather_than_burying(tmp_path):
    """Blank used to record a permanent skip, so hitting Enter to scroll past a setup lost it
    for good. It now falls through to the one reversible answer."""
    counts, record = _run([""], _candidate(), tmp_path)
    assert counts[setups_cli.LATER] == 1
    assert record["decision"] == setups_cli.LATER


def test_reject_records_the_reason(tmp_path):
    """'Bad trade' calibrates the setups scorer; 'their view is wrong' calibrates the roster
    trust score. Different consumers, so they must not collapse into one verdict."""
    _, trade = _run(["r", "t", ""], _candidate(), tmp_path)
    assert trade["decision"] == setups_cli.REJECTED
    assert trade["reason"] == setups_cli.REASON_TRADE

    _, view = _run(["r", "v", ""], _candidate(), tmp_path / "b")
    assert view["reason"] == setups_cli.REASON_VIEW


def test_a_one_keystroke_reason_still_gets_asked_for_a_note(tmp_path):
    """The enum says which loop to calibrate; only the note says what to change. Recorded
    verbatim — this is the field a later mining pass reads to find the actual pattern."""
    _, record = _run(["r", "t", "2.7R on a small tier isn't worth it"], _candidate(), tmp_path)
    assert record["reason"] == setups_cli.REASON_TRADE
    assert record["reason_note"] == "2.7R on a small tier isn't worth it"


def test_an_empty_note_is_omitted_rather_than_stored_blank(tmp_path):
    """Absent must be distinguishable from 'declined to say' when mining — a blank string
    would count as a note that exists and reads as nothing."""
    _, record = _run(["r", "t", "   "], _candidate(), tmp_path)
    assert "reason_note" not in record


def test_a_typed_out_reason_is_kept_as_the_note_not_discarded(tmp_path):
    """Typing a sentence at the reason prompt used to be silently thrown away — only its first
    letter survived. Anything past one character is a note, so nothing typed is ever lost."""
    _, record = _run(["r", "view is wrong, ETH broke down"], _candidate(), tmp_path)
    assert record["reason"] == setups_cli.REASON_VIEW
    assert record["reason_note"] == "view is wrong, ETH broke down"


def test_an_unrecognised_reject_reason_falls_back_to_other(tmp_path):
    _, record = _run(["r", "zzz"], _candidate(), tmp_path)
    assert record["reason"] == setups_cli.REASON_OTHER
    assert record["reason_note"] == "zzz"


def test_approve_and_later_are_not_asked_for_a_reason(tmp_path):
    """Approve and later must not consume a second answer — a stray prompt there would shift
    every subsequent keystroke onto the wrong candidate. Archive *is* asked (see below); it was
    not until 2026-07-27, and one session recorded 8 unexplained permanent suppressions."""
    for answer, verdict in (("l", setups_cli.LATER), ("a", setups_cli.APPROVED)):
        _, record = _run([answer], _candidate(), tmp_path / answer)
        assert record["decision"] == verdict
        assert "reason_note" not in record


def test_the_summary_names_every_verdict_triage_can_return(tmp_path):
    """Regression: the summary used to hand-list verdict names, drifted when the vocabulary
    changed, and was marked no-cover — so nothing caught it until it raised KeyError at the end
    of a real session. Deriving the line from the counts makes drift impossible."""
    counts = setups_cli.triage(
        _queue([]), decisions_path=tmp_path / "d.jsonl", vault_path=None,
        input_fn=lambda _: "", out=lambda *_: None,
    )
    line = setups_cli.format_counts(counts)
    assert counts, "triage must report a count per verdict"
    for verdict in counts:
        assert verdict in line


# ── archive says which kind it is ─────────────────────────────────────────────

def _run_x(answers, candidate, tmp_path, *, exclusions_path=None):
    """``_run`` with an exclusions file in play, since archive can now write one."""
    it = iter(answers)
    path = tmp_path / "decisions.jsonl"
    lines = []
    counts = setups_cli.triage(
        _queue([candidate]), decisions_path=path, vault_path=None,
        exclusions_path=exclusions_path,
        input_fn=lambda _: next(it), out=lines.append,
    )
    return counts, setups_cli.load_decisions(path).get(candidate.key), "\n".join(lines)


def test_archive_records_which_kind_of_archive_it_was(tmp_path):
    """One key was doing two jobs. Measured 2026-07-27: 8 of 25 decisions in a session were
    archives, they carried no reason at all, and they were confirmed afterwards to be a *mix*
    of "I don't trade this asset" and "this setup is stale" — which made all 8 unminable, and
    §4 mines exactly this file. The keystroke is what separates them at the moment the meaning
    is known, rather than leaving it to be guessed from prose later."""
    _, asset, _ = _run_x(["x", "a", "zero interest"], _candidate(), tmp_path / "a")
    assert asset["decision"] == setups_cli.ARCHIVED
    assert asset["reason"] == setups_cli.ARCHIVE_ASSET
    assert asset["reason_note"] == "zero interest"

    _, setup, _ = _run_x(["x", "s", "this zone is done"], _candidate(), tmp_path / "s")
    assert setup["reason"] == setups_cli.ARCHIVE_SETUP


def test_an_asset_archive_writes_the_exclusion_that_makes_it_stick(tmp_path):
    """The point of asking. `drop_decided` keys on the *zone*, so an asset-level archive buries
    one order block and the next to form on the same instrument asks again — OIL and CL came
    back on 4 of 59 rows the same day they were rejected. Only cfg/exclusions.yaml makes an
    asset-level "no" permanent."""
    excl = tmp_path / "exclusions.yaml"
    _, record, printed = _run_x(["x", "a", "zero interest"], _candidate(asset="PNUT"),
                                tmp_path, exclusions_path=excl)
    assert exclusions.load(excl) == {"PNUT": "zero interest"}
    assert record["reason"] == setups_cli.ARCHIVE_ASSET
    assert "PNUT" in printed and "exclusions.yaml" in printed


def test_a_setup_archive_never_touches_the_exclusions_file(tmp_path):
    """"Just this zone" is the conservative half and must stay conservative — silently
    excluding a whole market from one ambiguous keystroke is the failure the exclusions header
    warns about at length."""
    excl = tmp_path / "exclusions.yaml"
    _run_x(["x", "s", "done with this zone"], _candidate(asset="PNUT"), tmp_path,
           exclusions_path=excl)
    assert not excl.exists()


def test_an_unrecognised_archive_kind_falls_back_to_the_setup_only_half(tmp_path):
    """Ambiguity must resolve toward the reversible-ish answer. Guessing "asset" from a stray
    keystroke would delete a market from the queue permanently."""
    excl = tmp_path / "exclusions.yaml"
    _, record, _ = _run_x(["x", "zzz"], _candidate(asset="PNUT"), tmp_path,
                          exclusions_path=excl)
    assert record["reason"] == setups_cli.ARCHIVE_SETUP
    assert record["reason_note"] == "zzz"
    assert not excl.exists()


def test_an_asset_archive_with_no_reason_records_the_decision_but_writes_no_rule(tmp_path):
    """`exclusions.load` refuses a reason-less entry, so writing one would make the next run
    unstartable. The judgement still reaches the sidecar — losing a config line is recoverable,
    losing the decision is not — and the skip is announced rather than silent."""
    excl = tmp_path / "exclusions.yaml"
    _, record, printed = _run_x(["x", "a", "   "], _candidate(asset="PNUT"), tmp_path,
                                exclusions_path=excl)
    assert record["reason"] == setups_cli.ARCHIVE_ASSET
    assert not excl.exists()
    assert "no reason" in printed.lower()


def test_archiving_an_already_excluded_asset_says_so_and_changes_nothing(tmp_path):
    """Ordinary across sessions. The committed reason wins — it is the reviewed one."""
    excl = tmp_path / "exclusions.yaml"
    excl.write_text('assets:\n  PNUT: "the original reason"\n')
    _, _, printed = _run_x(["x", "a", "a newer reason"], _candidate(asset="PNUT"), tmp_path,
                           exclusions_path=excl)
    assert exclusions.load(excl) == {"PNUT": "the original reason"}
    assert "already" in printed.lower()


def test_a_failed_exclusion_write_does_not_lose_the_decision(tmp_path):
    """The sidecar write must not be hostage to the config write. Archive is judgement; the
    exclusion is a convenience that makes it stick."""
    excl = tmp_path / "nope" / "exclusions.yaml"  # parent does not exist
    counts, record, printed = _run_x(["x", "a", "zero interest"], _candidate(asset="PNUT"),
                                     tmp_path, exclusions_path=excl)
    assert counts[setups_cli.ARCHIVED] == 1
    assert record["decision"] == setups_cli.ARCHIVED
    assert record["reason"] == setups_cli.ARCHIVE_ASSET
    assert "exclusions.yaml" in printed


def test_archive_still_works_with_no_exclusions_path_configured(tmp_path):
    """`triage` is called directly by tests and could be by anything else; the exclusions path
    is optional and its absence must not turn an archive into a crash."""
    _, record, _ = _run_x(["x", "a", "zero interest"], _candidate(), tmp_path)
    assert record["decision"] == setups_cli.ARCHIVED
    assert record["reason"] == setups_cli.ARCHIVE_ASSET


# ── empty queue and quit ──────────────────────────────────────────────────────

def test_triage_on_an_empty_candidate_list_exits_without_prompting(tmp_path):
    def _boom(_):
        raise AssertionError("must not prompt when there is nothing to review")

    counts = setups_cli.triage(
        _queue([]), decisions_path=tmp_path / "decisions.jsonl", vault_path=None,
        input_fn=_boom, out=lambda *_: None,
    )
    assert counts == {setups_cli.APPROVED: 0, setups_cli.LATER: 0,
                      setups_cli.REJECTED: 0, setups_cli.ARCHIVED: 0}


def test_triage_quit_stops_immediately_without_consuming_further_input(tmp_path):
    c1 = _candidate()
    c2 = _candidate(block=_block(top=120.0, bottom=112.0))
    answers = iter(["q"])

    def _input(_):
        try:
            return next(answers)
        except StopIteration:  # pragma: no cover - failure path
            raise AssertionError("quit must stop before consuming further input")

    counts = setups_cli.triage(
        _queue([c1, c2]), decisions_path=tmp_path / "decisions.jsonl", vault_path=None,
        input_fn=_input, out=lambda *_: None,
    )
    assert counts == {setups_cli.APPROVED: 0, setups_cli.LATER: 0,
                      setups_cli.REJECTED: 0, setups_cli.ARCHIVED: 0}


# ── the unpriced tally reports groups, not one number ────────────────────────

def _stats(**overrides) -> setups_cli.BuildStats:
    base = dict(assets_total=0, assets_priced=0, unpriceable=Counter(),
                assets_uncached=0, assets_no_context=0, rejections=Counter(),
                candidate_count=0)
    base.update(overrides)
    return setups_cli.BuildStats(**base)


def test_the_unpriced_line_separates_the_gap_from_the_things_that_are_not_assets():
    """One number read as "183 missed opportunities" and was mostly nothing of the kind: the
    `__basket__` sentinel alone was 53 rows of the headline while being, by construction, the
    extractor's placeholder for a thesis that isn't about one thing. The groups have different
    answers — `not an instrument` never needs fixing, `computable` is the actual backlog."""
    line = format_unpriced(_stats(unpriceable=Counter(
        {"basket": 21, "conflict": 75, "unmapped": 41, "rate": 7,
         "dominance_metric": 6, "derived_ratio": 2, "event": 3, "private_company": 2,
         "macro": 1})))
    assert "computable 15" in line
    assert "no route 116" in line
    assert "not an instrument 27" in line


def test_a_routed_asset_that_was_never_fetched_is_not_a_routing_failure():
    """Opposite problems with opposite fixes: one wants a curation entry, the other wants
    `fetch-prices`. They were the same counter, so neither was actionable."""
    line = format_unpriced(_stats(unpriceable=Counter({"conflict": 3}), assets_uncached=25))
    assert "no route 3" in line
    assert "routed but never fetched 25" in line


def test_a_reason_no_group_claims_is_printed_rather_than_dropped():
    """`event` and `derived_ratio` were spelled only in oracle_map.yaml and never in route.py,
    which is exactly how they stayed invisible. A new one must not vanish the same way."""
    line = format_unpriced(_stats(unpriceable=Counter({"newly_invented_reason": 4})))
    assert "ungrouped newly_invented_reason 4" in line


def test_the_headline_still_counts_everything_that_never_reached_a_context():
    stats = _stats(unpriceable=Counter({"basket": 21, "conflict": 75}), assets_uncached=25)
    assert stats.assets_unpriced == 121


# ── build_candidates: engine assembly stats, without touching real data ──────

def test_build_candidates_with_no_rows_reports_zero_everything(tmp_path):
    registry = Registry()
    candidates, stats = setups_cli.build_candidates(
        [], registry, as_of=date(2026, 1, 1),
        listings_map={"coinbase": [], "kraken": []}, config_dir=tmp_path,
    )
    assert candidates == ()
    assert stats.assets_total == 0
    assert stats.assets_priced == 0
    assert stats.assets_unpriced == 0
    assert stats.candidate_count == 0


# ── default vault note ────────────────────────────────────────────────────────

def test_default_vault_note_is_the_running_setups_file_under_the_home_vault():
    """Hardcoding an absolute /Users/<name> path (as triage_cli does) breaks on any other
    machine; deriving from Path.home() is what makes the default portable."""
    default = setups_cli.DEFAULT_VAULT_NOTE
    assert default == Path.home() / "vault" / "Trading" / "Trade Logs" / "Setups.md"
    assert default.is_relative_to(Path.home())
    assert default.name == "Setups.md"


def test_render_note_heading_carries_the_approval_date():
    """Two approvals of the same asset must not render as identical sections."""
    c = _candidate(asset="ZEC", direction="long")
    first = setups_cli.render_note(c, decided_on="2026-07-25")
    second = setups_cli.render_note(c, decided_on="2026-08-14")
    assert first.splitlines()[0] == (
        "## 2026-07-25 · ZEC long · daily zone · tier major · score 0.50"
    )
    assert second.splitlines()[0].startswith("## 2026-08-14 · ZEC long")
    assert first != second


def test_approval_writes_a_dated_section_to_the_vault_note(tmp_path):
    note = tmp_path / "Setups.md"
    answers = iter(["a"])
    setups_cli.triage(
        _queue([_candidate(asset="ZEC")]), decisions_path=tmp_path / "d.jsonl", vault_path=note,
        input_fn=lambda _: next(answers), out=lambda *_: None,
    )
    body = note.read_text(encoding="utf-8")
    assert body.startswith("# Approved Setups")
    # the dated heading, not the bare one
    assert "## " in body and "· ZEC long" in body
    heading = [l for l in body.splitlines() if l.startswith("## ")][0]
    assert heading.split(" · ")[0].removeprefix("## ").count("-") == 2  # YYYY-MM-DD


# ── missing vault is a hard error, raised BEFORE triage consumes any input ────

def test_resolve_vault_note_raises_when_the_parent_directory_is_absent(tmp_path):
    missing = tmp_path / "no-vault" / "Trade Logs" / "Setups.md"
    with pytest.raises(setups_cli.VaultNoteUnavailable) as exc:
        setups_cli.resolve_vault_note(missing, disabled=False)
    assert "--no-vault-note" in str(exc.value)


def test_resolve_vault_note_returns_none_when_disabled(tmp_path):
    missing = tmp_path / "no-vault" / "Setups.md"
    assert setups_cli.resolve_vault_note(missing, disabled=True) is None


def test_resolve_vault_note_accepts_an_existing_parent(tmp_path):
    note = tmp_path / "Setups.md"          # tmp_path exists; note itself need not
    assert setups_cli.resolve_vault_note(note, disabled=False) == note


def test_missing_vault_never_creates_a_directory_tree(tmp_path):
    missing = tmp_path / "no-vault" / "Trade Logs" / "Setups.md"
    with pytest.raises(setups_cli.VaultNoteUnavailable):
        setups_cli.resolve_vault_note(missing, disabled=False)
    assert not (tmp_path / "no-vault").exists()


# ── the scratch sidecar ─────────────────────────────────────────────────────────────────────
# Added so the approve path can be rehearsed. A decided queue is permanently empty, so
# without this there was no way to exercise execution without spending a real judgement.

def test_decisions_defaults_to_the_real_sidecar():
    """The default must stay the durable log — a rehearsal has to be asked for."""
    assert setups_cli._parse_args([]).decisions == setups_cli.DEFAULT_DECISIONS


def test_decisions_can_be_pointed_at_a_scratch_file(tmp_path):
    scratch = tmp_path / "scratch.jsonl"
    assert setups_cli._parse_args(["--decisions", str(scratch)]).decisions == scratch


def test_a_scratch_sidecar_is_detected_as_such(tmp_path):
    """The flag that drives both the warning and the mirror being forced off."""
    args = setups_cli._parse_args(["--decisions", str(tmp_path / "scratch.jsonl")])
    assert (args.decisions != setups_cli.DEFAULT_DECISIONS) is True
    assert (setups_cli._parse_args([]).decisions != setups_cli.DEFAULT_DECISIONS) is False


def test_scratch_run_would_not_sync_the_vault_mirror(tmp_path, monkeypatch):
    """The safety rule this flag exists with.

    ``sync_mirror`` treats one file as a prefix of the other. A fresh scratch sidecar is
    empty, so syncing it against the real mirror would take the *restore* branch and copy 77
    real decisions into the scratch file — silently re-burying the queue the rehearsal was
    meant to expose, and mixing two histories.
    """
    calls = []
    monkeypatch.setattr(setups_cli, "sync_mirror", lambda *a, **k: calls.append(a))

    scratch = tmp_path / "scratch.jsonl"
    args = setups_cli._parse_args(["--decisions", str(scratch)])
    is_scratch = args.decisions != setups_cli.DEFAULT_DECISIONS
    mirror = None if (args.no_mirror or is_scratch) else args.decisions_mirror

    assert mirror is None, "a scratch run must never touch the vault mirror"
    assert calls == []
