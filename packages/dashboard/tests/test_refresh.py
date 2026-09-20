from __future__ import annotations

import subprocess
import threading
import time
from types import SimpleNamespace

import dashboard.api as api
import dashboard.assets as assets
from core.review import HOLD, SILENT, Holding, Location, Reading, RosterLean
from dashboard.refresh import REFRESH_STEPS, RefreshJobs
from fastapi.testclient import TestClient
from oracle.portfolios import Benchmark, Mandate, Portfolio, Position
from review.cli import ReviewResult

_MANDATE = Mandate(name="test", benchmarks=(Benchmark(type="held_flat"),),
                   horizon="position", risk_posture="moderate")


def _fake_review_result():
    """A minimal `ReviewResult` for the one test that drives the review endpoint alongside a
    refresh job — same shape as `test_api.py`'s `_grid_result`, kept local rather than
    imported across test modules."""
    reading = Reading(
        holding=Holding(ticker="AAA", shares=1.0, cost=None),
        roster=RosterLean(lean=SILENT, bulls=0, bears=0, people=0, newest=None,
                          age_days=None, voices=(), thin=False),
        location=Location(where="at_resistance", basis="range", position=0.5),
        verdict=HOLD, price=100.0, weekly_trend="uptrend",
    )
    book = Portfolio(
        name="retirement", mandate=_MANDATE,
        positions=(Position(holding=Holding(ticker="AAA", shares=1.0, cost=None),
                            domain="stock"),),
        updated=None,
    )
    return ReviewResult(book=book, readings=[reading], contexts=(), mismatched=(),
                        levels=((), (), 0), chains=(), macro=())


def _fake_freshness():
    return SimpleNamespace(message="prices: fetched 1 hours ago")


def _succeed(argv, *, cwd, capture_output, text, timeout):
    return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")


def _run_with_outcomes(outcomes):
    """`outcomes` maps a step name to a callable `(argv) -> CompletedProcess`, which may also
    raise (e.g. `subprocess.TimeoutExpired`, `FileNotFoundError`). Every other step succeeds."""
    by_argv = {step.argv: step.name for step in REFRESH_STEPS}

    def _run(argv, *, cwd, capture_output, text, timeout):
        name = by_argv[tuple(argv)]
        if name in outcomes:
            return outcomes[name](argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    return _run


def _poll_until_terminal(client, job_id, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/refresh/{job_id}").json()
        if body["state"] != "running":
            return body
        time.sleep(0.01)
    raise AssertionError(f"refresh job {job_id} never reached a terminal state")


def _app_with_registry(monkeypatch, tmp_path, registry):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.refresh_jobs] = lambda: registry
    return TestClient(app)


def test_starting_a_refresh_returns_an_identifier_immediately(monkeypatch, tmp_path):
    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=_succeed))

    response = client.post("/api/refresh")

    assert response.status_code == 200
    assert response.json()["id"]


def test_a_cross_site_post_is_refused(monkeypatch, tmp_path):
    """Any open tab can otherwise POST here silently — the dashboard has no CORS/auth layer by
    design (ADR-0010). `Sec-Fetch-Site: cross-site` is what a drive-by request from another
    origin carries and cannot be scripted around from that page's own JS."""
    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=_succeed))

    response = client.post("/api/refresh", headers={"sec-fetch-site": "cross-site"})

    assert response.status_code == 403


def test_a_same_origin_or_header_absent_post_still_starts_a_refresh(monkeypatch, tmp_path):
    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=_succeed))

    no_header = client.post("/api/refresh")
    same_origin = client.post("/api/refresh", headers={"sec-fetch-site": "same-origin"})

    assert no_header.status_code == 200
    assert same_origin.status_code == 200


def test_status_polls_through_to_succeeded_with_every_step_succeeded(monkeypatch, tmp_path):
    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=_succeed))

    job_id = client.post("/api/refresh").json()["id"]
    body = _poll_until_terminal(client, job_id)

    assert body["state"] == "succeeded"
    assert [s["state"] for s in body["steps"]] == ["succeeded"] * len(REFRESH_STEPS)


def test_a_nonzero_exit_makes_the_job_failed_and_names_the_step(monkeypatch, tmp_path):
    run = _run_with_outcomes({
        "fetch-funding": lambda argv: subprocess.CompletedProcess(argv, 1, stdout="",
                                                                   stderr="boom\n"),
    })
    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=run))

    job_id = client.post("/api/refresh").json()["id"]
    body = _poll_until_terminal(client, job_id)

    assert body["state"] == "failed"
    failing = next(s for s in body["steps"] if s["name"] == "fetch-funding")
    assert failing["state"] == "failed"
    assert "boom" in failing["detail"]


def test_a_timeout_is_reported_as_failed_not_a_500(monkeypatch, tmp_path):
    def _timeout(argv):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    run = _run_with_outcomes({"fetch-prices": _timeout})
    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=run))

    job_id = client.post("/api/refresh").json()["id"]
    body = _poll_until_terminal(client, job_id)

    assert body["state"] == "failed"
    failing = next(s for s in body["steps"] if s["name"] == "fetch-prices")
    assert failing["state"] == "failed"
    assert "timed out" in failing["detail"]


def test_a_run_raising_file_not_found_is_reported_failed_not_stuck_running(monkeypatch, tmp_path):
    def _missing(argv):
        raise FileNotFoundError("uv not on PATH")

    run = _run_with_outcomes({"data-pull": _missing})
    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=run))

    job_id = client.post("/api/refresh").json()["id"]
    body = _poll_until_terminal(client, job_id)

    assert body["state"] == "failed"
    failing = next(s for s in body["steps"] if s["name"] == "data-pull")
    assert failing["state"] == "failed"
    assert "FileNotFoundError" in failing["detail"]


def test_a_later_step_still_runs_after_an_earlier_one_fails(monkeypatch, tmp_path):
    run = _run_with_outcomes({
        "data-pull": lambda argv: subprocess.CompletedProcess(argv, 1, stdout="",
                                                                stderr="down"),
    })
    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=run))

    job_id = client.post("/api/refresh").json()["id"]
    body = _poll_until_terminal(client, job_id)

    assert body["state"] == "failed"
    states_by_name = {s["name"]: s["state"] for s in body["steps"]}
    assert states_by_name["data-pull"] == "failed"
    assert states_by_name["fetch-prices"] == "succeeded"
    assert states_by_name["fetch-funding"] == "succeeded"
    assert states_by_name["fetch-altsignal"] == "succeeded"


def test_a_second_post_while_a_job_is_in_flight_returns_the_same_identifier(monkeypatch, tmp_path):
    hold = threading.Event()
    release = threading.Event()

    def _run(argv, *, cwd, capture_output, text, timeout):
        release.set()
        hold.wait(timeout=5)
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=_run))

    first_id = client.post("/api/refresh").json()["id"]
    assert release.wait(timeout=5), "first step never started"

    second_id = client.post("/api/refresh").json()["id"]

    assert second_id == first_id
    hold.set()
    _poll_until_terminal(client, first_id)


def test_the_review_endpoint_still_answers_200_while_a_job_is_in_flight(monkeypatch, tmp_path):
    hold = threading.Event()
    release = threading.Event()

    def _run(argv, *, cwd, capture_output, text, timeout):
        release.set()
        hold.wait(timeout=5)
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    registry = RefreshJobs(run=_run)
    app.dependency_overrides[api.refresh_jobs] = lambda: registry
    app.dependency_overrides[api.mandate_review] = lambda: _fake_review_result()
    app.dependency_overrides[api.data_freshness] = lambda: _fake_freshness()
    client = TestClient(app)

    client.post("/api/refresh")
    assert release.wait(timeout=5), "refresh never started"

    response = client.get("/api/mandates/retirement/review")

    assert response.status_code == 200
    hold.set()


def test_get_an_unknown_id_is_404(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    client = TestClient(api.create_app())

    response = client.get("/api/refresh/no-such-job")

    assert response.status_code == 404


# ── the money pin (AC 5) ─────────────────────────────────────────────────────

_ALLOWED_UV_COMMANDS = {"fetch-prices", "fetch-funding", "fetch-altsignal"}


def test_only_free_commands_are_reachable_from_the_refresh_endpoint(monkeypatch, tmp_path):
    """#93's criterion: "the set of commands reachable from the refresh endpoint" contains
    nothing that spends metered money. Asserts on what the endpoint actually spawned, not on
    `REFRESH_STEPS` read directly — a constant read by the test is one layer below the seam
    the criterion names, and would still pass if the endpoint stopped using it.
    """
    recorded: list[tuple[str, ...]] = []

    def _run(argv, *, cwd, capture_output, text, timeout):
        recorded.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    client = _app_with_registry(monkeypatch, tmp_path, RefreshJobs(run=_run))

    job_id = client.post("/api/refresh").json()["id"]
    _poll_until_terminal(client, job_id)

    # 1. An exact literal, read from the recording rather than from REFRESH_STEPS — adding any
    #    step fails this until someone edits it and looks at the money list.
    assert recorded == [
        ("./scripts/data-pull.sh",),
        ("uv", "run", "fetch-prices", "--all-portfolios"),
        ("uv", "run", "fetch-funding"),
        ("uv", "run", "fetch-altsignal"),
    ]

    # 2. An allowlist, not a denylist — a denylist naming ingest-x would wave through
    #    ["./scripts/nightly.sh"], which reaches ingest-x, and would never have heard of the
    #    next metered command.
    for argv in recorded:
        if argv == ("./scripts/data-pull.sh",):
            continue
        assert argv[0] == "uv" and argv[1] == "run", argv
        assert argv[2] in _ALLOWED_UV_COMMANDS, argv

    # 3. No shell, no hidden second command.
    for argv in recorded:
        for token in argv:
            assert " " not in token and ";" not in token and "&&" not in token

    # 4. The one shell indirection the argv list cannot see through.
    script_text = (assets.REPO_ROOT / "scripts" / "data-pull.sh").read_text()
    for banned in ("ingest-x", "distill-roster", "brain-extract", "claude -p"):
        assert banned not in script_text
