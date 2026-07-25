import json

from core.stance import Provenance, Stance
from core.thesis import Source

from brain.stance_store import DATA_ROOT, exists, load_stances, save_stances, stance_path


def _stance(i: int) -> Stance:
    src = Source(person="P", platform="youtube", url="u",
                 published_at="2025-01-01", transcript_ref="youtube/vid00000001")
    return Stance(
        id=f"youtube/vid00000001#{i}", asset="BTC", lean="bullish",
        rationale="r", source=src,
        extraction=Provenance(model="m", extracted_at="t"),
    )


def test_data_root_resolves_to_repo_root_data_stances():
    assert DATA_ROOT.parts[-2:] == ("data", "stances")
    repo_root = DATA_ROOT.parent.parent
    assert (repo_root / ".git").exists()
    assert (repo_root / "CLAUDE.md").exists()


def test_save_and_load_round_trip(tmp_path):
    save_stances("youtube", "vid00000001", [_stance(0), _stance(1)],
                model="claude-sonnet-5", extracted_at="2026-07-23T00:00:00+00:00",
                root=tmp_path)
    assert exists("youtube", "vid00000001", root=tmp_path)
    loaded = load_stances("youtube", "vid00000001", root=tmp_path)
    assert [s.id for s in loaded] == ["youtube/vid00000001#0", "youtube/vid00000001#1"]


def test_empty_array_still_marks_processed(tmp_path):
    save_stances("youtube", "empty0000001", [], model="m",
                extracted_at="t", root=tmp_path)
    assert exists("youtube", "empty0000001", root=tmp_path)  # processed marker
    assert load_stances("youtube", "empty0000001", root=tmp_path) == []


def test_exists_false_when_absent(tmp_path):
    assert not exists("youtube", "nope00000001", root=tmp_path)


def test_file_shape_has_metadata(tmp_path):
    save_stances("youtube", "vid00000001", [_stance(0)], model="claude-sonnet-5",
                extracted_at="2026-07-23T00:00:00+00:00", root=tmp_path)
    doc = json.loads(stance_path("youtube", "vid00000001", root=tmp_path).read_text())
    assert doc["transcript_ref"] == "youtube/vid00000001"
    assert doc["model"] == "claude-sonnet-5"
    assert doc["extracted_at"] == "2026-07-23T00:00:00+00:00"
    assert doc["schema_version"] == "1"
    assert len(doc["stances"]) == 1
