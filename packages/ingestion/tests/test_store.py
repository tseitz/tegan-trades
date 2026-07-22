import json
from ingestion.store import TranscriptRecord, save, load, exists, path_for


def test_save_and_load_roundtrip(tmp_path):
    rec = TranscriptRecord(platform="youtube", source_id="abc123",
                           text="hello world", metadata={"title": "Test"})
    save(rec, root=tmp_path)
    assert load("youtube", "abc123", root=tmp_path) == "hello world"


def test_exists_reflects_saved_state(tmp_path):
    assert exists("youtube", "vid", root=tmp_path) is False
    save(TranscriptRecord("youtube", "vid", "t", {}), root=tmp_path)
    assert exists("youtube", "vid", root=tmp_path) is True


def test_metadata_sidecar_written(tmp_path):
    save(TranscriptRecord("youtube", "vid", "t", {"title": "T"}), root=tmp_path)
    meta = json.loads((tmp_path / "youtube" / "vid.json").read_text())
    assert meta["title"] == "T"
    assert meta["source_id"] == "vid"
    assert meta["platform"] == "youtube"


def test_save_is_idempotent(tmp_path):
    rec = TranscriptRecord("youtube", "vid", "t", {})
    p1 = save(rec, root=tmp_path)
    p2 = save(rec, root=tmp_path)
    assert p1 == p2
    assert path_for("youtube", "vid", root=tmp_path) == p1
