"""The FastAPI app and its routes. This file is wiring — it decides nothing; every read goes
through `review.cli.load_books`, and every shaping decision lives in `dashboard.wire`.
"""
from __future__ import annotations

import sys
from datetime import UTC, date, datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from review.cli import altsignal_settings, load_books, price_freshness, review_for
from treasury.cli import load_result

from dashboard.refresh import RefreshJobs
from dashboard.wire import (
    MandateList,
    RefreshJobStatus,
    ReviewDocument,
    TreasuryResponse,
    refresh_job_status,
    review_document,
    summarise,
    treasury_response,
)


def mandate_books() -> list:
    """The only way Mandates enter this process. A FastAPI dependency rather than a direct
    call so `test_boundaries`-adjacent tests can override it, and so `test_api` can prove the
    switcher reaches `review.cli.load_books` rather than reading the directory itself.
    """
    return load_books(warn=lambda m: print(f"warning: {m}", file=sys.stderr))


_MANDATE_BOOKS = Depends(mandate_books)


def as_of_today() -> date:
    """One value per request — FastAPI caches a dependency per request, so `mandate_review`
    and `get_mandate_review` see the same day. Without it, `mandate_review` and the header's
    age math each read the clock separately, and a request straddling UTC midnight could
    render a STALE banner computed a day ahead of the grid it sits above.
    """
    return datetime.now(UTC).date()


_AS_OF_TODAY = Depends(as_of_today)


def data_freshness():
    """How old the price cache is, reported once per request — never pulled. The thing that
    pulls, `setups_sync.ensure_fresh`, is behind the boundary wall this package cannot cross
    (`test_boundaries.FORBIDDEN` blocks `oracle.setups_sync`), so "a page load performs no
    sync" stays true by construction here too. A FastAPI dependency, mirroring
    `mandate_books`, so `test_api` can override it instead of stat-walking this machine's
    real `data/prices`.
    """
    return price_freshness()


_DATA_FRESHNESS = Depends(data_freshness)


def altsignal_cfg():
    """Which chains and markets to track, read once per request — a FastAPI dependency,
    mirroring `mandate_books` and `data_freshness`, so `test_api` can override it instead of
    reading this machine's real `cfg/altsignal.yaml`.

    `cfg` is unannotated, like `books` and `freshness` below — an annotation would name
    `oracle.altsignal_config.AltSignalConfig`, which is the `oracle` import
    `test_boundaries.FORBIDDEN` blocks.
    """
    return altsignal_settings()


_ALTSIGNAL_CFG = Depends(altsignal_cfg)


def mandate_review(name: str, books: list = _MANDATE_BOOKS, as_of: date = _AS_OF_TODAY,
                   cfg=_ALTSIGNAL_CFG):
    """The named Mandate's `ReviewResult`, fresh — mirrors `mandate_books` so `test_api` can
    override this one dependency instead of driving a real `review_for` call through it.

    **The alt-signal config is now passed** (#92) — `review_for` fills `chains`, `macro` and
    `yield_notes` from it, the same as the terminal. This is also what makes #90's yield notes
    live: they sit behind the same `if altsignal_cfg is not None` branch in `review_for`.
    **No sync** is still true after #89 too — this package cannot even import
    `oracle.setups_sync` (`test_boundaries.FORBIDDEN` blocks it) — but #89 does add
    *reporting* the price cache's age, via `data_freshness`, which never pulls.
    """
    book = next((b for b in books if b.name == name), None)
    if book is None:
        raise HTTPException(status_code=404, detail=f"no such Mandate: {name!r}")
    [result] = review_for([book], as_of=as_of, altsignal_cfg=cfg)
    return result


_MANDATE_REVIEW = Depends(mandate_review)


def treasury_result(books: list = _MANDATE_BOOKS, as_of: date = _AS_OF_TODAY):
    """The Treasury book's `TreasuryResult`, or `None` when nothing is parked — a FastAPI
    dependency mirroring `mandate_review`, so `test_api` overrides this one thing instead of
    driving the real loader.

    Passes `as_of` and `books` through for the same two reasons `mandate_review` does: one day
    across every dependency in a request (`as_of_today`'s UTC-midnight guard), and no second
    read-and-warn of every portfolio file `_MANDATE_BOOKS` already loaded.

    **This GET is not side-effect-free, and that is accepted deliberately.** `load_result` ->
    `treasury_for` -> `benchmarks.report`, whose first-ever call for a mandate's `flat_rate` or
    `held_flat` benchmark writes an anchor date to `data/benchmarks/anchors.json`
    (`oracle/benchmarks.py:238`). ADR-0010's write line permits it — `data/` is regenerable ore
    — and `uv run treasury` already does exactly this; the browser only makes it reachable from
    a page load, not new.
    """
    return load_result(as_of=as_of, books=books,
                       warn=lambda m: print(f"warning: {m}", file=sys.stderr))


_TREASURY_RESULT = Depends(treasury_result)


def refresh_jobs(request: Request) -> RefreshJobs:
    """The one registry per app instance, off `app.state` — mirrors `mandate_books` so
    `test_refresh.py` can override this one dependency with a stubbed registry instead of
    reaching into module state."""
    return request.app.state.refresh_jobs


_REFRESH_JOBS = Depends(refresh_jobs)

_SAFE_SEC_FETCH_SITE = {"same-origin", "same-site", "none"}


def _refuse_cross_site(request: Request) -> None:
    """POST /api/refresh is unauthenticated and side-effecting — the dashboard has no CORS or
    auth layer by design (ADR-0010, localhost-only), so it is the first route where that
    posture matters: any open tab can otherwise POST here silently and start a ~90-minute
    mirror pull plus three third-party fetches. `Sec-Fetch-Site` is sent by every current
    browser and cannot be set from a page's own JS, so refusing `cross-site` closes the
    drive-by POST without an origin allowlist to keep in sync with `vite.config.ts`'s dev
    proxy. Absent entirely (curl, a script) is let through — this is a browser-CSRF guard, not
    authentication.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in _SAFE_SEC_FETCH_SITE:
        raise HTTPException(status_code=403, detail="cross-site request refused")


def create_app() -> FastAPI:
    app = FastAPI()
    app.state.refresh_jobs = RefreshJobs()

    @app.get("/api/mandates")
    def list_mandates(books: list = _MANDATE_BOOKS) -> MandateList:
        return MandateList(mandates=[summarise(book) for book in books])

    @app.get("/api/mandates/{name}/review")
    def get_mandate_review(result=_MANDATE_REVIEW, as_of: date = _AS_OF_TODAY,
                           freshness=_DATA_FRESHNESS) -> ReviewDocument:
        return review_document(result, as_of=as_of, freshness=freshness)

    @app.get("/api/treasury")
    def get_treasury(result=_TREASURY_RESULT) -> TreasuryResponse:
        return treasury_response(result)

    @app.post("/api/refresh")
    def start_refresh(request: Request, jobs: RefreshJobs = _REFRESH_JOBS) -> RefreshJobStatus:
        _refuse_cross_site(request)
        job_id = jobs.start()
        return refresh_job_status(jobs.get(job_id))

    @app.get("/api/refresh/{job_id}")
    def get_refresh(job_id: str, jobs: RefreshJobs = _REFRESH_JOBS) -> RefreshJobStatus:
        record = jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no such refresh job: {job_id!r}")
        return refresh_job_status(record)

    # Both registered before mount_web(app) — Starlette matches in registration order, so
    # assets.py's catch-all would shadow anything registered after it.
    from dashboard.assets import mount_web

    mount_web(app)

    return app
