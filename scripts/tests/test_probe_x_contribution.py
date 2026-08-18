"""Tests for the attribution in ``probe_x_contribution`` — the part that can be quietly wrong.

The probe answers a spending question, so the failure that matters is the one that flatters X.
Three things do that and none of them looks wrong on inspection: classifying a candidate by its
people rather than its thesis ids (most of the roster posts on both platforms, so nearly
everything would read as X-supported), treating an unresolvable thesis id as YouTube (which
silently moves a candidate out of X-ONLY), and picking the freshest supporting view by string
order rather than by date.

Synthetic ids and rows on purpose, matching ``test_probe_stale_entries``: the probe reads
``data/theses/``, which is gitignored ore, so a test that leaned on it would be testing the
corpus rather than the attribution.
"""
from __future__ import annotations

from dataclasses import dataclass

from probe_x_contribution import classify, freshest_platform, platform_of


@dataclass(frozen=True)
class Row:
    """Just the two fields ``freshest_platform`` reads off a ``CorpusRow``."""
    id: str
    published_at: str


YT_A = "youtube/FO7jissnJMg#813822861a30"
YT_B = "youtube/RqmHc7N-Pq4#aa11bb22cc33"
X_A = "x/DonAlt-2026-08-17#deadbeef1234"
X_B = "x/CryptoCred-2026-08-16#feedface5678"


def test_platform_is_the_id_prefix():
    assert platform_of(X_A) == "x"
    assert platform_of(YT_A) == "youtube"


def test_platform_survives_slashes_and_hashes_in_the_tail():
    """Only the FIRST segment is the platform — a handle containing a slash must not shift it."""
    assert platform_of("x/some/nested-2026-08-17#abc") == "x"


def test_x_only_requires_every_thesis_from_x():
    assert classify((X_A, X_B)) == "X-ONLY"
    assert classify((X_A,)) == "X-ONLY"


def test_one_youtube_thesis_demotes_x_only_to_mixed():
    """The bucket that decides the spend. A candidate YouTube also backs survives without X."""
    assert classify((X_A, YT_A)) == "MIXED"
    assert classify((YT_A, X_A)) == "MIXED", "order must not matter"


def test_no_x_when_nothing_came_from_x():
    assert classify((YT_A, YT_B)) == "NO-X"


def test_freshest_is_by_date_not_by_id_order():
    """X_A sorts after YT_A as a string but is the OLDER post; the newest view decides."""
    by_id = {
        YT_A: Row(YT_A, "2026-08-18"),
        X_A: Row(X_A, "2026-08-17"),
    }
    assert freshest_platform((X_A, YT_A), by_id) == "youtube"
    assert freshest_platform((YT_A, X_A), by_id) == "youtube"


def test_freshest_picks_x_when_x_is_newest():
    by_id = {
        YT_A: Row(YT_A, "2026-08-01"),
        X_A: Row(X_A, "2026-08-17"),
    }
    assert freshest_platform((YT_A, X_A), by_id) == "x"


def test_unresolvable_ids_are_none_not_youtube():
    """A candidate can outlive the theses it is keyed on. Defaulting that to YouTube would
    understate X, which is the direction this probe must not be wrong in."""
    assert freshest_platform((X_A, YT_A), {}) is None


def test_partially_resolvable_uses_only_what_resolves():
    by_id = {X_A: Row(X_A, "2026-08-17")}
    assert freshest_platform((X_A, YT_A), by_id) == "x"
