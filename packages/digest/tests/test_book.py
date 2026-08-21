"""What the account did overnight, read out of the order log.

The log and the queue snapshot stamp time in two different formats, which is the trap these
mostly guard. Everything else is about not reporting an event twice or reporting a stale one as
if it happened last night.
"""
from __future__ import annotations

import pytest
from digest import book


def _lines(rows, **kw) -> list[str]:
    """Just the events. ``lines`` returns ``(events, notice)`` — the notice is not an event and
    is checked explicitly by the tests that care about it."""
    events, _ = book.lines(rows, **kw)
    return events


def _row(at: str, outcome: str, **over) -> dict:
    row = {"at": at, "outcome": outcome, "network": "paper",
           "candidate_key": over.pop("key", "k1"), "asset": over.pop("asset", "GOOG")}
    row.update(over)
    return row


# ── the two clocks ────────────────────────────────────────────────────────────

def test_the_two_timestamp_formats_compare_correctly():
    """The order log writes ``+00:00`` (``store._now``) and the queue snapshot writes ``Z``
    (``setups_cli``). Both are valid ISO-8601 for the same instant, and a plain string compare
    puts ``Z`` after ``+00:00`` for the same second — so a naive cutoff silently drops or
    duplicates whatever landed in that second."""
    rows = [_row("2026-08-20T06:20:11+00:00", book.PLACED)]
    assert book.since(rows, after="2026-08-20T06:20:10Z") == rows
    assert book.since(rows, after="2026-08-20T06:20:12Z") == []


def test_an_unparseable_stamp_is_kept_rather_than_silently_dropped():
    """Dropping it would quietly hide a real fill. Keeping it means the reader sees the event
    and can judge the timestamp themselves."""
    rows = [_row("not a date", book.PLACED)]
    assert book.since(rows, after="2026-08-20T06:20:10Z") == rows


def test_no_cutoff_returns_everything():
    """The first digest has no previous run to bound the window."""
    rows = [_row("2026-08-01T00:00:00+00:00", book.PLACED)]
    assert book.since(rows, after=None) == rows


# ── rendering the events ──────────────────────────────────────────────────────

def test_a_close_reports_how_it_ended_and_what_it_made():
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.CLOSED, asset="GOOG",
                             exit_reason="target", pnl_net=412.5, r_net_at_fill=2.6)])
    assert any("GOOG" in ln and "target" in ln and "412.50" in ln for ln in lines)


def test_a_close_prefers_net_over_gross():
    """The most-skimmed surface in the repo showed gross once and understated a loss by 0.36R.
    ``execution.journal.line`` makes the same choice for the same reason."""
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.CLOSED,
                             pnl=-567.0, pnl_net=-928.0, r_net_at_fill=-1.4)])
    assert any("928" in ln for ln in lines)
    assert not any("567" in ln for ln in lines)


def test_a_close_with_no_net_figure_falls_back_to_gross():
    """Rows written before the net fields existed still have to render."""
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.CLOSED, pnl=-567.0)])
    assert any("567" in ln for ln in lines)


def test_an_uncredible_fill_carries_the_flag():
    """A +2.6R that never had to find a buyer reads exactly like performance. The flag travels
    with the number wherever the number goes — see ``execution.journal``."""
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.CLOSED, pnl_net=1000.0,
                             credible=False, not_evidence=["stop collapsed"])])
    assert any("NOT EVIDENCE" in ln for ln in lines)


def test_a_venue_kill_is_reported_as_a_kill_not_a_fill():
    """``placed`` means the venue accepted the submission, which is weaker than it reads — a
    GTC bracket sent while the market is shut is checked hours later. Three of eight died that
    way on 2026-07-29 while the log still said ``placed``."""
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.RECONCILED, asset="AVAX",
                             failed=True, status="rejected", filled_qty=0)])
    assert any("AVAX" in ln and "rejected" in ln for ln in lines)


def test_a_reconciliation_that_filled_is_reported_as_a_fill():
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.RECONCILED, asset="HYPE",
                             failed=False, status="filled", filled_qty=12.0,
                             filled_avg_price=57.99)])
    assert any("HYPE" in ln and "filled" in ln for ln in lines)


def test_a_reconciliation_that_changed_nothing_is_not_an_event():
    """The nightly reconciles every night. A resting order still resting is not news, and
    printing it would bury the rows that are."""
    assert _lines([_row("2026-08-20T06:00:00+00:00", book.RECONCILED, failed=False,
                        status="accepted", filled_qty=0)]) == []


def test_a_refusal_names_why_pre_flight_stopped_it():
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.REFUSED, asset="GROY",
                             reason="unlisted", detail="GROY has no listing on this venue")])
    assert any("GROY" in ln and "unlisted" in ln for ln in lines)


def test_a_reconciliation_is_named_from_the_placement_it_settles():
    """``store.record_reconciliation`` records ``candidate_key`` and no ``asset`` — so on real
    data every fill and every kill rendered as ``filled ?``, which is the single most important
    line in the section and the one that most needs a name. The placement carries the asset and
    shares the key, so the join is available; nothing has to be re-derived."""
    log = [_row("2026-08-19T00:00:00+00:00", book.PLACED, key="k9", asset="HYPE"),
           _row("2026-08-20T06:00:00+00:00", book.RECONCILED, key="k9", asset=None,
                failed=False, status="filled", filled_qty=12.0)]
    lines = _lines(log[1:], names=book.asset_names(log))
    assert any("HYPE" in ln for ln in lines)


def test_an_unnameable_key_still_renders_the_event():
    """A fill with no matching placement is odd but real — a manual order, or a log truncated
    at the wrong point. Losing the event would be worse than losing the name."""
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.RECONCILED, key="ghost",
                             asset=None, failed=True, status="rejected")], names={})
    assert any("rejected" in ln for ln in lines)


def test_a_row_that_carries_its_own_asset_does_not_need_the_join():
    lines = _lines([_row("2026-08-20T06:00:00+00:00", book.CLOSED, asset="BTC",
                             pnl_net=1.0)], names={"k1": "SHOULD-NOT-WIN"})
    assert any("BTC" in ln for ln in lines)


def test_events_are_ordered_oldest_first():
    lines = _lines([
        _row("2026-08-20T07:00:00+00:00", book.CLOSED, asset="ETH", pnl_net=1.0),
        _row("2026-08-20T06:00:00+00:00", book.CLOSED, asset="BTC", pnl_net=1.0)])
    assert "BTC" in lines[0] and "ETH" in lines[1]


@pytest.mark.parametrize("outcome", [book.PLACED, book.FAILED])
def test_placements_and_failures_render(outcome):
    assert _lines([_row("2026-08-20T06:00:00+00:00", outcome, asset="SOL",
                        error="insufficient buying power")])


# ── the cap, and saying what was cut ──────────────────────────────────────────

def test_the_cap_keeps_the_newest_and_names_what_it_dropped():
    """Only ever binds on the first run, where ``since`` has no bound and returns the whole log.
    Recent events are the ones still actionable, so the tail is what survives."""
    rows = [_row(f"2026-08-{day:02d}T06:00:00+00:00", book.CLOSED, asset=f"A{day}", pnl_net=1.0)
            for day in range(1, 11)]
    events, notice = book.lines(rows, limit=3)
    assert [e.split()[1] for e in events] == ["A8", "A9", "A10"]
    assert "7 older" in notice


def test_the_truncation_notice_is_not_returned_as_an_event():
    """It lived in the events list once, and the subject line counted it — so a capped night
    reported one more event than existed, on the line that has to be dependable."""
    rows = [_row(f"2026-08-{d:02d}T06:00:00+00:00", book.CLOSED, pnl_net=1.0)
            for d in range(1, 6)]
    events, notice = book.lines(rows, limit=2)
    assert len(events) == 2
    assert notice is not None and not any("not shown" in e for e in events)


def test_an_unknown_window_says_so_rather_than_reading_as_a_busy_night():
    """No previous run stamp means this is not one night's activity. Rendered as a plain
    truncation it would present months of history as what happened while you slept."""
    rows = [_row("2026-08-01T06:00:00+00:00", book.CLOSED, pnl_net=1.0)]
    _, notice = book.lines(rows, window_unknown=True)
    assert "WINDOW UNKNOWN" in notice


def test_no_notice_when_nothing_was_cut():
    assert book.lines([], limit=5) == ([], None)


# ── clocks that do not match ──────────────────────────────────────────────────

def test_a_naive_stamp_does_not_crash_the_comparison():
    """Every stamp this repo writes is aware, so a naive one means a hand edit or an external
    tool. Returned naive it raised ``TypeError`` at the first compare, a long way from the
    cause; ``fmt.instant`` assumes UTC instead."""
    rows = [_row("2026-08-20T06:20:11", book.PLACED)]
    assert book.since(rows, after="2026-08-20T06:20:10Z") == rows


def test_naive_and_aware_stamps_sort_together():
    events = _lines([_row("2026-08-20T07:00:00", book.CLOSED, asset="ETH", pnl_net=1.0),
                     _row("2026-08-20T06:00:00+00:00", book.CLOSED, asset="BTC", pnl_net=1.0)])
    assert "BTC" in events[0] and "ETH" in events[1]


def test_an_unparseable_cutoff_returns_everything():
    rows = [_row("2026-08-20T06:20:11+00:00", book.PLACED)]
    assert book.since(rows, after="not a date") == rows


# ── an outcome this module has never seen ─────────────────────────────────────

def test_an_unrecognised_outcome_is_surfaced_not_dropped():
    """These are order outcomes on a real account. Adding a kind to ``execution.store`` would
    otherwise delete it from the digest permanently with nothing anywhere saying so."""
    events = _lines([_row("2026-08-20T06:00:00+00:00", "partially_filled", asset="SOL")])
    assert events and "SOL" in events[0] and "unrecognised" in events[0]
