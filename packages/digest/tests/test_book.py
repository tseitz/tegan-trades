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


# ── what the account is actually in ───────────────────────────────────────────
#
# Everything else in this module is a diff, so a position opened three weeks ago and still open
# produces no line at all. On 2026-08-26 the account held META and HOOD and no digest had ever
# mentioned either. Silence about an open position reads exactly like holding nothing.

def test_a_position_is_built_from_the_placement_and_the_fill():
    """Neither row is enough alone. ``record_reconciliation`` writes no asset, direction, stop
    or target; the placement knows nothing about what actually filled."""
    rows = [_row("2026-07-31T18:19:31+00:00", book.PLACED, asset="META", direction="long",
                 entry=575.81, stop=516.07, target=800.0),
            _row("2026-08-05T03:10:27+00:00", book.RECONCILED, asset=None,
                 filled_qty=16.0, filled_avg_price=550.75)]
    held = book.holdings(rows, {"k1"})
    assert len(held) == 1
    it = held[0]
    assert it.asset == "META" and it.direction == "long"
    assert it.qty == 16.0 and it.fill_price == 550.75
    assert it.stop == 516.07 and it.target == 800.0
    assert it.settled_at == "2026-08-05T03:10:27+00:00"


def test_only_the_keys_asked_for_come_back():
    """The caller decides what is open — it reads ``store.awaiting_exit_keys``, which knows
    about exits this module cannot see from the placement rows alone."""
    rows = [_row("2026-07-31T18:19:31+00:00", book.PLACED, key="open", asset="META"),
            _row("2026-07-31T18:19:31+00:00", book.PLACED, key="shut", asset="SOL")]
    assert [h.asset for h in book.holdings(rows, {"open"})] == ["META"]


def test_a_paper_position_says_so():
    """The same rule the closed line follows: the flag travels with the number. A paper fill
    that never had to find a buyer reads exactly like a real one."""
    rows = [_row("2026-07-31T18:19:31+00:00", book.PLACED, asset="META", network="paper")]
    assert book.holdings(rows, {"k1"})[0].paper is True
    live = [_row("2026-07-31T18:19:31+00:00", book.PLACED, asset="META", network="mainnet")]
    assert book.holdings(live, {"k1"})[0].paper is False


def test_a_position_with_no_fill_recorded_still_appears():
    """It is open either way, and the stop is the number worth reading. Dropping it because one
    field is missing would hide a real commitment."""
    rows = [_row("2026-07-31T18:19:31+00:00", book.PLACED, asset="META", stop=516.07)]
    it = book.holdings(rows, {"k1"})[0]
    assert it.qty is None and it.fill_price is None
    assert it.stop == 516.07


def test_holdings_are_sorted_by_asset():
    rows = [_row("2026-07-31T18:19:31+00:00", book.PLACED, key="a", asset="META"),
            _row("2026-07-31T18:19:31+00:00", book.PLACED, key="b", asset="HOOD")]
    assert [h.asset for h in book.holdings(rows, {"a", "b"})] == ["HOOD", "META"]


def test_the_newest_fill_wins_when_a_key_reconciles_twice():
    """The nightly reconciles every night, so a long-held position accumulates rows. An older
    partial fill would understate the size."""
    rows = [_row("2026-07-31T18:19:31+00:00", book.PLACED, asset="META"),
            _row("2026-08-01T03:00:00+00:00", book.RECONCILED, asset=None, filled_qty=8.0,
                 filled_avg_price=560.0),
            _row("2026-08-05T03:10:27+00:00", book.RECONCILED, asset=None, filled_qty=16.0,
                 filled_avg_price=550.75)]
    it = book.holdings(rows, {"k1"})[0]
    assert it.qty == 16.0 and it.fill_price == 550.75


def test_nothing_open_is_an_empty_tuple_not_a_guess():
    assert book.holdings([], set()) == ()


# ── what the position is worth now ────────────────────────────────────────────
#
# The section named the fill and the stop and stopped there, so it said what was committed and
# never what it had become. The mark arrives from the price cache; the arithmetic is here.

def _open(**over):
    kw = {"asset": "HOOD", "direction": "long", "qty": 63.0, "fill_price": 87.79}
    kw.update(over)
    return book.Holding(**kw)


def test_a_long_gains_when_the_mark_is_above_the_fill():
    it = _open(mark=104.26)
    assert it.pnl == pytest.approx(1037.61)
    assert it.pnl_pct == pytest.approx(0.18761, rel=1e-4)


def test_a_short_gains_when_the_mark_is_below_the_fill():
    """The sign has to follow the direction. A short priced with the long's arithmetic reports
    a winning trade as a loss, on the one number a reader takes at face value."""
    assert _open(direction="short", mark=80.0).pnl == pytest.approx(490.77)


def test_percent_is_measured_against_what_the_entry_cost():
    """Not against the margin posted. On a leveraged venue those differ by the leverage, and
    the entry notional is the only one of the two this module can see."""
    assert _open(qty=2.0, fill_price=100.0, mark=110.0).pnl_pct == pytest.approx(0.10)


def test_a_position_nothing_has_priced_has_no_profit_figure():
    """An asset the price cache has never seen still prints — as a position with no number,
    never as a position at break-even."""
    it = _open()
    assert it.mark is None and it.pnl is None and it.pnl_pct is None


def test_a_position_with_no_fill_recorded_has_no_profit_figure():
    assert _open(qty=None, fill_price=None, mark=104.26).pnl is None


def test_marks_are_attached_by_asset_and_leave_the_rest_alone():
    held = book.priced((_open(), _open(asset="META", fill_price=550.75, qty=16.0)),
                       {"HOOD": 104.26})
    assert [h.mark for h in held] == [104.26, None]
    assert held[0].stop == _open().stop


def test_the_book_total_says_how_many_rows_it_could_not_price():
    """A total quietly missing a position reads as the whole book, which is worse than a total
    that admits its own hole."""
    gain, share, unpriced = book.totals(
        (_open(qty=2.0, fill_price=100.0, mark=110.0), _open(asset="META")))
    assert gain == pytest.approx(20.0)
    assert share == pytest.approx(0.10)
    assert unpriced == 1


def test_a_book_with_nothing_priced_reports_no_total():
    assert book.totals((_open(),)) == (None, None, 1)
