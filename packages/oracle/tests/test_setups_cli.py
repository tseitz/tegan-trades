from __future__ import annotations

import json
from datetime import date

from core.canon import Registry
from core.setups import STRUCTURAL, TIER_LARGE, TIER_MAJOR, Candidate, View
from core.structure import BULLISH, SWING_HIGH, SWING_LOW, Break, OrderBlock, Swing

from oracle import setups_cli


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
        reward_risk=3.0, depth=0.0, proximity=1.0,
        weekly_trend="uptrend", daily_trend="uptrend", zone="discount",
        tier=TIER_MAJOR,
        views=(View(person="Mayne", published_at="2026-07-20"),), thesis_ids=("t1",),
        score=0.5,
    )
    base.update(overrides)
    return Candidate(**base)


# ── filter_candidates ────────────────────────────────────────────────────────

def test_filter_by_min_score_drops_below_threshold():
    low, high = _candidate(score=0.2), _candidate(score=0.8)
    assert setups_cli.filter_candidates([low, high], min_score=0.5) == [high]


def test_filter_by_tier_keeps_only_requested_tiers():
    major = _candidate(tier=TIER_MAJOR)
    large = _candidate(tier=TIER_LARGE)
    assert setups_cli.filter_candidates([major, large], tiers=(TIER_MAJOR,)) == [major]


def test_filter_by_limit_caps_the_result():
    cands = [_candidate(score=s) for s in (0.9, 0.8, 0.7)]
    assert setups_cli.filter_candidates(cands, limit=2) == cands[:2]


def test_filter_with_no_arguments_is_a_passthrough():
    cands = [_candidate(), _candidate()]
    assert setups_cli.filter_candidates(cands) == cands


# ── decision records + sidecar round trip ────────────────────────────────────

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
    c = _candidate(proximity=0.4)
    assert setups_cli.drop_decided([c], _decided(c, setups_cli.LATER)) == []


def test_a_deferred_candidate_returns_once_price_reaches_the_zone():
    """The reason deferral had to become reversible: on the first live run every candidate sat
    outside its zone, so burying them permanently would discard each setup at exactly the
    moment it became actionable."""
    away = _candidate(proximity=0.4)
    arrived = _candidate(proximity=1.0)
    assert arrived.key == away.key       # same zone, different moment
    assert setups_cli.drop_decided([arrived], _decided(away, setups_cli.LATER)) == [arrived]


def test_a_deferred_candidate_returns_when_another_person_backs_it():
    before = _candidate(proximity=0.4)
    after = _candidate(
        proximity=0.4,
        views=(View(person="Mayne", published_at="2026-07-20"),
               View(person="Cowen", published_at="2026-07-19")),
    )
    assert setups_cli.drop_decided([after], _decided(before, setups_cli.LATER)) == [after]


def test_a_rejected_candidate_does_not_come_back_even_when_price_arrives():
    """Rejection is a judgment about the trade, not about its timing."""
    away = _candidate(proximity=0.4)
    arrived = _candidate(proximity=1.0)
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
    c = _candidate(stop=100.0, invalidation=90.0)
    text = setups_cli.format_candidate(c)
    assert "stop 100" in text
    assert "invalidation 90" in text


def test_format_candidate_always_shows_target_source():
    stated = setups_cli.format_candidate(_candidate(target_source="stated"))
    structural = setups_cli.format_candidate(_candidate(target_source=STRUCTURAL))
    assert "[stated]" in stated
    assert "[structural]" in structural


def test_format_candidate_shows_entry_zone_and_entry_price():
    text = setups_cli.format_candidate(_candidate(entry=110.0, entry_top=110.0, entry_bottom=100.0))
    assert "100" in text and "110" in text


# ── vault note is optional ────────────────────────────────────────────────────

def test_triage_skips_the_vault_write_when_vault_path_is_none(tmp_path):
    c = _candidate()
    decisions_path = tmp_path / "decisions.jsonl"
    answers = iter(["a"])
    counts = setups_cli.triage(
        [c], decisions_path=decisions_path, vault_path=None,
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
        [candidate], decisions_path=path, vault_path=None,
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
    _, trade = _run(["r", "t"], _candidate(), tmp_path)
    assert trade["decision"] == setups_cli.REJECTED
    assert trade["reason"] == setups_cli.REASON_TRADE

    _, view = _run(["r", "v"], _candidate(), tmp_path / "b")
    assert view["reason"] == setups_cli.REASON_VIEW


def test_an_unrecognised_reject_reason_falls_back_to_other(tmp_path):
    _, record = _run(["r", "zzz"], _candidate(), tmp_path)
    assert record["reason"] == setups_cli.REASON_OTHER


def test_archive_is_recorded_without_a_reason(tmp_path):
    """Archive is suppression, explicitly not judgment — so there is nothing to explain."""
    counts, record = _run(["x"], _candidate(), tmp_path)
    assert counts[setups_cli.ARCHIVED] == 1
    assert record["decision"] == setups_cli.ARCHIVED
    assert "reason" not in record


# ── empty queue and quit ──────────────────────────────────────────────────────

def test_triage_on_an_empty_candidate_list_exits_without_prompting(tmp_path):
    def _boom(_):
        raise AssertionError("must not prompt when there is nothing to review")

    counts = setups_cli.triage(
        [], decisions_path=tmp_path / "decisions.jsonl", vault_path=None,
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
        [c1, c2], decisions_path=tmp_path / "decisions.jsonl", vault_path=None,
        input_fn=_input, out=lambda *_: None,
    )
    assert counts == {setups_cli.APPROVED: 0, setups_cli.LATER: 0,
                      setups_cli.REJECTED: 0, setups_cli.ARCHIVED: 0}


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
