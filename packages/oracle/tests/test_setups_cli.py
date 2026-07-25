from __future__ import annotations

import json
from datetime import date

from core.canon import Registry
from core.setups import STRUCTURAL, TIER_LARGE, TIER_MAJOR, Candidate
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
        people=("Mayne",), thesis_ids=("t1",),
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


def test_load_decisions_reads_back_appended_records(tmp_path):
    c = _candidate()
    path = tmp_path / "decisions.jsonl"
    setups_cli.append_decision(
        path, setups_cli.decision_record(c, setups_cli.SKIPPED, decided_at="x")
    )
    assert setups_cli.load_decisions(path) == {c.key: setups_cli.SKIPPED}


def test_load_decisions_on_missing_file_is_empty(tmp_path):
    assert setups_cli.load_decisions(tmp_path / "nope.jsonl") == {}


# ── already-decided candidates are skipped on re-runs ────────────────────────

def test_drop_decided_filters_out_a_candidate_already_in_the_sidecar():
    c = _candidate()
    assert setups_cli.drop_decided([c], {c.key}) == []
    assert setups_cli.drop_decided([c], set()) == [c]


def test_two_candidates_with_different_zones_have_distinct_keys_and_are_tracked_independently():
    """Two zones differing only in price must not collide onto one decision — each is
    tracked (and can be individually already-decided) on its own key."""
    a = _candidate(block=_block(top=110.0, bottom=100.0))
    b = _candidate(block=_block(top=120.0, bottom=112.0))
    assert a.key != b.key

    decided = {a.key}
    assert setups_cli.drop_decided([a, b], decided) == [b]


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
    assert setups_cli.load_decisions(decisions_path) == {c.key: setups_cli.APPROVED}
    # nothing else was written under tmp_path — no vault note file appeared anywhere
    assert list(tmp_path.iterdir()) == [decisions_path]


# ── empty queue and quit ──────────────────────────────────────────────────────

def test_triage_on_an_empty_candidate_list_exits_without_prompting(tmp_path):
    def _boom(_):
        raise AssertionError("must not prompt when there is nothing to review")

    counts = setups_cli.triage(
        [], decisions_path=tmp_path / "decisions.jsonl", vault_path=None,
        input_fn=_boom, out=lambda *_: None,
    )
    assert counts == {setups_cli.APPROVED: 0, setups_cli.SKIPPED: 0, setups_cli.ARCHIVED: 0}


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
    assert counts == {setups_cli.APPROVED: 0, setups_cli.SKIPPED: 0, setups_cli.ARCHIVED: 0}


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
