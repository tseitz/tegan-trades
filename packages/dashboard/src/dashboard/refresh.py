"""Runs the dashboard's free data-refresh fan-out as child processes — ADR-0010.

Each step below is a subprocess, exactly as `scripts/nightly.sh` invokes it, never an import:
`test_boundaries.py` forbids this package from importing `oracle`, so `fetch_cli:main` and
friends are unreachable by call. That constraint turns into a feature here — `.env`, which
holds the execution package's signing key, is loaded inside the CHILD's own address space
(`core.env.load_env`, called from each console script), never this process's. ADR-0010's "the
signing key never enters the web process" stays true because the fan-out is child processes,
not because anyone remembered to keep it true.
"""
from __future__ import annotations

import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from dashboard.assets import REPO_ROOT

StepState = Literal["pending", "running", "succeeded", "failed"]
JobState = Literal["running", "succeeded", "failed"]


@dataclass(frozen=True)
class RefreshStep:
    name: str
    argv: tuple[str, ...]
    timeout: float


REFRESH_STEPS: tuple[RefreshStep, ...] = (
    # An rclone transfer, not an API call: data-pull.sh:86-89 records the sibling direction at
    # 43-65 minutes and trending up. Mirror first, so the fetches below run against whatever it
    # brought down. No flags — `--scheduled` adds gates meant for the unattended poller, and a
    # clicked Refresh must never turn into a silent no-op.
    RefreshStep(name="data-pull", argv=("./scripts/data-pull.sh",), timeout=90 * 60),
    RefreshStep(name="fetch-prices", argv=("uv", "run", "fetch-prices", "--all-portfolios"),
               timeout=15 * 60),
    RefreshStep(name="fetch-funding", argv=("uv", "run", "fetch-funding"), timeout=15 * 60),
    RefreshStep(name="fetch-altsignal", argv=("uv", "run", "fetch-altsignal"), timeout=15 * 60),
)


@dataclass
class StepResult:
    name: str
    state: StepState
    detail: str | None = None


@dataclass
class JobRecord:
    id: str
    state: JobState
    steps: list[StepResult]
    started: datetime
    finished: datetime | None = None


def _tail(text: str, *, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def run_step(step: RefreshStep, *, run=subprocess.run) -> StepResult:
    """Runs one step's `argv` from `REPO_ROOT`. Never raises: a failure the child reports
    (non-zero exit, a timeout) and a failure the child never got to run (`uv` missing from
    PATH — the reason `nightly.sh:37-38` exists at all — or an unexecutable script) both come
    back as a `failed` `StepResult` instead of an exception escaping to the worker thread,
    which would strand the job in `running` forever (AC 4).
    """
    try:
        proc = run(list(step.argv), cwd=REPO_ROOT, capture_output=True, text=True,
                   timeout=step.timeout)
    except subprocess.TimeoutExpired:
        return StepResult(name=step.name, state="failed",
                          detail=f"timed out after {step.timeout:.0f}s")
    except Exception as exc:  # noqa: BLE001 - must not escape, or the worker thread dies quietly
        return StepResult(name=step.name, state="failed", detail=f"{type(exc).__name__}: {exc}")

    if proc.returncode != 0:
        return StepResult(name=step.name, state="failed", detail=_tail(proc.stderr) or None)
    return StepResult(name=step.name, state="succeeded")


class RefreshJobs:
    """The refresh job registry — a dict keyed by job id behind a lock, held on `app.state`
    rather than as a module singleton. `test_api.py` builds a fresh `create_app()` around 35
    times in one process; a module-level registry would be shared across all of them.
    Construction is side-effect free — no thread, no `data/` read — because
    `gen-api-types.sh --print-openapi` builds an app to dump its schema and must not start
    doing work.

    `spawn` and `run` are constructor seams: `run` is the stub point for "no network, no data
    directory" and the recorder the money test reads; `spawn` lets a test replace the thread
    with something synchronous if it ever needs to.
    """

    def __init__(self, *, spawn=threading.Thread, run=subprocess.run):
        self._spawn = spawn
        self._run = run
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}
        self._running_id: str | None = None

    def start(self) -> str:
        """Returns the started-or-joined job's id. A second call while one is in flight joins
        it rather than starting a second — a double-click guard, and nothing more: this
        exclusion is in-memory only, so the nightly on its own schedule or a second `uv run
        dashboard` can still run `fetch-prices` alongside a click. AC 1 still holds either way —
        the caller gets an id immediately and cannot tell the difference.
        """
        with self._lock:
            if self._running_id is not None:
                return self._running_id
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = JobRecord(
                id=job_id,
                state="running",
                steps=[StepResult(name=step.name, state="pending") for step in REFRESH_STEPS],
                started=datetime.now(UTC),
            )
            self._running_id = job_id

        # Spawned after the lock is released — never hold the lock across a run() call. Holding
        # it around a blocking child process while a status poll waits on the same lock is a
        # permanent deadlock, and there is no pytest-timeout in this repo to catch it.
        thread = self._spawn(target=self._work, args=(job_id,), daemon=True)
        thread.start()
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _work(self, job_id: str) -> None:
        """A failing step does not abort the run — same reasoning as `nightly.sh:23-25`. Every
        step runs; each carries its own status; the job's terminal state is `failed` if any
        step failed.
        """
        failed = False
        for index, step in enumerate(REFRESH_STEPS):
            with self._lock:
                self._jobs[job_id].steps[index] = StepResult(name=step.name, state="running")

            result = run_step(step, run=self._run)

            with self._lock:
                self._jobs[job_id].steps[index] = result
            if result.state == "failed":
                failed = True

        with self._lock:
            self._jobs[job_id].state = "failed" if failed else "succeeded"
            self._jobs[job_id].finished = datetime.now(UTC)
            self._running_id = None
