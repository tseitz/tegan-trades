import json

from core.canon import Registry

from distill.triage_cli import (
    append_note,
    collapse_restatements,
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


# ── collapse_restatements ───────────────────────────────────────────────────

def _ranked_from(tmp_path, theses):
    _write_doc(tmp_path, "a.json", theses)
    return rank_corpus(tmp_path, REGISTRY)


def test_collapse_defaults_to_current_view_only(tmp_path):
    """Default: one entry per (person, asset, direction, timeframe) — their current stance.
    A fixed window only slices a continuously-restated view into buckets; it never finds
    'the latest one', which is what triage actually wants."""
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at="2026-07-22"),
        _tdict(ref="a", idx=1, published_at="2026-06-19"),  # >30d earlier: own cluster
        _tdict(ref="a", idx=2, published_at="2026-01-05"),  # months earlier: own cluster
    ])
    out = collapse_restatements(ranked)

    assert len(out) == 1
    assert out[0].thesis.source.published_at == "2026-07-22"
    # Counts the whole history, not just one cluster — dropping 2 statements silently
    # would read as "this is all he said".
    assert out[0].restated == 2
    assert out[0].restated_since == "2026-01-05"


def test_collapse_current_view_still_separates_direction_and_timeframe(tmp_path):
    """A long-term position call and a short-term swing call are different theses, not
    restatements — these people routinely hold both on the same asset at once."""
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at="2026-07-22", direction="long"),
        _tdict(ref="a", idx=1, published_at="2026-07-21", direction="short"),
        _tdict(ref="a", idx=2, published_at="2026-07-20", person="Benjamin Cowen"),
    ])
    assert len(collapse_restatements(ranked)) == 3


def test_collapse_current_view_leaves_undated_alone(tmp_path):
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at=""),
        _tdict(ref="a", idx=1, published_at=""),
    ])
    assert len(collapse_restatements(ranked)) == 2


def test_collapse_keeps_most_recent_restatement_within_window(tmp_path):
    """Same person re-stating the same call inside the window is one position, not three."""
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at="2026-07-22"),
        _tdict(ref="a", idx=1, published_at="2026-07-10"),
        _tdict(ref="a", idx=2, published_at="2026-07-01"),
    ])
    out = collapse_restatements(ranked, window_days=30, latest_only=False)

    assert len(out) == 1
    assert out[0].thesis.source.published_at == "2026-07-22"  # newest survives, not highest-scoring
    assert out[0].restated == 2
    assert out[0].restated_since == "2026-07-01"


def test_collapse_keeps_calls_outside_the_window_separate(tmp_path):
    """A June call and a July call are genuinely separate statements of view, not dupes."""
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at="2026-07-22"),
        _tdict(ref="a", idx=1, published_at="2026-04-01"),
    ])
    out = collapse_restatements(ranked, window_days=30, latest_only=False)

    assert len(out) == 2
    assert all(r.restated == 0 for r in out)


def test_collapse_chains_by_cluster_anchor_not_transitively(tmp_path):
    """Anchored on the newest of each cluster: a steady weekly drip must not chain into one
    blob spanning months, or an old call silently absorbs the current one."""
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at="2026-07-22"),
        _tdict(ref="a", idx=1, published_at="2026-07-05"),  # within 30d of 07-22 -> folds
        _tdict(ref="a", idx=2, published_at="2026-06-20"),  # 32d from 07-22 -> new cluster
        _tdict(ref="a", idx=3, published_at="2026-06-10"),  # within 30d of 06-20 -> folds
    ])
    out = collapse_restatements(ranked, window_days=30, latest_only=False)

    assert [r.thesis.source.published_at for r in out] == ["2026-07-22", "2026-06-20"]
    assert [r.restated for r in out] == [1, 1]


def test_collapse_never_merges_across_direction_timeframe_or_person(tmp_path):
    """A long and a short on the same day are a thesis change — the whole point of triage."""
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at="2026-07-22", direction="long"),
        _tdict(ref="a", idx=1, published_at="2026-07-22", direction="short"),
        _tdict(ref="a", idx=2, published_at="2026-07-22", person="Benjamin Cowen"),
        _tdict(ref="a", idx=3, published_at="2026-07-22", asset="Bitcoin"),
    ])
    out = collapse_restatements(ranked, window_days=30, latest_only=False)
    # idx0 and idx3 share (person, BTC, long, swing) and the same day -> collapse to one.
    assert len(out) == 3


def test_collapse_leaves_undated_theses_alone(tmp_path):
    """Without a date we can't say which restatement is current, so never fold them."""
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at=""),
        _tdict(ref="a", idx=1, published_at=""),
    ])
    out = collapse_restatements(ranked, window_days=30, latest_only=False)
    assert len(out) == 2


def test_collapse_preserves_score_ordering(tmp_path):
    ranked = _ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at="2026-07-22", asset="BTC", conviction="low", confidence=0.2),
        _tdict(ref="a", idx=1, published_at="2026-07-22", asset="Bitcoin", direction="short",
               conviction="high", confidence=0.99),
    ])
    out = collapse_restatements(ranked, window_days=30, latest_only=False)
    assert [r.score for r in out] == sorted((r.score for r in out), reverse=True)


def test_prompt_shows_publish_date_and_restatement_count(tmp_path):
    """Which of two similar calls is current is the whole question — show the date."""
    ranked = collapse_restatements(_ranked_from(tmp_path, [
        _tdict(ref="a", idx=0, published_at="2026-07-22"),
        _tdict(ref="a", idx=1, published_at="2026-07-02"),
    ]), window_days=30)
    lines: list[str] = []
    triage(ranked, decisions_path=tmp_path / "d.jsonl", vault_path=tmp_path / "v.md",
           input_fn=lambda _: "q", out=lines.append)

    assert "2026-07-22" in lines[0]
    assert "+1 similar since 2026-07-02" in lines[0]


def test_prompt_marks_undated_theses(tmp_path):
    ranked = _ranked_from(tmp_path, [_tdict(ref="a", idx=0, published_at="")])
    lines: list[str] = []
    triage(ranked, decisions_path=tmp_path / "d.jsonl", vault_path=tmp_path / "v.md",
           input_fn=lambda _: "q", out=lines.append)

    assert "undated" in lines[0]


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
