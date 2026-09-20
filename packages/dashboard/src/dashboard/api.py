"""The FastAPI app and its routes. This file is wiring — it decides nothing; every read goes
through `review.cli.load_books`, and every shaping decision lives in `dashboard.wire`.
"""
from __future__ import annotations

import sys
from datetime import UTC, date, datetime

from fastapi import Depends, FastAPI, HTTPException
from review.cli import altsignal_settings, load_books, price_freshness, review_for

from dashboard.wire import MandateList, ReviewDocument, review_document, summarise


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


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/mandates")
    def list_mandates(books: list = _MANDATE_BOOKS) -> MandateList:
        return MandateList(mandates=[summarise(book) for book in books])

    @app.get("/api/mandates/{name}/review")
    def get_mandate_review(result=_MANDATE_REVIEW, as_of: date = _AS_OF_TODAY,
                           freshness=_DATA_FRESHNESS) -> ReviewDocument:
        return review_document(result, as_of=as_of, freshness=freshness)

    from dashboard.assets import mount_web

    mount_web(app)

    return app
