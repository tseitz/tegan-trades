import brain.cli as cli


def test_extract_main_prints_summary(capsys, monkeypatch):
    from brain.sweep import ExtractResult
    monkeypatch.setattr(cli, "extract_all", lambda **kw: [ExtractResult(person="Cowen")])
    rc = cli.extract_main([])
    assert rc == 0
    assert "Cowen" in capsys.readouterr().out


def test_extract_main_passes_through_flags(monkeypatch):
    captured = {}

    def fake_extract_all(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(cli, "extract_all", fake_extract_all)
    cli.extract_main(["--model", "my-model", "--force", "--concurrency", "5", "--limit", "10"])
    assert captured["model"] == "my-model"
    assert captured["force"] is True
    assert captured["max_workers"] == 5
    assert captured["limit"] == 10


def test_extract_main_defaults(monkeypatch):
    from brain.extract import DEFAULT_MODEL
    from brain.sweep import DEFAULT_MAX_WORKERS
    captured = {}

    def fake_extract_all(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(cli, "extract_all", fake_extract_all)
    cli.extract_main([])
    assert captured["model"] == DEFAULT_MODEL
    assert captured["force"] is False
    assert captured["max_workers"] == DEFAULT_MAX_WORKERS
    assert captured["limit"] is None
