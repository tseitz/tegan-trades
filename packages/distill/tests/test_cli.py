import distill.cli as cli


def test_roster_main_prints_summary(capsys, monkeypatch):
    from distill.roster import DistillResult
    monkeypatch.setattr(cli, "distill_all", lambda **kw: [DistillResult(person="Cowen")])
    rc = cli.roster_main([])
    assert rc == 0
    assert "Cowen" in capsys.readouterr().out


def test_transcript_main_distills_one(capsys, monkeypatch, tmp_path):
    import json
    d = tmp_path / "transcripts" / "youtube"
    d.mkdir(parents=True)
    (d / "vid00000001.txt").write_text("body")
    (d / "vid00000001.json").write_text(json.dumps({
        "platform": "youtube", "source_id": "vid00000001", "person": "P",
        "published_at": "2025-01-01", "url": "u"}))

    captured = {}
    monkeypatch.setattr(cli, "TRANSCRIPTS_ROOT", d.parent)
    monkeypatch.setattr(cli, "extract_theses", lambda text, source, **kw: [])
    monkeypatch.setattr(cli, "save_theses",
                        lambda *a, **k: captured.setdefault("saved", True))
    rc = cli.transcript_main(["vid00000001"])
    assert rc == 0
    assert captured.get("saved")
