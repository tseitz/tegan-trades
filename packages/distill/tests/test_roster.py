import json

from core.thesis import Extraction, Source, Thesis
from distill.roster import DistillResult, distill_all, format_summary


def _write_transcript(root, vid, person, text="body"):
    d = root / "transcripts" / "youtube"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid}.txt").write_text(text, encoding="utf-8")
    (d / f"{vid}.json").write_text(json.dumps({
        "url": f"https://www.youtube.com/watch?v={vid}", "title": "T",
        "published_at": "2025-02-28", "person": person,
        "platform": "youtube", "source_id": vid,
    }), encoding="utf-8")


def _one_thesis(source: Source) -> list[Thesis]:
    return [Thesis(id=f"{source.transcript_ref}#0", thesis_type="macro_lean",
                   domain="crypto", asset="BTC", direction="long",
                   timeframe="macro", conviction="med", summary="s", source=source,
                   extraction=Extraction(model="m", confidence=0.5, extracted_at="t"))]


def test_distill_all_buckets_results(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    _write_transcript(tmp_path, "vid00000002", "Cowen")

    def fake_extract(text, source, **kw):
        return _one_thesis(source) if source.transcript_ref.endswith("1") else []

    results = distill_all(root=tmp_path, extract=fake_extract,
                          distilled_at="2026-07-23T00:00:00+00:00")
    r = results[0]
    assert r.person == "Cowen"
    assert r.distilled == ["vid00000001"]
    assert r.empty == ["vid00000002"]


def test_distill_all_skips_already_distilled(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    calls = {"n": 0}

    def fake_extract(text, source, **kw):
        calls["n"] += 1
        return _one_thesis(source)

    distill_all(root=tmp_path, extract=fake_extract, distilled_at="t")
    results = distill_all(root=tmp_path, extract=fake_extract, distilled_at="t")
    assert calls["n"] == 1                       # second sweep skipped it
    assert results[0].skipped == ["vid00000001"]


def test_distill_all_records_failure_and_continues(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    _write_transcript(tmp_path, "vid00000002", "Cowen")

    def fake_extract(text, source, **kw):
        if source.transcript_ref.endswith("1"):
            raise RuntimeError("boom")
        return _one_thesis(source)

    results = distill_all(root=tmp_path, extract=fake_extract, distilled_at="t")
    r = results[0]
    assert r.failed and r.failed[0][0] == "vid00000001"
    assert r.distilled == ["vid00000002"]        # kept going


def test_force_redistills_existing(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    calls = {"n": 0}

    def fake_extract(text, source, **kw):
        calls["n"] += 1
        return _one_thesis(source)

    distill_all(root=tmp_path, extract=fake_extract, distilled_at="t")
    distill_all(root=tmp_path, extract=fake_extract, distilled_at="t", force=True)
    assert calls["n"] == 2                       # force bypassed the skip


def test_distill_all_runs_concurrently(tmp_path):
    import time

    for i in range(4):
        _write_transcript(tmp_path, f"vid0000000{i}", "Cowen")

    def slow_extract(text, source, **kw):
        time.sleep(0.2)
        return []

    start = time.monotonic()
    distill_all(root=tmp_path, extract=slow_extract, distilled_at="t", max_workers=4)
    elapsed = time.monotonic() - start
    # sequential would take >= 0.8s; concurrent (4 workers) should be ~0.2-0.3s
    assert elapsed < 0.6


def test_format_summary_has_totals(tmp_path):
    r = DistillResult(person="Cowen")
    r.distilled.append("a")
    r.empty.append("b")
    out = format_summary([r])
    assert "Cowen" in out and "TOTAL" in out
