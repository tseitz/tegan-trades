"""The FastAPI app and its routes. This file is wiring — it decides nothing; every read goes
through `review.cli.load_books`, and every shaping decision lives in `dashboard.wire`.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException
from review.cli import load_books, review_for

from dashboard.wire import MandateList, ReviewDocument, review_document, summarise


def mandate_books() -> list:
    """The only way Mandates enter this process. A FastAPI dependency rather than a direct
    call so `test_boundaries`-adjacent tests can override it, and so `test_api` can prove the
    switcher reaches `review.cli.load_books` rather than reading the directory itself.
    """
    return load_books(warn=lambda m: print(f"warning: {m}", file=sys.stderr))


_MANDATE_BOOKS = Depends(mandate_books)


def mandate_review(name: str, books: list = _MANDATE_BOOKS):
    """The named Mandate's `ReviewResult`, fresh — mirrors `mandate_books` so `test_api` can
    override this one dependency instead of driving a real `review_for` call through it.

    Two things #87 deliberately leaves out, each owned by a later ticket: **no alt-signal
    config** (#92) — `review_for` is called with `altsignal_cfg=None`, so `chains`, `macro`
    and `yield_notes` come back empty. **No sync** (#89) — this package cannot even import
    `oracle.setups_sync` (`test_boundaries.FORBIDDEN` blocks it), so "a page load performs no
    sync" is true by construction, not by an `if` this file remembered to write.
    """
    book = next((b for b in books if b.name == name), None)
    if book is None:
        raise HTTPException(status_code=404, detail=f"no such Mandate: {name!r}")
    [result] = review_for([book], as_of=datetime.now(UTC).date())
    return result


_MANDATE_REVIEW = Depends(mandate_review)


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/mandates")
    def list_mandates(books: list = _MANDATE_BOOKS) -> MandateList:
        return MandateList(mandates=[summarise(book) for book in books])

    @app.get("/api/mandates/{name}/review")
    def get_mandate_review(result=_MANDATE_REVIEW) -> ReviewDocument:
        return review_document(result, as_of=datetime.now(UTC).date())

    from dashboard.assets import mount_web

    mount_web(app)

    return app
