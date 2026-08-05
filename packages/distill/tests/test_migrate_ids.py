import json

from core.thesis import thesis_id
from distill.migrate_ids import migrate, remap_document


def _thesis(idx, *, asset="BTC", direction="long", summary="accumulate spot"):
    return {
        "id": f"youtube/vid#{idx}",  # legacy positional id
        "schema_version": "1",
        "thesis_type": "macro_lean",
        "domain": "crypto",
        "asset": asset,
        "direction": direction,
        "timeframe": "position",
        "conviction": "high",
        "summary": summary,
        "invalidation": None,
        "key_levels": [],
        "quotes": [],
        "source": {"person": "TraderMayne", "platform": "youtube", "url": "https://yt/vid",
                   "published_at": "2026-07-22", "transcript_ref": "youtube/vid"},
        "extraction": {"model": "m", "confidence": 0.9, "extracted_at": "2026-01-01T00:00:00Z"},
    }


def _write(root, name, theses):
    d = root / "theses" / "youtube"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"transcript_ref": "youtube/vid", "theses": theses}),
                          encoding="utf-8")
    return d / name


def test_remap_document_rekeys_by_content():
    doc = {"theses": [_thesis(0), _thesis(1, direction="short")]}
    new_doc, mapping = remap_document(doc)

    expected = thesis_id("youtube/vid", thesis_type="macro_lean", asset="BTC",
                         direction="long", timeframe="position", summary="accumulate spot")
    assert new_doc["theses"][0]["id"] == expected
    assert mapping["youtube/vid#0"] == expected
    assert mapping["youtube/vid#1"] != expected
    assert doc["theses"][0]["id"] == "youtube/vid#0"  # input untouched


def test_migrate_rewrites_files_and_remaps_decisions(tmp_path):
    path = _write(tmp_path, "vid.json", [_thesis(0), _thesis(1, direction="short")])
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(
        '{"id": "youtube/vid#0", "decision": "promoted"}\n'
        '{"id": "youtube/vid#1", "decision": "skipped"}\n', encoding="utf-8")

    report = migrate(tmp_path / "theses", decisions)

    written = json.loads(path.read_text())
    new_ids = [t["id"] for t in written["theses"]]
    assert all("#" in i and not i.endswith(("#0", "#1")) for i in new_ids)

    remapped = [json.loads(x) for x in decisions.read_text().splitlines() if x.strip()]
    assert {r["id"] for r in remapped} == set(new_ids)
    # The decision must follow the *content*, not the slot.
    by_id = {r["id"]: r["decision"] for r in remapped}
    assert by_id[new_ids[0]] == "promoted"
    assert by_id[new_ids[1]] == "skipped"
    assert report.theses == 2 and report.decisions_remapped == 2 and report.decisions_dropped == 0


def test_migrate_is_idempotent(tmp_path):
    path = _write(tmp_path, "vid.json", [_thesis(0)])
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text('{"id": "youtube/vid#0", "decision": "promoted"}\n', encoding="utf-8")

    migrate(tmp_path / "theses", decisions)
    first = (path.read_text(), decisions.read_text())
    migrate(tmp_path / "theses", decisions)

    assert (path.read_text(), decisions.read_text()) == first


def test_migrate_drops_decisions_with_no_surviving_thesis(tmp_path):
    _write(tmp_path, "vid.json", [_thesis(0)])
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(
        '{"id": "youtube/vid#0", "decision": "promoted"}\n'
        '{"id": "youtube/gone#4", "decision": "skipped"}\n', encoding="utf-8")

    report = migrate(tmp_path / "theses", decisions)

    assert report.decisions_dropped == 1
    assert report.dropped_ids == ["youtube/gone#4"]
    assert "youtube/gone#4" not in decisions.read_text()


def test_migrate_handles_missing_decisions_file(tmp_path):
    _write(tmp_path, "vid.json", [_thesis(0)])
    report = migrate(tmp_path / "theses", tmp_path / "nope.jsonl")
    assert report.theses == 1 and report.decisions_remapped == 0
