import json

from core.canon import Registry

from distill.triage_cli import (
    append_note,
    load_decisions,
    rank_corpus,
    record_decision,
    render_note,
    triage,
)

REGISTRY = Registry(
    people={"tradermayne": "TraderMayne", "cowen": "Benjamin Cowen"},
    members={},
    assets={"bitcoin": "BTC", "btc": "BTC"},
    tickers={"BTC": {"name": "Bitcoin", "market_cap_rank": 1}},
)


def _tdict(*, ref="vid", idx=0, asset="BTC", person="TraderMayne", direction="long",
           conviction="high", confidence=0.9, published_at="2025-06-01"):
    return {
        "id": f"youtube/{ref}#{idx}",
        "schema_version": "1",
        "thesis_type": "macro_lean",
        "domain": "crypto",
        "asset": asset,
        "direction": direction,
        "timeframe": "swing",
        "conviction": conviction,
        "summary": f"summary for {asset} {direction}",
        "confidence": confidence,
        "invalidation": "close below the level",
        "key_levels": [107450.0],
        "quotes": [{"text": "a representative quote", "timestamp": None}],
        "source": {"person": person, "platform": "youtube", "url": f"https://yt/{ref}",
                   "published_at": published_at, "transcript_ref": f"youtube/{ref}"},
        "extraction": {"model": "m", "confidence": confidence, "extracted_at": "2026-01-01T00:00:00Z"},
    }


def _write_doc(root, name, theses):
    d = root / "youtube"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"theses": theses}), encoding="utf-8")


# ── rank_corpus ─────────────────────────────────────────────────────────────

def test_rank_corpus_orders_by_score_desc_and_skips_empty(tmp_path):
    _write_doc(tmp_path, "a.json", [
        _tdict(ref="a", idx=0, conviction="high", confidence=0.95, published_at="2025-06-10"),  # strong
        _tdict(ref="a", idx=1, conviction="low", confidence=0.20, published_at="2025-01-01"),   # weak
    ])
    _write_doc(tmp_path, "empty.json", [])  # must be skipped, not crash
    ranked = rank_corpus(tmp_path, REGISTRY)
    assert [r.thesis.id for r in ranked] == ["youtube/a#0", "youtube/a#1"]
    assert ranked[0].score > ranked[1].score
    assert ranked[0].resolved.asset_canonical == "BTC"


def test_rank_corpus_survives_undated_thesis(tmp_path):
    # Regression: a transcript ingested without metadata hydration has published_at "".
    # min() over raw strings made '' the corpus 'oldest' and date.fromisoformat('') raised,
    # so one unhydrated video took down the whole triage queue.
    _write_doc(tmp_path, "a.json", [
        _tdict(ref="a", idx=0, published_at="2025-06-10"),
        _tdict(ref="a", idx=1, published_at=""),  # unhydrated
    ])
    _write_doc(tmp_path, "b.json", [_tdict(ref="b", idx=0, published_at="2025-01-01")])
    ranked = rank_corpus(tmp_path, REGISTRY)

    assert len(ranked) == 3
    by_id = {r.thesis.id: r for r in ranked}
    # The dated pair still spans 2025-01-01..2025-06-10, so the newest keeps full recency
    # rather than being flattened by a phantom '' oldest.
    assert by_id["youtube/a#0"].score > by_id["youtube/b#0"].score
    # The undated one is ranked, not dropped, but earns no recency credit.
    assert by_id["youtube/a#1"].score < by_id["youtube/a#0"].score


def test_rank_corpus_all_undated_does_not_crash(tmp_path):
    _write_doc(tmp_path, "a.json", [
        _tdict(ref="a", idx=0, published_at=""),
        _tdict(ref="a", idx=1, published_at="", conviction="low"),
    ])
    ranked = rank_corpus(tmp_path, REGISTRY)
    assert [r.thesis.id for r in ranked] == ["youtube/a#0", "youtube/a#1"]


def test_rank_corpus_excludes_decided(tmp_path):
    _write_doc(tmp_path, "a.json", [
        _tdict(ref="a", idx=0),
        _tdict(ref="a", idx=1),
    ])
    ranked = rank_corpus(tmp_path, REGISTRY, decided={"youtube/a#0"})
    assert [r.thesis.id for r in ranked] == ["youtube/a#1"]


def test_rank_corpus_agreement_lifts_a_shared_view(tmp_path):
    # Two different people, same canonical asset + direction -> each gets agreement lift.
    _write_doc(tmp_path, "shared.json", [
        _tdict(ref="s", idx=0, person="TraderMayne", asset="BTC", direction="long"),
        _tdict(ref="s", idx=1, person="Cowen", asset="Bitcoin", direction="long"),  # alias -> BTC
    ])
    _write_doc(tmp_path, "solo.json", [
        _tdict(ref="u", idx=0, person="TraderMayne", asset="BTC", direction="short"),
    ])
    ranked = rank_corpus(tmp_path, REGISTRY)
    shared = [r for r in ranked if r.thesis.direction == "long"]
    solo = next(r for r in ranked if r.thesis.direction == "short")
    # the two agreeing longs outrank the lone short (all else equal)
    assert all(r.score > solo.score for r in shared)


# ── decisions sidecar ───────────────────────────────────────────────────────

def test_decisions_roundtrip_last_wins(tmp_path):
    path = tmp_path / "triage" / "decisions.jsonl"
    record_decision(path, "youtube/a#0", "skipped")
    record_decision(path, "youtube/a#1", "promoted")
    record_decision(path, "youtube/a#0", "archived")  # overrides earlier skip
    decisions = load_decisions(path)
    assert decisions == {"youtube/a#0": "archived", "youtube/a#1": "promoted"}


def test_load_decisions_missing_file_is_empty(tmp_path):
    assert load_decisions(tmp_path / "nope.jsonl") == {}


# ── vault note rendering ────────────────────────────────────────────────────

def test_render_note_contains_key_fields(tmp_path):
    _write_doc(tmp_path, "a.json", [_tdict(ref="a", idx=0, asset="Bitcoin", person="TraderMayne")])
    ranked = rank_corpus(tmp_path, REGISTRY)[0]
    note = render_note(ranked)
    assert "TraderMayne" in note
    assert "BTC" in note                      # canonical, not the raw "Bitcoin"
    assert "long" in note
    assert "close below the level" in note    # invalidation
    assert "representative quote" in note      # quote body
    assert "https://yt/a" in note              # source link


def test_append_note_creates_then_appends(tmp_path):
    note_path = tmp_path / "vault" / "Promoted Theses.md"
    append_note(note_path, "## first section")
    append_note(note_path, "## second section")
    text = note_path.read_text(encoding="utf-8")
    assert text.count("##") == 2
    assert text.index("first") < text.index("second")
    assert "# Promoted Theses" in text        # title header written once


# ── interactive triage loop ─────────────────────────────────────────────────

def _ranked(tmp_path):
    _write_doc(tmp_path, "a.json", [
        _tdict(ref="a", idx=0, confidence=0.95),
        _tdict(ref="a", idx=1, confidence=0.50),
        _tdict(ref="a", idx=2, confidence=0.10),
    ])
    return rank_corpus(tmp_path, REGISTRY)


def test_triage_approve_writes_note_and_records(tmp_path):
    ranked = _ranked(tmp_path)
    decisions_path = tmp_path / "triage" / "decisions.jsonl"
    vault_path = tmp_path / "vault" / "Promoted Theses.md"
    answers = iter(["a", "s", "x"])  # approve, skip, archive
    counts = triage(ranked, decisions_path=decisions_path, vault_path=vault_path,
                    input_fn=lambda _p: next(answers), out=lambda _m: None)
    assert counts == {"approved": 1, "skipped": 1, "archived": 1}
    # only the approved thesis produced a note section
    assert vault_path.read_text(encoding="utf-8").count("## ") == 1
    decisions = load_decisions(decisions_path)
    assert decisions[ranked[0].thesis.id] == "promoted"
    assert decisions[ranked[1].thesis.id] == "skipped"
    assert decisions[ranked[2].thesis.id] == "archived"


def test_triage_quit_stops_early_and_writes_nothing_after(tmp_path):
    ranked = _ranked(tmp_path)
    decisions_path = tmp_path / "triage" / "decisions.jsonl"
    vault_path = tmp_path / "vault" / "Promoted Theses.md"
    answers = iter(["q", "a"])  # quit immediately; the 'a' must never be consumed
    counts = triage(ranked, decisions_path=decisions_path, vault_path=vault_path,
                    input_fn=lambda _p: next(answers), out=lambda _m: None)
    assert counts == {"approved": 0, "skipped": 0, "archived": 0}
    assert not vault_path.exists()
    assert load_decisions(decisions_path) == {}
