"""The FastAPI app and its routes. This file is wiring — it decides nothing; every read goes
through `review.cli.load_books`, and every shaping decision lives in `dashboard.wire`.
"""
from __future__ import annotations

import sys

from fastapi import Depends, FastAPI
from review.cli import load_books

from dashboard.wire import MandateList, summarise


def mandate_books() -> list:
    """The only way Mandates enter this process. A FastAPI dependency rather than a direct
    call so `test_boundaries`-adjacent tests can override it, and so `test_api` can prove the
    switcher reaches `review.cli.load_books` rather than reading the directory itself.
    """
    return load_books(warn=lambda m: print(f"warning: {m}", file=sys.stderr))


_MANDATE_BOOKS = Depends(mandate_books)


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/mandates")
    def list_mandates(books: list = _MANDATE_BOOKS) -> MandateList:
        return MandateList(mandates=[summarise(book) for book in books])

    from dashboard.assets import mount_web

    mount_web(app)

    return app
