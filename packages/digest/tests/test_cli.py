"""``build`` — the seam where the pure modules meet disk.

This had no tests, and three of the defects a review found lived here: a half-applied
``--orders``, an unguarded order-log read, and warnings that reached nobody. Every one is
reachable by writing a bad file to a tmp path and asserting on the returned body, which is why
``build`` takes its paths as arguments.

``main`` — argparse, printing, delivery — stays untested on purpose. It is orchestration over
pieces that are covered.
"""
from __future__ import annotations

import json

import pytest
from core.trigger import ARMED, NO_ZONE_TAG
from digest import cli


def _entry(key: str, **over) -> dict:
    row = {"key": key, "asset": over.pop("asset", "BTC"), "direction": "long",
           "score": 0.5, "entry": 110.0, "stop": 100.0, "target": 140.0, "price": 105.0,
           "reward_risk": 3.0, "zone_timeframe": "daily", "agreement": 2,
           "trigger_state": NO_ZONE_TAG}
    row.update(over)
    return row


def _snap(as_of: str, rows, **over) -> dict:
    snap = {"run": f"{as_of}T06:20:00Z", "as_of": as_of, "score_version": 8,
            "filters": {"min_score": None, "tiers": None},
            "population": {"qualified": len(rows)}, "rejections": {}, "rows": rows}
    snap.update(over)
    return snap


def _write(path, *snapshots):
    path.write_text("".join(json.dumps(s) + "\n" for s in snapshots), encoding="utf-8")
    return path


@pytest.fixture
def today(monkeypatch):
    """Freeze the clock so "is this snapshot tonight's" is testable at all."""
    from datetime import UTC, date, datetime

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 21, 6, 30, tzinfo=tz or UTC)

    monkeypatch.setattr(cli, "datetime", _Now)
    return date(2026, 8, 21).isoformat()


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path):
    """Keep every test off the real corpus, the real ledger and the real network."""
    monkeypatch.setattr(cli, "load_all_stances", lambda *a, **k: [])
    monkeypatch.setattr(cli.spend, "total", lambda *a, **k: 0.0)
    monkeypatch.setattr(cli.store, "unsettled_keys", lambda *a, **k: set())
    monkeypatch.setattr(cli.store, "awaiting_exit_keys", lambda *a, **k: set())
    monkeypatch.setattr(cli, "DECISIONS", tmp_path / "no-decisions.jsonl")
    monkeypatch.setattr(cli, "HISTORY", tmp_path / "no-history.jsonl")


# ── the stale-snapshot guard ──────────────────────────────────────────────────

def test_a_snapshot_that_is_not_from_today_is_shouted_about(tmp_path, today):
    """The worst failure available: ``setups`` did not run or could not write, so the newest
    snapshot is yesterday's. Rendered quietly this is a real, internally consistent diff of the
    wrong night, with entry and stop levels, that the vault note then stamps with today's date."""
    snaps = _write(tmp_path / "q.jsonl",
                   _snap("2026-08-19", [_entry("a")]), _snap("2026-08-20", [_entry("a")]))
    subject, body = build(snaps, tmp_path)
    assert "STALE" in subject
    assert "2026-08-20" in body and "STALE" in body


def test_a_current_snapshot_is_not_flagged(tmp_path, today):
    snaps = _write(tmp_path / "q.jsonl",
                   _snap("2026-08-20", [_entry("a")]), _snap("2026-08-21", [_entry("a")]))
    subject, body = build(snaps, tmp_path)
    assert "STALE" not in subject and "STALE" not in body


def test_the_body_always_says_which_two_runs_it_diffed(tmp_path, today):
    """Provenance, printed unconditionally. Without it a stale digest is indistinguishable from
    a fresh one on the page."""
    snaps = _write(tmp_path / "q.jsonl",
                   _snap("2026-08-20", [_entry("a")]), _snap("2026-08-21", [_entry("a")]))
    _, body = build(snaps, tmp_path)
    assert "2026-08-20T06:20:00Z → 2026-08-21T06:20:00Z" in body


# ── nothing may take the whole digest down ────────────────────────────────────

def test_a_corrupt_order_log_costs_the_book_section_not_the_digest(tmp_path, today):
    """``execution.store.load`` parses each line with no guard, and this file is appended during
    the nightly — a half-written final line is exactly what an interrupted append leaves."""
    orders = tmp_path / "orders.jsonl"
    orders.write_text('{"at": "2026-08-21T06:00:00+00:00", "outcome": "placed"}\n{"at": "2026-',
                      encoding="utf-8")
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a")]))
    subject, body = build(snaps, tmp_path, orders=orders)
    assert subject
    assert "PROBLEMS" in body and "order log" in body


def test_a_malformed_exclusions_file_costs_attribution_not_the_digest(tmp_path, today,
                                                                     monkeypatch):
    """``yaml.YAMLError`` subclasses ``Exception`` directly, NOT ``ValueError`` — so the
    handler that existed missed the one failure this reader actually has."""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "exclusions.yaml").write_text("bad:\n\t- tab is illegal in yaml\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_DIR", cfg)
    snaps = _write(tmp_path / "q.jsonl",
                   _snap("2026-08-20", [_entry("a"), _entry("b", asset="ETH")]),
                   _snap("2026-08-21", [_entry("a")]))
    subject, body = build(snaps, tmp_path)
    assert subject
    assert "PROBLEMS" in body and "exclusions" in body


def test_problems_reach_the_subject_line_too(tmp_path, today):
    """Warnings used to go only to stderr, which the nightly sends to a rotated log the design
    assumes nobody opens — so every warning in this package was, as deployed, a no-op."""
    orders = tmp_path / "orders.jsonl"
    orders.write_text("{not json\n", encoding="utf-8")
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a")]))
    subject, _ = build(snaps, tmp_path, orders=orders)
    assert subject.startswith("!!") and "problem" in subject


def test_a_missing_snapshot_file_explains_itself(tmp_path, today):
    subject, body = build(tmp_path / "nope.jsonl", tmp_path)
    assert "no queue snapshot" in subject
    assert "setups --list" in body


# ── the override actually overrides ───────────────────────────────────────────

def test_the_orders_override_is_used_for_roster_scope_too(tmp_path, today, monkeypatch):
    """``--orders`` was honoured for the BOOK section and ignored when scoping the roster, which
    reached past it to the real production log. A half-overridden flag is worse than none."""
    seen: list = []
    monkeypatch.setattr(cli.store, "unsettled_keys",
                        lambda path, **k: seen.append(path) or set())
    monkeypatch.setattr(cli.store, "awaiting_exit_keys", lambda path, **k: set())
    orders = tmp_path / "orders.jsonl"
    orders.write_text("", encoding="utf-8")
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a")]))
    build(snaps, tmp_path, orders=orders)
    assert seen and all(str(p) != str(cli.store.DEFAULT_PATH) for p in seen)


# ── run health ────────────────────────────────────────────────────────────────

def test_run_health_from_an_older_night_is_omitted_rather_than_reported(tmp_path, today,
                                                                       monkeypatch):
    """The nightly writes tonight's row before calling the digest, but that writer runs with
    stderr to /dev/null and its exit forced to 0. On the night it breaks, an unchecked read
    prints ``RUN clean`` about yesterday."""
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"run": "2026-08-19T06:00:00Z", "exit": 0,
                                   "steps": [{"name": "setups", "status": "ok"}]}) + "\n",
                       encoding="utf-8")
    monkeypatch.setattr(cli, "HISTORY", history)
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a")]))
    _, body = build(snaps, tmp_path)
    assert "RUN  clean" not in body
    assert "not today" in body


def test_tonights_run_health_is_reported(tmp_path, today, monkeypatch):
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"run": "2026-08-21T06:00:00Z", "exit": 0,
                                   "steps": [{"name": "setups", "status": "ok"}]}) + "\n",
                       encoding="utf-8")
    monkeypatch.setattr(cli, "HISTORY", history)
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a")]))
    _, body = build(snaps, tmp_path)
    assert "RUN  clean" in body


# ── the roster section says which kind of nothing it is ───────────────────────

def test_a_quiet_roster_is_not_silent(tmp_path, today):
    """"The roster is quiet" and "the narrator is broken" were byte-identical in the inbox."""
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a")]))
    _, body = build(snaps, tmp_path, with_llm=False)
    assert "no movement" in body


def test_the_llm_is_never_called_with_no_llm(tmp_path, today, monkeypatch):
    def _explode(*_a, **_k):
        raise AssertionError("narrate must not be called with --no-llm")

    monkeypatch.setattr(cli.narrate, "narrate", _explode)
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a", trigger_state=ARMED)]))
    build(snaps, tmp_path, with_llm=False)


def build(snapshots, tmp_path, *, orders=None, with_llm=False, state_path=None):
    """``cli.build`` with an order log that exists but is empty unless a test supplies one.

    Drops the memory ``cli.build`` returns third, so the many tests that only care about the
    rendered text keep reading as ``subject, body``. ``build_with_memory`` is for the few that
    are about the memory itself.
    """
    subject, body, _ = build_with_memory(snapshots, tmp_path, orders=orders, with_llm=with_llm,
                                         state_path=state_path)
    return subject, body


def build_with_memory(snapshots, tmp_path, *, orders=None, with_llm=False, state_path=None):
    if orders is None:
        orders = tmp_path / "empty-orders.jsonl"
        orders.write_text("", encoding="utf-8")
    return cli.build(snapshots_path=snapshots, orders_path=orders, with_llm=with_llm,
                     state_path=state_path)


# ── the bootstrap warning must not cry wolf ───────────────────────────────────

def test_several_snapshots_all_from_today_is_not_a_problem(tmp_path, today):
    """The normal way this starts: a first day with a few manual ``setups`` runs on it. Warning
    here put a ``!!`` on the subject every morning until the second night ever happened."""
    snaps = _write(tmp_path / "q.jsonl",
                   _snap("2026-08-21", [_entry("a")]), _snap("2026-08-21", [_entry("a")]),
                   _snap("2026-08-21", [_entry("a")]))
    subject, body = build(snaps, tmp_path)
    assert not subject.startswith("!!")
    assert "PROBLEMS" not in body


def test_a_future_dated_snapshot_is_still_flagged(tmp_path, today):
    """More than one day present but none earlier than the newest means an out-of-order or
    future stamp — the corruption the warning exists for."""
    snaps = _write(tmp_path / "q.jsonl",
                   _snap("2026-09-01", [_entry("a")]), _snap("2026-08-21", [_entry("a")]))
    _, body = build(snaps, tmp_path)
    assert "out-of-order" in body


# ── the roster section says a thing once ──────────────────────────────────────
#
# The window is seven days wide, so one video sits inside it for seven nights. As shipped that
# was seven near-identical emails about the same event — reworded each night, because the
# narrator saw the same numbers and wrote fresh prose over them. These cover the memory that
# cancels it, at the seam where it actually has to work.

def _stance(person, asset, lean, published):
    from core.stance import ExtractedStance, build_stance
    from core.thesis import Source
    return build_stance(
        ExtractedStance.model_validate({"asset": asset, "lean": lean, "horizon": "swing",
                                        "rationale": f"{person} on {asset}"}),
        source=Source(person=person, platform="youtube",
                      url=f"https://youtu.be/{person}{published}", published_at=published,
                      transcript_ref=f"youtube/{person[:3]}{published}"),
        model="m", extracted_at="2026-08-20T00:00:00+00:00")


@pytest.fixture
def narrated(monkeypatch):
    """A narrator that records what it was asked to write about, so a test can assert on the
    events that reached prose rather than on the prose itself."""
    seen = []

    def _narrate(payload, **_k):
        seen.append(payload)
        return "  BTC: someone spoke."

    monkeypatch.setattr(cli.narrate, "narrate", _narrate)
    return seen


def _roster_night(tmp_path, monkeypatch, stances, state_path):
    monkeypatch.setattr(cli, "load_all_stances", lambda *a, **k: stances)
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a", asset="BTC")]))
    return build_with_memory(snaps, tmp_path, with_llm=True, state_path=state_path)


def test_the_same_movement_is_not_reported_two_nights_running(tmp_path, today, monkeypatch,
                                                              narrated):
    stances = [_stance("Benjamin Cowen", "BTC", "bullish", "2026-08-20")]
    path = tmp_path / "state.json"

    _, first, memory = _roster_night(tmp_path, monkeypatch, stances, path)
    assert "ROSTER MOVED" in first
    assert len(narrated) == 1
    cli.state.save(path, memory)

    _, second, _ = _roster_night(tmp_path, monkeypatch, stances, path)
    assert "Benjamin Cowen" not in second
    # Not "no movement" — the roster did move, it was simply said last night, and those are
    # different facts about the night.
    assert "nothing new" in second and "earlier digest" in second
    # The narrator is not merely ignored — it is not paid for.
    assert len(narrated) == 1


def test_movement_that_is_genuinely_new_still_arrives(tmp_path, today, monkeypatch, narrated):
    """The guard must suppress repeats, not the section."""
    path = tmp_path / "state.json"
    _, _, memory = _roster_night(tmp_path, monkeypatch,
                                 [_stance("Benjamin Cowen", "BTC", "bullish", "2026-08-20")],
                                 path)
    cli.state.save(path, memory)

    _, second, _ = _roster_night(
        tmp_path, monkeypatch,
        [_stance("Benjamin Cowen", "BTC", "bullish", "2026-08-20"),
         _stance("Pierre", "BTC", "bearish", "2026-08-21")], path)
    assert "ROSTER MOVED" in second
    assert [v["new_voices"] for v in narrated[-1]] == [["Pierre"]]


def test_a_night_that_only_counted_the_movement_does_not_mark_it_told(tmp_path, today,
                                                                     monkeypatch, narrated):
    """`--no-llm` prints "N asset(s) moved", not the events. Recording them as told would drop
    them for good — the section would simply never mention them again."""
    stances = [_stance("Benjamin Cowen", "BTC", "bullish", "2026-08-20")]
    path = tmp_path / "state.json"
    monkeypatch.setattr(cli, "load_all_stances", lambda *a, **k: stances)
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a", asset="BTC")]))

    _, body, memory = build_with_memory(snaps, tmp_path, with_llm=False, state_path=path)
    assert "asset(s) moved" in body
    assert memory[cli.state.ROSTER] == {}

    cli.state.save(path, memory)
    _, second, _ = _roster_night(tmp_path, monkeypatch, stances, path)
    assert "ROSTER MOVED" in second


def test_a_failed_narration_does_not_mark_the_movement_told(tmp_path, today, monkeypatch):
    """Same trap: the count survives, the events did not reach the reader."""
    monkeypatch.setattr(cli.narrate, "narrate",
                        lambda *a, **k: (_ for _ in ()).throw(cli.narrate.NarrationFailed("no")))
    _, body, memory = _roster_night(
        tmp_path, monkeypatch, [_stance("Benjamin Cowen", "BTC", "bullish", "2026-08-20")],
        tmp_path / "state.json")
    assert "could not be generated" in body
    assert memory[cli.state.ROSTER] == {}


def test_no_state_path_means_no_memory_and_no_change_in_behaviour(tmp_path, today, monkeypatch,
                                                                  narrated):
    """``--subject-only`` takes this path. It must neither read nor write the memory."""
    stances = [_stance("Benjamin Cowen", "BTC", "bullish", "2026-08-20")]
    for _ in range(2):
        _, body, memory = _roster_night(tmp_path, monkeypatch, stances, None)
        assert "ROSTER MOVED" in body
        assert memory is None


# ── the spend line only speaks when the number moved ──────────────────────────

def test_a_frozen_spend_total_stops_shouting(tmp_path, today, monkeypatch):
    """`ingest-x` is off by default, so this number cannot move until the month rolls over. It
    shouted on the identical figure four nights running, directly under the line that reports
    real step failures."""
    monkeypatch.setattr(cli.spend, "total", lambda *a, **k: 21.41)
    monkeypatch.setenv("XAI_MONTHLY_CAP", "20.00")
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"run": "2026-08-21T06:00:00Z", "exit": 0,
                                   "steps": [{"name": "setups", "status": "ok"}]}) + "\n",
                       encoding="utf-8")
    monkeypatch.setattr(cli, "HISTORY", history)
    snaps = _write(tmp_path / "q.jsonl", _snap("2026-08-21", [_entry("a")]))
    path = tmp_path / "state.json"

    _, first, memory = build_with_memory(snaps, tmp_path, state_path=path)
    assert "OVER the cap" in first
    cli.state.save(path, memory)

    _, second, _ = build_with_memory(snaps, tmp_path, state_path=path)
    assert "OVER the cap" not in second
    # The run line itself is untouched — that is what the section is for.
    assert "RUN  clean" in second


def test_a_quiet_roster_and_an_already_told_one_do_not_read_alike(tmp_path, today, monkeypatch,
                                                                  narrated):
    """Collapsing the two into one message is how this section stops being trusted — and the
    "already told" wording is the only outward sign the memory is working at all."""
    quiet_body = _roster_night(tmp_path, monkeypatch, [], tmp_path / "a.json")[1]
    assert "no movement on the assets in play" in quiet_body

    path = tmp_path / "b.json"
    stances = [_stance("Benjamin Cowen", "BTC", "bullish", "2026-08-20")]
    cli.state.save(path, _roster_night(tmp_path, monkeypatch, stances, path)[2])
    assert "nothing new" in _roster_night(tmp_path, monkeypatch, stances, path)[1]
