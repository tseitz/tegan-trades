"""Horizon -> term, and how long a view of each horizon stays current. Pure."""
from __future__ import annotations

from datetime import date

from core.rank import parse_date

TERMS = ("near", "long", "unset")

_TERM_OF = {"scalp": "near", "swing": "near", "position": "long", "macro": "long"}


def term_of(horizon: str | None) -> str:
    """`near` = scalp+swing, `long` = position+macro, anything else (null, unknown) `unset`."""
    return _TERM_OF.get(horizon or "", "unset")


# How long a view of each horizon stays meaningfully "current". A four-month-old swing
# call is stale; a four-month-old macro call is not. Without this, a view someone last
# stated 14 months ago prints identically to one from last week — observed live, where
# TraderMayne's most recent BTC statement was 2025-05-29 and still labelled current.
STALE_AFTER_DAYS = {"scalp": 14, "swing": 45, "position": 240, "macro": 540}
_DEFAULT_STALE_DAYS = 120


def is_stale(published_at: str | None, horizon: str | None, today: date | None) -> bool:
    """Whether a view has outlived its horizon. An undated view counts as stale.

    Public because ``digest.roster`` needs the same verdict to say how much of a lean count is
    archaeology. A second copy of these limits elsewhere would drift from this one.
    """
    if today is None:
        return False
    published = parse_date(published_at)
    if published is None:
        return True
    limit = STALE_AFTER_DAYS.get(horizon or "", _DEFAULT_STALE_DAYS)
    return (today - published).days > limit
