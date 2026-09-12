"""How old the price cache is, answered without a network call.

The guard these back replaced one that asked the Drive mirror when it was last backed up. That
question is answerable only when the thing it is meant to detect is working — see
``oracle.freshness``' module docstring.
"""
from __future__ import annotations

import os
from datetime import timedelta

from oracle import freshness


def _aged(path, *, days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    when = freshness._now().timestamp() - days * 86400
    os.utime(path, (when, when))


def test_age_of_an_empty_cache_is_unknown(tmp_path):
    assert freshness.check(root=tmp_path).age is None


def test_age_of_a_missing_cache_is_unknown(tmp_path):
    assert freshness.check(root=tmp_path / "nope").age is None


def test_the_newest_file_wins_not_the_oldest(tmp_path):
    """One stale symbol among fresh ones is not a stale cache.

    A fetch skips symbols it cannot route, so old files linger forever beside current ones.
    Reading the oldest would report the cache as years stale on every run.
    """
    _aged(tmp_path / "yahoo" / "OLD.json", days=400)
    _aged(tmp_path / "yahoo" / "NEW.json", days=0.1)
    age = freshness.check(root=tmp_path).age
    assert age is not None
    assert age < timedelta(days=1)


def test_nested_directories_are_searched(tmp_path):
    _aged(tmp_path / "intraday" / "yahoo" / "1h" / "AAPL.json", days=0.2)
    assert freshness.check(root=tmp_path).age is not None


def test_a_cache_fetched_today_is_not_stale(tmp_path):
    _aged(tmp_path / "yahoo" / "AAPL.json", days=0.5)
    assert freshness.check(root=tmp_path).is_stale is False


def test_a_cache_older_than_a_night_is_stale(tmp_path):
    _aged(tmp_path / "yahoo" / "AAPL.json", days=2)
    assert freshness.check(root=tmp_path).is_stale is True


def test_an_unknown_age_is_stale(tmp_path):
    """A cache nobody can date is not evidence of freshness.

    Wrong is worse than missing: an empty cache reporting 'not stale' would let a fresh clone
    score every asset against no bars at all and call the result a queue.
    """
    assert freshness.check(root=tmp_path).is_stale is True


def test_the_line_prints_on_a_fresh_cache_too(tmp_path):
    """Printed every run, not only when stale — `review` prints its portfolio age the same way.

    A warning that appears only on the bad day is one nobody has learned to read.
    """
    _aged(tmp_path / "yahoo" / "AAPL.json", days=0.1)
    assert "prices" in freshness.check(root=tmp_path).message


def test_the_stale_line_says_how_old_and_what_to_do(tmp_path):
    _aged(tmp_path / "yahoo" / "AAPL.json", days=6)
    message = freshness.check(root=tmp_path).message
    assert "6 days" in message
    assert "STALE" in message


def test_an_empty_cache_says_so_rather_than_claiming_an_age(tmp_path):
    assert "never" in freshness.check(root=tmp_path).message.lower()


def test_hours_are_reported_below_a_day(tmp_path):
    _aged(tmp_path / "yahoo" / "AAPL.json", days=0.25)
    assert "hours" in freshness.check(root=tmp_path).message
