"""`ingest-x`'s day loop.

What these pin is the one property the redesign exists for: a day that fails must not
cost the days that succeeded. `ingest-x` is the only command in the repo that spends real
money, and its resume window is capped — so a run that throws away good days because a
later one timed out can lose days permanently.
"""
from __future__ import annotations

import json

import pytest
from ingestion import cli


def _response(handle: str, day: str, post_id: str = "1"):
    """A well-formed xAI response carrying one cited post.

    `x_search_calls` must be non-zero: `harvest` raises `SearchNotRun` on a response that
    is well-formed but never actually searched, which is the silent failure it exists to
    catch. A fixture without it tests the error path by accident.
    """
    url = f"https://x.com/{handle}/status/{post_id}"
    return {
        "status": "completed",
        "usage": {"server_side_tool_usage_details": {"x_search_calls": 1}},
        "output": [
            {"type": "message", "content": [{
                "type": "output_text",
                "text": json.dumps([{
                    "handle": handle, "post_id": post_id, "url": url,
                    "posted_at": day, "text": "a post", "chart": "",
                }]),
                "annotations": [{"type": "url_citation", "url": url,
                                 "start_index": 0, "end_index": 0, "title": "1"}],
            }]},
        ],
    }


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the CLI at a tmp corpus with a one-handle watchlist and no real network."""
    monkeypatch.setattr(cli, "DATA_ROOT", tmp_path / "transcripts")
    monkeypatch.setattr(cli, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "load_watchlist", lambda: {
        "people": [{
            "name": "DonAlt",
            "channels": [{"platform": "x", "id": "DonAlt"}],
        }],
        "x_grok_digest": ["DonAlt"],
    })
    monkeypatch.setattr(cli.x_roster, "search_handles", lambda w: ["DonAlt"])
    monkeypatch.setattr(cli.x_roster, "unattributable", lambda w: [])
    monkeypatch.setattr(cli.x_roster, "undigested", lambda w: [])
    return tmp_path


def test_each_day_in_the_window_is_its_own_call(wired, monkeypatch):
    seen: list[tuple[str, str]] = []

    def fake_search(handles, frm, to, **kw):
        seen.append((frm, to))
        return _response("DonAlt", frm)

    monkeypatch.setattr(cli, "search", fake_search)

    code = cli.x_main(["--from", "2026-08-03", "--to", "2026-08-06"])

    assert code == 0
    # Each request's upper bound is the NEXT day, because `to_date` is exclusive at the
    # tool — `(d, d)` is an empty range that returns nothing while looking like a quiet day.
    assert seen == [("2026-08-03", "2026-08-04"),
                    ("2026-08-04", "2026-08-05"),
                    ("2026-08-05", "2026-08-06")]


def test_a_day_that_fails_does_not_cost_the_days_that_worked(wired, monkeypatch, capsys):
    """The reason this redesign exists. The whole window used to go up as one call, so the
    2026-08-05 timeout threw away three days of posts that had already been paid for."""
    import requests

    def fake_search(handles, frm, to, **kw):
        if frm == "2026-08-04":
            raise requests.exceptions.ReadTimeout("slow")
        return _response("DonAlt", frm)

    monkeypatch.setattr(cli, "search", fake_search)

    code = cli.x_main(["--from", "2026-08-03", "--to", "2026-08-06"])

    assert code == 1, "a failed day must still surface as a failure"
    stored = sorted(p.name for p in (wired / "transcripts" / "x").glob("*.txt"))
    assert stored == ["DonAlt-2026-08-03.txt", "DonAlt-2026-08-05.txt"]


def test_the_cost_line_is_emitted_per_day_so_the_nightly_can_sum_it(wired, monkeypatch,
                                                                    capsys):
    monkeypatch.setattr(cli, "search", lambda h, frm, to, **kw: _response("DonAlt", frm))

    cli.x_main(["--from", "2026-08-03", "--to", "2026-08-06"])

    out = capsys.readouterr().out
    assert out.count("[ingest-x] cost: $") == 3


def test_a_dry_run_spends_the_calls_but_writes_no_corpus(wired, monkeypatch):
    monkeypatch.setattr(cli, "search", lambda h, frm, to, **kw: _response("DonAlt", frm))

    code = cli.x_main(["--from", "2026-08-03", "--to", "2026-08-05", "--dry-run"])

    assert code == 0
    assert not list((wired / "transcripts").rglob("*.txt"))
    # The raw responses ARE kept — a dry run withholds corpus writes, not diagnostics.
    assert len(list((wired / "raw").glob("*.json"))) == 2
