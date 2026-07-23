from datetime import date
import ingestion.cli as cli
from ingestion.roster import ChannelResult


def test_roster_main_prints_summary(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_watchlist", lambda *a, **k: {"people": []})
    monkeypatch.setattr(
        cli, "ingest_roster",
        lambda wl, **k: [ChannelResult("Alice", "@alice")],
    )
    rc = cli.roster_main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Alice" in out


def test_channel_main_builds_target_and_reports(capsys, monkeypatch):
    captured = {}

    def fake_ingest_channel(target, **kwargs):
        captured["channel"] = target.channel
        captured["max_videos"] = target.max_videos
        r = ChannelResult(target.person, target.channel)
        r.ingested.append("vid1")
        return r

    monkeypatch.setattr(cli, "ingest_channel", fake_ingest_channel)
    rc = cli.channel_main(["@alice", "--max-videos", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["channel"] == "@alice"
    assert captured["max_videos"] == 5
    assert "1 ingested" in out
