from core.thesis import Extraction, Source, Thesis
from distill.store import exists, load_theses, save_theses, thesis_path


def _thesis(i: int) -> Thesis:
    src = Source(person="P", platform="youtube", url="u",
                 published_at="2025-01-01", transcript_ref="youtube/vid00000001")
    return Thesis(
        id=f"youtube/vid00000001#{i}", thesis_type="macro_lean", domain="crypto",
        asset="BTC", direction="long", timeframe="macro", conviction="med",
        summary="s", source=src,
        extraction=Extraction(model="m", confidence=0.5, extracted_at="t"),
    )


def test_save_and_load_round_trip(tmp_path):
    save_theses("youtube", "vid00000001", [_thesis(0), _thesis(1)],
                model="claude-sonnet-5", distilled_at="2026-07-23T00:00:00+00:00",
                root=tmp_path)
    assert exists("youtube", "vid00000001", root=tmp_path)
    loaded = load_theses("youtube", "vid00000001", root=tmp_path)
    assert [t.id for t in loaded] == ["youtube/vid00000001#0", "youtube/vid00000001#1"]


def test_empty_array_still_marks_processed(tmp_path):
    save_theses("youtube", "empty0000001", [], model="m",
                distilled_at="t", root=tmp_path)
    assert exists("youtube", "empty0000001", root=tmp_path)  # processed marker
    assert load_theses("youtube", "empty0000001", root=tmp_path) == []


def test_exists_false_when_absent(tmp_path):
    assert not exists("youtube", "nope00000001", root=tmp_path)


def test_file_shape_has_metadata(tmp_path):
    import json
    save_theses("youtube", "vid00000001", [_thesis(0)], model="claude-sonnet-5",
                distilled_at="2026-07-23T00:00:00+00:00", root=tmp_path)
    doc = json.loads(thesis_path("youtube", "vid00000001", root=tmp_path).read_text())
    assert doc["transcript_ref"] == "youtube/vid00000001"
    assert doc["model"] == "claude-sonnet-5"
    assert doc["distilled_at"] == "2026-07-23T00:00:00+00:00"
    assert doc["schema_version"] == "1"
    assert len(doc["theses"]) == 1
