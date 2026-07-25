import json
import time

from core.stance import DroppedStance, Provenance, Stance
from core.thesis import Source

from brain.extract import StanceResult
from brain.sweep import ExtractResult, extract_all, format_summary


def _write_transcript(root, vid, person, text="body"):
    d = root / "transcripts" / "youtube"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid}.txt").write_text(text, encoding="utf-8")
    (d / f"{vid}.json").write_text(json.dumps({
        "url": f"https://www.youtube.com/watch?v={vid}", "title": "T",
        "published_at": "2025-02-28", "person": person,
        "platform": "youtube", "source_id": vid,
    }), encoding="utf-8")


def _stance(source: Source, i: int = 0) -> Stance:
    return Stance(
        id=f"{source.transcript_ref}#{i}", asset="BTC", lean="bullish",
        rationale="r", source=source,
        extraction=Provenance(model="m", extracted_at="t"),
    )


def _one_stance(source: Source) -> StanceResult:
    return StanceResult(stances=[_stance(source)], dropped=[])


def _empty(source: Source) -> StanceResult:
    return StanceResult(stances=[], dropped=[])


def test_extract_all_buckets_results(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    _write_transcript(tmp_path, "vid00000002", "Cowen")

    def fake_extract(text, source, **kw):
        return _one_stance(source) if source.transcript_ref.endswith("1") else _empty(source)

    results = extract_all(root=tmp_path, extract=fake_extract,
                          extracted_at="2026-07-23T00:00:00+00:00")
    r = results[0]
    assert r.person == "Cowen"
    assert r.extracted == ["vid00000001"]
    assert r.empty == ["vid00000002"]


def test_extract_all_skips_already_extracted(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    calls = {"n": 0}

    def fake_extract(text, source, **kw):
        calls["n"] += 1
        return _one_stance(source)

    extract_all(root=tmp_path, extract=fake_extract, extracted_at="t")
    results = extract_all(root=tmp_path, extract=fake_extract, extracted_at="t")
    assert calls["n"] == 1                       # second sweep skipped it
    assert results[0].skipped == ["vid00000001"]


def test_extract_all_records_failure_and_continues(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    _write_transcript(tmp_path, "vid00000002", "Cowen")

    def fake_extract(text, source, **kw):
        if source.transcript_ref.endswith("1"):
            raise RuntimeError("boom")
        return _one_stance(source)

    results = extract_all(root=tmp_path, extract=fake_extract, extracted_at="t")
    r = results[0]
    assert r.failed and r.failed[0][0] == "vid00000001"
    assert r.extracted == ["vid00000002"]        # kept going


def test_force_reextracts_existing(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    calls = {"n": 0}

    def fake_extract(text, source, **kw):
        calls["n"] += 1
        return _one_stance(source)

    extract_all(root=tmp_path, extract=fake_extract, extracted_at="t")
    extract_all(root=tmp_path, extract=fake_extract, extracted_at="t", force=True)
    assert calls["n"] == 2                       # force bypassed the skip


def test_extract_all_runs_concurrently(tmp_path):
    for i in range(4):
        _write_transcript(tmp_path, f"vid0000000{i}", "Cowen")

    def slow_extract(text, source, **kw):
        time.sleep(0.2)
        return _empty(source)

    start = time.monotonic()
    extract_all(root=tmp_path, extract=slow_extract, extracted_at="t", max_workers=4)
    elapsed = time.monotonic() - start
    # sequential would take >= 0.8s; concurrent (4 workers) should be ~0.2-0.3s
    assert elapsed < 0.6


def test_dropped_stances_are_recorded_not_silently_lost(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")

    def fake_extract(text, source, **kw):
        return StanceResult(
            stances=[_stance(source)],
            dropped=[DroppedStance(raw={"bad": True}, error="asset: field required")],
        )

    results = extract_all(root=tmp_path, extract=fake_extract, extracted_at="t")
    r = results[0]
    assert r.extracted == ["vid00000001"]         # sibling stance survives
    assert r.dropped == [("vid00000001", "asset: field required")]


def test_limit_applies_to_sorted_paths_deterministically(tmp_path):
    for i in range(5):
        _write_transcript(tmp_path, f"vid0000000{i}", "Cowen")
    calls = []

    def fake_extract(text, source, **kw):
        calls.append(source.transcript_ref)
        return _empty(source)

    extract_all(root=tmp_path, extract=fake_extract, extracted_at="t", limit=2)
    first_run = sorted(calls)

    calls.clear()
    extract_all(root=tmp_path, extract=fake_extract, extracted_at="t", limit=2, force=True)
    second_run = sorted(calls)

    assert first_run == second_run
    assert len(first_run) == 2
    assert first_run == ["youtube/vid00000000", "youtube/vid00000001"]


def test_format_summary_has_totals_and_dropped_marker():
    r = ExtractResult(person="Cowen")
    r.extracted.append("a")
    r.empty.append("b")
    r.dropped.append(("c", "bad asset"))
    out = format_summary([r])
    assert "Cowen" in out and "TOTAL" in out
    assert "    ~ c: bad asset" in out
