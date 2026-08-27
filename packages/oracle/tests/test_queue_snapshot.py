"""The queue snapshot — what qualified on a given run, so a later run can diff it.

The invariants worth holding are about *what a missing value means*: a trigger the pass never
computed must not read as a trigger that computed "price never reached the zone", and a
corrupt row must not shrink a population count with no trace.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date

from core.exits import EXTREME
from core.setups import DAILY, TIER_MAJOR, Candidate, View
from core.structure import BULLISH, SWING_HIGH, SWING_LOW, Break, OrderBlock, Swing
from core.trigger import ARMED, NO_ZONE_TAG, Trigger
from oracle import queue_snapshot


def _swing(price, kind, *, index=0, day=1):
    when = date(2025, 1, day)
    return Swing(date=when, price=price, kind=kind, confirmed_at=when, index=index)


def _block():
    """Hand-built, same approach as ``test_queue._block``. Nothing here reads it, but
    ``Candidate`` requires a real one."""
    broken = _swing(120.0, SWING_HIGH)
    origin = _swing(90.0, SWING_LOW, index=3, day=4)
    confirmed_at = date(2025, 1, 6)
    bos = Break(kind=BULLISH, level=broken.price, swing=broken, origin=origin,
                date=confirmed_at, index=5)
    return OrderBlock(kind=BULLISH, top=110.0, bottom=100.0, date=date(2025, 1, 5), index=4,
                      confirmed_at=confirmed_at, bos=bos, invalidation=90.0)


def _candidate(asset: str = "BTC", score: float = 0.5, **over) -> Candidate:
    return Candidate(
        asset=asset, direction="long", block=_block(),
        entry=110.0, entry_top=110.0, entry_bottom=100.0,
        stop=100.0, invalidation=90.0,
        target=140.0, target_source=EXTREME,
        reward_risk=3.0, reward_risk_from_price=3.5, approach=1.0, price=105.0,
        weekly_trend="uptrend", daily_trend="uptrend", zone="discount",
        zone_timeframe=DAILY, tier=TIER_MAJOR,
        freshness=1.0, trend_alignment=1.0,
        views=over.pop("views", (View(person="Mayne", published_at="2026-07-20"),)),
        thesis_ids=("t1",), score=score, **over,
    )


def _stats(**over):
    """Only the fields the snapshot reads. A stand-in rather than a real ``BuildStats`` so the
    test does not have to construct the whole audit trail to check one projection of it."""
    class _S:
        rejections = over.get("rejections", Counter())
        triggers = over.get("triggers", {})
        candidate_count = over.get("candidate_count", 0)
        assets_priced = over.get("assets_priced", 0)
        assets_unpriced = over.get("assets_unpriced", 0)
        duplicate_zones = over.get("duplicate_zones", 0)
    return _S()


def _row(**over):
    candidates = over.pop("candidates", [_candidate()])
    return queue_snapshot.row(candidates, over.pop("stats", _stats()),
                              as_of=date(2026, 8, 20), run_at="2026-08-20T06:20:11Z", **over)


# ── projecting candidates into a row ──────────────────────────────────────────

def test_one_entry_per_candidate_keyed_by_candidate_key():
    a, b = _candidate("BTC"), _candidate("ETH")
    row = _row(candidates=[a, b])
    assert [e["key"] for e in row["rows"]] == [a.key, b.key]
    assert [e["asset"] for e in row["rows"]] == ["BTC", "ETH"]


def test_carries_the_fields_the_digest_prints_without_re_deriving_them():
    """The digest must render a row from the snapshot alone. Anything it would otherwise have
    to recompute is decision-time state that a later run would get wrong."""
    entry = _row()["rows"][0]
    for field in ("score", "entry", "stop", "target", "price", "reward_risk",
                  "direction", "zone_timeframe", "agreement"):
        assert field in entry, field
    assert entry["agreement"] == 1


def test_trigger_state_is_carried_when_the_pass_ran():
    c = _candidate()
    row = _row(candidates=[c], stats=_stats(triggers={c.key: Trigger(state=ARMED)}))
    assert row["rows"][0]["trigger_state"] == ARMED


def test_a_candidate_the_trigger_pass_skipped_is_none_not_a_state():
    """``no_zone_tag`` means the pass ran and price never reached the zone. ``None`` means it
    never ran at all — ``setups --no-triggers``, or an asset with no intraday series. Collapsing
    the two would make the digest announce an arrival every time triggers came back on."""
    c = _candidate()
    row = _row(candidates=[c], stats=_stats(triggers={}))
    assert row["rows"][0]["trigger_state"] is None

    tagged = _row(candidates=[c], stats=_stats(triggers={c.key: Trigger(state=NO_ZONE_TAG)}))
    assert tagged["rows"][0]["trigger_state"] == NO_ZONE_TAG


def test_records_the_population_it_was_drawn_from():
    """A qualified count without the candidate count cannot say whether a shrinking queue is
    the corpus going quiet or the gates tightening."""
    row = _row(candidates=[_candidate()],
               stats=_stats(candidate_count=58, assets_priced=338, assets_unpriced=207))
    assert row["population"] == {"qualified": 1, "candidates": 58,
                                 "assets_priced": 338, "assets_unpriced": 207,
                                 "duplicate_zones": 0}


def test_records_the_filters_that_produced_the_population():
    """A candidate can leave the qualified set because the *gate* moved rather than because
    anything about it did. Without the floor and the tier filter a later diff cannot tell those
    apart, and would report a tightened filter as the corpus going quiet."""
    row = _row(min_score=0.4, tiers=("major", "large"))
    assert row["filters"] == {"min_score": 0.4, "tiers": ["major", "large"]}


def test_absent_filters_are_recorded_as_null_not_omitted():
    """``--min-score`` defaults to None. Recording the absence explicitly is what lets a reader
    tell "no floor was set" from "this snapshot predates the field"."""
    assert _row()["filters"] == {"min_score": None, "tiers": None}


def test_rejections_are_recorded_as_a_plain_dict():
    row = _row(stats=_stats(rejections=Counter({"weekly_disagrees": 2170, "no_live_zone": 1156})))
    assert row["rejections"] == {"weekly_disagrees": 2170, "no_live_zone": 1156}


def test_the_row_is_json_serializable():
    """It is written with ``json.dumps``; a ``Counter`` or a ``date`` leaking through would
    take out the nightly step that writes it."""
    json.dumps(_row())


# ── append / load ─────────────────────────────────────────────────────────────

def test_append_is_append_only(tmp_path):
    path = tmp_path / "queue.jsonl"
    queue_snapshot.append(path, {"run": "a", "rows": []})
    queue_snapshot.append(path, {"run": "b", "rows": []})
    assert [r["run"] for r in queue_snapshot.load(path)] == ["a", "b"]


def test_append_creates_the_parent_directory(tmp_path):
    """Unlike the vault mirror, this lives under ``data/`` — a tree this repo owns and creates
    freely. The refuse-to-mkdir rule is about not faking an unmounted vault, not about here."""
    path = tmp_path / "setups" / "queue.jsonl"
    assert queue_snapshot.append(path, {"run": "a", "rows": []}) is True
    assert path.exists()


def test_a_failing_write_warns_and_never_raises(tmp_path):
    """This runs inside ``setups``, which the nightly calls. Losing a snapshot costs tomorrow's
    digest one section; raising here would cost the whole queue listing."""
    blocked = tmp_path / "file.txt"
    blocked.write_text("not a directory", encoding="utf-8")
    warned: list[str] = []
    assert queue_snapshot.append(blocked / "queue.jsonl", {"run": "a"},
                                 warn=warned.append) is False
    assert warned


def test_a_corrupt_line_is_skipped_loudly(tmp_path):
    """Silently dropping a row shrinks a population count with no trace — the same argument
    ``brain.stance_store.load_all_stances`` makes for printing rather than passing."""
    path = tmp_path / "queue.jsonl"
    path.write_text('{"run": "a"}\nnot json\n{"run": "b"}\n', encoding="utf-8")
    warned: list[str] = []
    rows = queue_snapshot.load(path, warn=warned.append)
    assert [r["run"] for r in rows] == ["a", "b"]
    assert warned


def test_load_of_a_missing_file_is_empty_not_an_error(tmp_path):
    """The normal state on the first ever run."""
    assert queue_snapshot.load(tmp_path / "nope.jsonl") == []


def test_latest_returns_the_most_recent_row(tmp_path):
    path = tmp_path / "queue.jsonl"
    queue_snapshot.append(path, {"run": "a", "rows": []})
    queue_snapshot.append(path, {"run": "b", "rows": []})
    assert queue_snapshot.latest(path)["run"] == "b"


def test_latest_is_none_before_the_first_run(tmp_path):
    """The digest's bootstrap case: nothing to diff against is not a failure."""
    assert queue_snapshot.latest(tmp_path / "nope.jsonl") is None


# ── rows that cannot be read ──────────────────────────────────────────────────

def test_a_row_missing_an_identity_field_is_dropped_loudly(tmp_path):
    """A *well-formed* row missing ``key`` used to raise ``KeyError`` out of the middle of
    ``digest.diff.compare`` — a hard failure in a package whose whole contract is to degrade —
    while the malformed-line path beside it was carefully handled."""
    path = tmp_path / "queue.jsonl"
    good = {"key": "a", "asset": "BTC", "direction": "long"}
    path.write_text(json.dumps({"run": "r", "rows": [good, {"asset": "ETH"}]}) + "\n",
                    encoding="utf-8")
    warned: list[str] = []
    rows = queue_snapshot.load(path, warn=warned.append)
    assert [r["key"] for r in rows[0]["rows"]] == ["a"]
    assert warned and "dropped" in warned[0]


def test_a_snapshot_whose_rows_are_not_a_list_degrades_to_empty(tmp_path):
    path = tmp_path / "queue.jsonl"
    path.write_text(json.dumps({"run": "r", "rows": "nonsense"}) + "\n", encoding="utf-8")
    assert queue_snapshot.load(path)[0]["rows"] == []


def test_a_readable_snapshot_is_untouched(tmp_path):
    path = tmp_path / "queue.jsonl"
    good = {"key": "a", "asset": "BTC", "direction": "long", "score": 0.5}
    path.write_text(json.dumps({"run": "r", "rows": [good]}) + "\n", encoding="utf-8")
    warned: list[str] = []
    assert queue_snapshot.load(path, warn=warned.append)[0]["rows"] == [good]
    assert not warned


def test_the_date_of_the_newest_view_is_carried():
    """``agreement`` is a bare count, so three people who last spoke in March render exactly
    like three who spoke yesterday. The digest cannot recompute this — the views live on the
    ``Candidate``, which no longer exists by the time it reads the snapshot."""
    entry = _row(candidates=[_candidate()])["rows"][0]
    assert entry["newest_at"] == _candidate().newest_at


def test_a_candidate_with_no_views_carries_no_date_rather_than_an_empty_string():
    """``Candidate.newest_at`` returns ``""`` when there are no views. Written through as-is it
    would render as an age of "today", which is the one reading that is certainly wrong."""
    entry = _row(candidates=[_candidate(views=())])["rows"][0]
    assert entry["newest_at"] is None
