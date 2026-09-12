"""The freshness gate that runs before ``setups`` builds a queue.

Between 2026-09-04 and 2026-09-11 the laptop's rclone token expired, nothing said so, and every
run scored six-day-old bars and re-presented a queue already triaged on 09-05. These cover the
beat that would have caught it, and the two ways it must not misfire: never on a scratch run,
never when the operator asked for no network.
"""
from __future__ import annotations

from datetime import timedelta

from oracle import setups_sync
from oracle.freshness import Freshness


class Spy:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.ok


def _fresh():
    return Freshness(age=timedelta(hours=2))


def _stale():
    return Freshness(age=timedelta(days=6))


def test_a_fresh_cache_pulls_nothing(capsys):
    pull = Spy()
    setups_sync.ensure_fresh(check=_fresh, pull=pull, out=print)
    assert pull.calls == 0


def test_the_age_prints_even_when_fresh(capsys):
    """Printed every run, like `review`'s portfolio age.

    A line that appears only on the bad day is one nobody has learned to read.
    """
    setups_sync.ensure_fresh(check=_fresh, pull=Spy(), out=print)
    assert "2 hours ago" in capsys.readouterr().out


def test_a_stale_cache_pulls(capsys):
    pull = Spy()
    setups_sync.ensure_fresh(check=_stale, pull=pull, out=print)
    assert pull.calls == 1


def test_the_operator_is_told_before_the_wait_not_after(capsys):
    """A pull days behind takes minutes. Silence for minutes reads as a hang."""
    setups_sync.ensure_fresh(check=_stale, pull=Spy(), out=print)
    out = capsys.readouterr().out
    assert "6 days" in out
    assert "pulling" in out.lower()


def test_no_sync_never_pulls_however_stale(capsys):
    pull = Spy()
    setups_sync.ensure_fresh(check=_stale, pull=pull, out=print, enabled=False)
    assert pull.calls == 0


def test_no_sync_still_warns_that_the_data_is_old(capsys):
    """Declining to fix staleness is not a reason to stop reporting it."""
    setups_sync.ensure_fresh(check=_stale, pull=Spy(), out=print, enabled=False)
    assert "STALE" in capsys.readouterr().out


def test_a_failed_pull_warns_and_does_not_raise(capsys):
    """The session must survive an expired token — the judgement in it is the scarce thing."""
    setups_sync.ensure_fresh(check=_stale, pull=Spy(ok=False), out=print)
    assert "could not" in capsys.readouterr().out.lower()


def test_a_failed_pull_reports_it_rather_than_claiming_success(capsys):
    result = setups_sync.ensure_fresh(check=_stale, pull=Spy(ok=False), out=print)
    assert result is False


def test_a_successful_pull_reports_true():
    assert setups_sync.ensure_fresh(check=_stale, pull=Spy(), out=print) is True


def test_an_unknown_age_is_treated_as_stale(capsys):
    """A cache nobody can date is not evidence of freshness."""
    pull = Spy()
    setups_sync.ensure_fresh(check=lambda: Freshness(age=None), pull=pull, out=print)
    assert pull.calls == 1
