"""The dashboard's own response shapes — never a View's result object.

ADR-0011: the API never serializes ``ReviewResult`` or ``Portfolio`` directly. This module is
where that translation lives, so a View's internal shape stays free to change (ADR-0005)
without becoming a browser contract. `core.review.Reading` in particular exposes `market_value`,
`pnl` and `pnl_pct` as computed properties, not fields — a reflexive `.model_validate(book)`
would silently drop exactly the three numbers a review screen is largely made of.
"""
from __future__ import annotations

from pydantic import BaseModel


class MandateSummary(BaseModel):
    name: str


class MandateList(BaseModel):
    mandates: list[MandateSummary]


def summarise(book) -> MandateSummary:
    """``book.name`` — the pot's name, not ``book.mandate.name`` (the policy's). See the
    scout's collision row: `Portfolio.name` and `Portfolio.mandate.name` are different things
    that happen to share a type. Untyped like `review.cli.load_books`'s own return: naming
    `oracle.portfolios.Portfolio` here is exactly the import `test_boundaries.py` forbids.
    """
    return MandateSummary(name=book.name)
