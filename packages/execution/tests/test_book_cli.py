"""The listing and the selection. Driven against a fake broker, so nothing here is live.

The selection tests carry the weight: this is the only command in the repo that cancels, and
a menu that obeys *part* of what was typed is worse than one that refuses — the reader walks
away believing three orders are gone when two are still holding the budget.
"""
from __future__ import annotations

from datetime import UTC, datetime

from execution import store
from execution.account import Account
from execution.book import parse_positions, parse_resting, parse_state
from execution.book_cli import main, offer, reconcile, render, selected
from execution.wire import Placement

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

RAW_ORDERS = [
    {"id": "be-1", "client_order_id": "196cc1ef1e5c", "symbol": "BE", "qty": "31",
     "limit_price": "140.3", "side": "buy", "position_intent": "buy_to_open",
     "status": "new", "submitted_at": "2026-07-01T08:00:00Z"},
    {"id": "hood-1", "client_order_id": "b3337c85b916", "symbol": "HOOD", "qty": "63",
     "limit_price": "88.08", "side": "buy", "position_intent": "buy_to_open",
     "status": "new", "submitted_at": "2026-07-29T08:00:00Z"},
]
ORDERS = parse_resting(RAW_ORDERS)
POSITIONS = parse_positions([
    {"symbol": "INTL", "qty": "1639", "side": "long",
     "market_value": "48219.38", "unrealized_pl": "-329.82"},
])
ACCOUNT = Account(equity=99_674.47, buying_power=24_971.52, committed=74_702.95,
                  multiplier=1.0, can_short=False)


class FakeBroker:
    def __init__(self, orders=ORDERS, positions=POSITIONS, account=ACCOUNT, error=None):
        self._orders, self._positions, self._account = orders, positions, account
        self._error = error
        self.cancelled: list[str] = []
        self.joined = None

    def resting(self, order_ids=None):
        # Recorded rather than ignored: the CLI is the only place that reads the order log for
        # this, and passing it is what makes Hyperliquid's listing name its candidates.
        self.joined = order_ids
        return self._orders

    def positions(self):
        return self._positions

    def account(self):
        return self._account

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        return self._error


def _render(**kw):
    kw.setdefault("orders", ORDERS)
    kw.setdefault("positions", POSITIONS)
    kw.setdefault("account", ACCOUNT)
    kw.setdefault("max_age", 14.0)
    kw.setdefault("now", NOW)
    return "\n".join(render(kw.pop("orders"), kw.pop("positions"), **kw))


# ── the listing ─────────────────────────────────────────────────────────────────────────────

def test_the_totals_are_the_point():
    """§40 in one line: nothing summed. Both sections lead with their total."""
    text = _render()
    assert "$24,971.52 free" in text
    assert f"holding ${31 * 140.3 + 63 * 88.08:,.2f}" in text
    assert "$48,219.38" in text


def test_stale_entries_are_flagged_not_hidden():
    text = _render()
    assert "STALE" in text
    # BE is 28 days old, HOOD is 4 hours. Only one is flagged.
    assert text.count("STALE") == 1


def test_nothing_is_flagged_when_the_ceiling_is_generous():
    assert "STALE" not in _render(max_age=90.0)


def test_positions_are_not_numbered():
    """They carry no row number because they are not selectable, and the reason is printed —
    a reader offered a numbered menu will otherwise wonder why this section has none."""
    text = _render()
    assert "will not do it" in text


def test_an_unaskable_venue_says_so_rather_than_reporting_an_empty_book():
    """"Cannot be asked" and "nothing is resting" would otherwise look identical, and one of
    them means the account is free."""
    assert "cannot be asked" in _render(orders=None, positions=None)
    assert "none" in _render(orders=(), positions=())


# ── the selection ───────────────────────────────────────────────────────────────────────────

def test_row_numbers_select_those_rows():
    assert selected("1", ORDERS, max_age=14.0, now=NOW) == (ORDERS[0],)
    assert selected("1 2", ORDERS, max_age=14.0, now=NOW) == tuple(ORDERS)
    assert selected("2,1", ORDERS, max_age=14.0, now=NOW) == (ORDERS[1], ORDERS[0])


def test_empty_selects_nothing():
    for answer in ("", "  ", "n", "no", "none"):
        assert selected(answer, ORDERS, max_age=14.0, now=NOW) == ()


def test_stale_selects_exactly_what_was_flagged():
    assert selected("stale", ORDERS, max_age=14.0, now=NOW) == (ORDERS[0],)


def test_all_selects_everything():
    assert selected("all", ORDERS, max_age=14.0, now=NOW) == tuple(ORDERS)


def test_a_bad_row_aborts_the_whole_selection():
    """Partial obedience is the worst answer available: the reader believes three orders are
    cancelled and two of them are still holding the budget."""
    assert selected("1 9", ORDERS, max_age=14.0, now=NOW) == "there is no row 9"
    assert selected("1 x", ORDERS, max_age=14.0, now=NOW) == "'x' is not a row number"
    assert selected("0", ORDERS, max_age=14.0, now=NOW) == "there is no row 0"


def test_a_repeated_row_is_selected_once():
    assert selected("1 1", ORDERS, max_age=14.0, now=NOW) == (ORDERS[0],)


# ── cancelling ──────────────────────────────────────────────────────────────────────────────

def _answers(*replies):
    queue = list(replies)
    return lambda _prompt: queue.pop(0)


def test_a_confirmed_selection_is_cancelled():
    broker = FakeBroker()
    lines: list[str] = []
    count = offer(broker, ORDERS, max_age=14.0, now=NOW,
                  input_fn=_answers("1", "y"), out=lines.append)
    assert count == 1
    assert broker.cancelled == ["be-1"]


def test_the_confirmation_names_what_is_freed():
    broker = FakeBroker()
    lines: list[str] = []
    offer(broker, ORDERS, max_age=14.0, now=NOW,
          input_fn=_answers("stale", "y"), out=lines.append)
    assert any("$4,349.30" in line for line in lines)


def test_declining_the_confirmation_cancels_nothing():
    broker = FakeBroker()
    offer(broker, ORDERS, max_age=14.0, now=NOW,
          input_fn=_answers("all", "n"), out=lambda _: None)
    assert broker.cancelled == []


def test_a_bad_selection_never_reaches_the_venue():
    broker = FakeBroker()
    offer(broker, ORDERS, max_age=14.0, now=NOW,
          input_fn=_answers("1 9"), out=lambda _: None)
    assert broker.cancelled == []


def test_a_venue_refusal_is_named_per_order():
    """An entry that filled between the listing and the confirmation is no longer cancellable,
    and that is a position the reader now has and did not a moment ago."""
    broker = FakeBroker(error="order is not cancelable")
    lines: list[str] = []
    offer(broker, ORDERS, max_age=14.0, now=NOW,
          input_fn=_answers("1", "y"), out=lines.append)
    assert any("not cancelled" in line for line in lines)


# ── the command ─────────────────────────────────────────────────────────────────────────────

def test_listing_alone_never_prompts_and_never_cancels():
    """``uv run book`` has to be safe to run anywhere, including from a scheduled job."""
    broker = FakeBroker()

    def no_input(_prompt):
        raise AssertionError("listing must not prompt")

    lines: list[str] = []
    assert main([], now=NOW, input_fn=no_input, out=lines.append, broker=broker) == 0
    assert broker.cancelled == []
    assert any("--cancel" in line for line in lines)


def test_cancel_prompts():
    broker = FakeBroker()
    assert main(["--cancel"], now=NOW, input_fn=_answers("1", "y"),
                out=lambda _: None, broker=broker) == 0
    assert broker.cancelled == ["be-1"]


def test_max_age_is_overridable_from_the_flag():
    lines: list[str] = []
    main(["--max-age", "90"], now=NOW, out=lines.append, broker=FakeBroker())
    assert not any("STALE" in line for line in lines)


# ── reconciliation ──────────────────────────────────────────────────────────────────────────
# `placed` is written from the submission reply, and Alpaca runs its buying-power and
# account-type checks at the open. Three of eight orders died that way on 2026-07-29 and the
# log went on saying `placed` for all three.

class StubPlan:
    def __init__(self, key, asset="RKLB"):
        self.candidate_key, self.asset = key, asset
        self.coin, self.direction = asset, "long"
        self.size, self.entry, self.stop, self.target = 108.0, 56.91, 47.7, 151.0
        self.risk, self.notional, self.equity = 995.0, 6_146.28, 100_000.0
        self.capped_from = self.cap_reason = None


def _order(status, legs=(), filled_qty="0", filled_avg_price=None):
    return {"status": status, "filled_qty": filled_qty,
            "filled_avg_price": filled_avg_price,
            "legs": [{"status": s} for s in legs]}


class StatefulBroker(FakeBroker):
    def __init__(self, replies, **kw):
        super().__init__(**kw)
        self._replies = replies
        self.asked: list[str] = []

    def states(self, keys, order_ids=None):
        self.asked = list(keys)
        return {k: parse_state(self._replies.get(k), k) for k in keys}


def _logged(tmp_path, *keys):
    path = tmp_path / "orders.jsonl"
    for key in keys:
        store.record_placement(path, StubPlan(key), Placement(ok=True, order_ids=("1",)),
                               network="paper")
    return path


def test_a_rejection_the_log_called_placed_is_found_and_recorded(tmp_path):
    """The exact failure. RKLB was accepted at 03:47 and rejected at the open; nothing looked."""
    path = _logged(tmp_path, "rklb-1")
    broker = StatefulBroker({"rklb-1": _order("rejected", ("canceled", "canceled"))})
    lines: list[str] = []

    killed = reconcile(broker, path, network="paper", out=lines.append)

    assert killed == 1
    assert any("REJECTED" in line for line in lines)
    row = store.load(path)[-1]
    assert row["outcome"] == store.RECONCILED
    assert row["status"] == "rejected"
    assert row["failed"] is True


def test_a_still_working_order_is_left_alone(tmp_path):
    """A resting entry is supposed to stay on the work list — it has not settled yet."""
    path = _logged(tmp_path, "be-1")
    broker = StatefulBroker({"be-1": _order("new", ("held", "held"))})
    lines: list[str] = []

    assert reconcile(broker, path, network="paper", out=lines.append) == 0
    assert any("still working" in line for line in lines)
    assert store.unsettled_keys(path, network="paper") == {"be-1"}


def test_a_fill_is_settled_without_being_a_failure(tmp_path):
    """It traded. That is the log being right, and it still wants recording so the candidate
    stops being asked about."""
    path = _logged(tmp_path, "vrt-1")
    broker = StatefulBroker({"vrt-1": _order("filled", ("canceled", "filled"),
                                             filled_qty="39", filled_avg_price="243.33")})
    lines: list[str] = []

    assert reconcile(broker, path, network="paper", out=lines.append) == 0
    row = store.load(path)[-1]
    assert row["failed"] is False
    assert row["filled_avg_price"] == 243.33
    assert store.unsettled_keys(path, network="paper") == set()


def test_an_unreadable_order_stays_on_the_list(tmp_path):
    """Recording "unknown" as a verdict would settle the row and lose the question for good."""
    path = _logged(tmp_path, "ghost-1")
    broker = StatefulBroker({})
    lines: list[str] = []

    assert reconcile(broker, path, network="paper", out=lines.append) == 0
    assert any("unreadable" in line for line in lines)
    assert store.unsettled_keys(path, network="paper") == {"ghost-1"}


def test_running_twice_writes_one_verdict(tmp_path):
    path = _logged(tmp_path, "rklb-1")
    broker = StatefulBroker({"rklb-1": _order("rejected", ("canceled", "canceled"))})
    reconcile(broker, path, network="paper", out=lambda _: None)
    reconcile(broker, path, network="paper", out=lambda _: None)
    assert sum(1 for r in store.load(path) if r["outcome"] == store.RECONCILED) == 1


def test_an_empty_log_says_so_without_asking_the_venue(tmp_path):
    broker = StatefulBroker({})
    lines: list[str] = []
    assert reconcile(broker, tmp_path / "none.jsonl", network="paper",
                     out=lines.append) == 0
    assert broker.asked == []
    assert any("nothing awaiting" in line for line in lines)


def test_a_venue_that_cannot_be_asked_says_so_and_records_nothing(tmp_path):
    """Hyperliquid returns None for every key. That must not be read as "all fine"."""
    path = _logged(tmp_path, "hl-1", "hl-2")
    broker = StatefulBroker({})
    lines: list[str] = []

    assert reconcile(broker, path, network="paper", out=lines.append) == 0
    assert any("cannot be asked" in line for line in lines)
    assert all(r["outcome"] == store.PLACED for r in store.load(path))


def test_one_unreadable_order_is_not_reported_as_an_unaskable_venue(tmp_path):
    """The two look identical from the data alone, so the distinction has to come from whether
    ANYTHING answered — otherwise a single 404 reads as "this venue does not support it"."""
    path = _logged(tmp_path, "ghost-1", "be-1")
    broker = StatefulBroker({"be-1": _order("new", ("held", "held"))})
    lines: list[str] = []

    reconcile(broker, path, network="paper", out=lines.append)
    text = "\n".join(lines)
    assert "unreadable" in text
    assert "cannot be asked" not in text


def test_the_reconcile_flag_runs_before_the_listing(tmp_path):
    """Order matters: an order the venue killed is not holding budget, however confidently the
    log says it was placed."""
    path = _logged(tmp_path, "rklb-1")
    broker = StatefulBroker({"rklb-1": _order("rejected", ("canceled", "canceled"))},
                            orders=ORDERS)
    lines: list[str] = []

    main(["--reconcile"], now=NOW, out=lines.append, broker=broker, orders_path=path)
    text = "\n".join(lines)
    assert text.index("RECONCILING") < text.index("RESTING ENTRIES")


def test_the_listing_alone_asks_nothing_about_past_orders(tmp_path):
    """One round trip per placed order is not free, so it stays behind the flag."""
    broker = StatefulBroker({}, orders=ORDERS)
    main([], now=NOW, out=lambda _: None, broker=broker,
         orders_path=_logged(tmp_path, "rklb-1"))
    assert broker.asked == []


# ── phase 2: how the trade ended ────────────────────────────────────────────────────────────
#
# ``reconcile`` settles the ENTRY. This settles the trade. The gap it closes: a candidate left
# every work list at entry fill, so realised P/L was recorded nowhere. See ``execution.outcome``.

from execution import outcome  # noqa: E402
from execution.book_cli import close_out  # noqa: E402
from execution.participation import Depth  # noqa: E402

ENTRY_ID, TP_ID, SL_ID = "f41cbf96", "1fa9b483", "94e89e37"
KEY = "19ba232b91ce"

# The real INTL session: median 19,262 shares/day over 33 sessions.
INTL_DEPTH = Depth(sessions=33, median_volume=19_262.0, median_trades=170.0,
                   median_dollar_volume=597_000.0)


def _feed(*rows):
    return outcome.parse_fills(list(rows))


def _activity(order_id, qty, price, at, side="sell", symbol="INTL"):
    return {"activity_type": "FILL", "order_id": order_id, "qty": str(qty), "price": str(price),
            "side": side, "symbol": symbol, "transaction_time": at}


ENTRY_ACTIVITY = _activity(ENTRY_ID, 1639, 29.621233, "2026-07-29T13:34:57.147565Z", side="buy")
EXIT_ACTIVITY = _activity("c29d3a96", 1639, 31.21, "2026-08-07T13:39:09.192769Z")


class ExitBroker:
    """A broker that answers the two questions the close pass asks."""

    def __init__(self, fills=(), depth=INTL_DEPTH, funding=0.0):
        self._fills = fills
        self._depth = depth
        self._funding = funding

    def fills(self, since):
        self.since = since
        return self._fills

    def depth(self, coin):
        return self._depth

    def funding_paid(self, symbol, *, start, end=None):
        self.funding_window = (start, end)
        self.funding_calls = getattr(self, "funding_calls", [])
        self.funding_calls.append((start, end))
        # 0.0 is the equity answer and a measurement — see ``AlpacaBroker.funding_paid``.
        return self._funding


def _intl_log(tmp_path, *, network="paper", filled_qty=1639.0):
    """A candidate whose entry filled, exactly as the real log has it."""
    path = tmp_path / "orders.jsonl"
    store._append(path, {
        "at": "2026-07-29T03:24:29+00:00", "outcome": store.PLACED, "network": network,
        "candidate_key": KEY, "asset": "INTL", "coin": "INTL", "direction": "long",
        "size": 1639.0, "entry": 29.8, "stop": 29.19, "target": 32.34,
        "risk": 999.79, "notional": 48842.2, "equity": 100000.0,
        "order_ids": [ENTRY_ID, TP_ID, SL_ID],
    })
    store._append(path, {
        "at": "2026-07-29T20:57:47+00:00", "outcome": store.RECONCILED, "network": network,
        "candidate_key": KEY, "status": "filled", "failed": False,
        "filled_qty": filled_qty, "filled_avg_price": 29.621233,
        "leg_statuses": ["new", "held"],
    })
    return path


def _close_out(path, broker, network="paper", lines=None):
    return close_out(broker, path, network=network, max_participation=0.01,
                     out=(lines.append if lines is not None else (lambda _: None)))


def test_a_manual_exit_is_recorded_against_its_candidate(tmp_path):
    """The real trade: bracket cancelled, 1,639 shares sold by hand on 2026-08-07. Nothing tied
    that exit to candidate 19ba232b91ce until this pass existed."""
    path = _intl_log(tmp_path)
    broker = ExitBroker(fills=_feed(ENTRY_ACTIVITY, EXIT_ACTIVITY))
    assert _close_out(path, broker) == 1

    row = next(r for r in store.load(path) if r["outcome"] == store.CLOSED)
    assert row["candidate_key"] == KEY
    assert row["exit_reason"] == outcome.MANUAL
    assert row["exit_price"] == 31.21
    assert row["exit_qty"] == 1639.0
    assert round(row["pnl"], 2) == 2603.99
    assert round(row["r_planned"], 2) == 2.60
    assert round(row["r_at_fill"], 2) == 3.68
    assert round(row["held_days"], 1) == 9.0
    assert row["reconstructed"] is True


def test_a_reconstructed_close_flags_an_uncredible_fill(tmp_path):
    """1,639 shares is 8.51% of a median INTL session against a 1% ceiling, and paper never
    consumes the book. Both reasons this fill is not evidence land on the row."""
    path = _intl_log(tmp_path)
    _close_out(path, ExitBroker(fills=_feed(ENTRY_ACTIVITY, EXIT_ACTIVITY)))
    row = next(r for r in store.load(path) if r["outcome"] == store.CLOSED)
    assert round(row["participation"], 4) == 0.0851
    assert row["paper"] is True
    assert row["credible"] is False


def test_a_target_fill_is_named_as_one(tmp_path):
    path = _intl_log(tmp_path)
    hit = _activity(TP_ID, 1639, 32.34, "2026-08-07T13:39:09Z")
    _close_out(path, ExitBroker(fills=_feed(ENTRY_ACTIVITY, hit)))
    row = next(r for r in store.load(path) if r["outcome"] == store.CLOSED)
    assert row["exit_reason"] == outcome.TARGET


def test_a_stop_fill_is_a_loss_of_about_one_r(tmp_path):
    path = _intl_log(tmp_path)
    stopped = _activity(SL_ID, 1639, 29.19, "2026-07-30T14:00:00Z")
    _close_out(path, ExitBroker(fills=_feed(ENTRY_ACTIVITY, stopped)))
    row = next(r for r in store.load(path) if r["outcome"] == store.CLOSED)
    assert row["exit_reason"] == outcome.STOP
    assert row["r_at_fill"] == -1.0


def test_a_position_still_open_records_nothing(tmp_path):
    """No exit prints means the trade is running. Writing a zero-quantity close would report a
    scratch on a position that is still making or losing money."""
    path = _intl_log(tmp_path)
    assert _close_out(path, ExitBroker(fills=_feed(ENTRY_ACTIVITY))) == 0
    assert not [r for r in store.load(path) if r["outcome"] == store.CLOSED]
    assert store.awaiting_exit_keys(path, network="paper") == {KEY}


def test_a_partial_exit_stays_pending(tmp_path):
    """800 of 1,639 sold. Recording a close here would drop the candidate off the work list with
    839 shares still on the book, and the rest of the exit would never be looked for."""
    path = _intl_log(tmp_path)
    half = _activity("manual", 800, 31.0, "2026-08-07T13:39:09Z")
    assert _close_out(path, ExitBroker(fills=_feed(ENTRY_ACTIVITY, half))) == 0
    assert store.awaiting_exit_keys(path, network="paper") == {KEY}


def test_an_exit_split_across_two_orders_records_one_close_each(tmp_path):
    """Half taken by the target, the rest sold by hand — two different outcomes on one
    candidate. Averaged into a single row they would describe a trade that never happened."""
    path = _intl_log(tmp_path)
    fills = _feed(
        ENTRY_ACTIVITY,
        _activity(TP_ID, 800, 32.34, "2026-08-05T14:00:00Z"),
        _activity("byhand", 839, 30.50, "2026-08-07T13:39:09Z"),
    )
    assert _close_out(path, ExitBroker(fills=fills)) == 2
    closes = [r for r in store.load(path) if r["outcome"] == store.CLOSED]
    assert {r["exit_reason"] for r in closes} == {outcome.TARGET, outcome.MANUAL}
    # Risk is pro-rated by share of the position, so the two R's are comparable and sum sanely.
    assert round(sum(r["risk_planned"] for r in closes), 2) == 999.79


def test_a_venue_that_cannot_report_fills_records_nothing(tmp_path):
    """Hyperliquid sends no ``cloid``, so a fill there cannot be attributed. ``None`` must leave
    the candidate pending — an empty tuple would mean "never closed" and strand it forever."""
    path = _intl_log(tmp_path, network="testnet")
    broker = ExitBroker(fills=None)
    assert _close_out(path, broker, network="testnet") == 0
    assert store.awaiting_exit_keys(path, network="testnet") == {KEY}


def test_an_entry_missing_from_the_feed_window_is_left_pending(tmp_path):
    """Without the entry's own prints the position cannot be dated, and an undated boundary
    would let an unrelated earlier sale of the same symbol count as this trade's exit."""
    path = _intl_log(tmp_path)
    orphan = _activity("c29d3a96", 1639, 31.21, "2026-08-07T13:39:09Z")
    assert _close_out(path, ExitBroker(fills=_feed(orphan))) == 0
    assert store.awaiting_exit_keys(path, network="paper") == {KEY}


def test_a_real_money_close_is_not_flagged_as_paper(tmp_path):
    path = _intl_log(tmp_path, network="live")
    _close_out(path, ExitBroker(fills=_feed(ENTRY_ACTIVITY, EXIT_ACTIVITY)), network="live")
    row = next(r for r in store.load(path) if r["outcome"] == store.CLOSED)
    assert row["paper"] is False
    # Still not credible: 8.51% of a median session is the other, independent reason.
    assert row["credible"] is False


def test_nothing_awaiting_a_close_asks_the_venue_nothing(tmp_path):
    """A quiet night must not spend a request. ``fills`` is one call, but the pass runs nightly
    for the rest of this account's life."""
    path = tmp_path / "orders.jsonl"
    broker = ExitBroker(fills=_feed(EXIT_ACTIVITY))
    assert _close_out(path, broker) == 0
    assert not hasattr(broker, "since")


def test_the_feed_is_asked_from_the_earliest_open_position(tmp_path):
    path = _intl_log(tmp_path)
    broker = ExitBroker(fills=_feed(ENTRY_ACTIVITY, EXIT_ACTIVITY))
    _close_out(path, broker)
    assert broker.since == "2026-07-29"


# ── reading the realised history back ───────────────────────────────────────────────────────

from execution.book_cli import render_closed  # noqa: E402


def _closed_rows(tmp_path):
    path = _intl_log(tmp_path)
    _close_out(path, ExitBroker(fills=_feed(ENTRY_ACTIVITY, EXIT_ACTIVITY)))
    return store.load(path)


def test_the_history_leads_with_the_totals(tmp_path):
    text = "\n".join(render_closed(_closed_rows(tmp_path), network="paper"))
    assert "1 closed" in text
    assert "2,603.99" in text


def test_an_uncredible_fill_is_marked_in_the_listing(tmp_path):
    """The number would otherwise read as a clean +2.6R, which is the exact mistake the flag
    exists to prevent."""
    text = "\n".join(render_closed(_closed_rows(tmp_path), network="paper"))
    assert "NOT EVIDENCE" in text


def test_the_totals_exclude_fills_that_are_not_evidence(tmp_path):
    """A summary that pools a paper fill on 8.5% of a session with real ones is a summary of
    fiction. Both numbers are shown; only the credible one is presented as performance."""
    text = "\n".join(render_closed(_closed_rows(tmp_path), network="paper"))
    assert "0 credible" in text


def test_no_closes_says_so(tmp_path):
    assert "none" in "\n".join(render_closed([], network="paper")).lower()


def test_the_history_is_scoped_to_one_network(tmp_path):
    rows = _closed_rows(tmp_path)
    assert "none" in "\n".join(render_closed(rows, network="live")).lower()


# ── two candidates, one symbol ───────────────────────────────────────────────────────────────
#
# The account-wide feed cannot tell whose sell is whose when two brackets hold the same
# instrument. A manual exit carries an order id this repo never recorded, so there is nothing to
# attribute it by — and guessing would fabricate one close and strand the other position.

OTHER_KEY = "aaaa1111bbbb"
OTHER_ENTRY = "e2222222"


def _two_intl(tmp_path, *, network="paper"):
    """Two separate candidates both long INTL — two zones on one asset, which the engine can
    legitimately produce and no guard forbids."""
    path = _intl_log(tmp_path, network=network)
    store._append(path, {
        "at": "2026-07-30T03:00:00+00:00", "outcome": store.PLACED, "network": network,
        "candidate_key": OTHER_KEY, "asset": "INTL", "coin": "INTL", "direction": "long",
        "size": 100.0, "entry": 30.5, "stop": 29.9, "target": 33.0,
        "risk": 60.0, "notional": 3050.0, "equity": 100000.0,
        "order_ids": [OTHER_ENTRY, "e2tp", "e2sl"],
    })
    store._append(path, {
        "at": "2026-07-30T20:00:00+00:00", "outcome": store.RECONCILED, "network": network,
        "candidate_key": OTHER_KEY, "status": "filled", "failed": False,
        "filled_qty": 100.0, "filled_avg_price": 30.5, "leg_statuses": ["new", "held"],
    })
    return path


def test_an_unattributable_exit_on_a_contested_symbol_closes_nothing(tmp_path):
    """The misattribution this guards: candidate A's filter matches candidate B's sell too, so
    without the check A would be closed on B's fill and B left open forever."""
    path = _two_intl(tmp_path)
    fills = _feed(
        ENTRY_ACTIVITY,
        _activity(OTHER_ENTRY, 100, 30.5, "2026-07-30T13:35:00Z", side="buy"),
        EXIT_ACTIVITY,  # a manual sell of 1639 — whose?
    )
    lines = []
    assert _close_out(path, ExitBroker(fills=fills), lines=lines) == 0
    assert store.awaiting_exit_keys(path, network="paper") == {KEY, OTHER_KEY}
    assert any("cannot be attributed" in ln for ln in lines)


def test_a_contested_symbol_still_closes_on_its_own_bracket_leg(tmp_path):
    """Ambiguity is only about UNKNOWN order ids. A fill on this candidate's own take-profit leg
    names its owner exactly, so contention does not block it."""
    path = _two_intl(tmp_path)
    fills = _feed(
        ENTRY_ACTIVITY,
        _activity(OTHER_ENTRY, 100, 30.5, "2026-07-30T13:35:00Z", side="buy"),
        _activity(TP_ID, 1639, 32.34, "2026-08-07T13:39:09Z"),
    )
    assert _close_out(path, ExitBroker(fills=fills)) == 1
    row = next(r for r in store.load(path) if r["outcome"] == store.CLOSED)
    assert row["candidate_key"] == KEY
    assert row["exit_reason"] == outcome.TARGET
    assert store.awaiting_exit_keys(path, network="paper") == {OTHER_KEY}


def test_an_uncontested_symbol_still_accepts_a_manual_exit(tmp_path):
    """The ordinary case must not regress — one candidate per symbol is how it normally is, and
    a hand-closed trade is exactly what this whole pass was built to capture."""
    path = _intl_log(tmp_path)
    assert _close_out(path, ExitBroker(fills=_feed(ENTRY_ACTIVITY, EXIT_ACTIVITY))) == 1


# ── malformed rows must not take out the rest of the pass ────────────────────────────────────

def test_a_malformed_row_is_skipped_without_stranding_later_candidates(tmp_path):
    """One bad value must cost one candidate, not every candidate sorted after it. This runs
    unattended, and the rows are read from a log that predates several of its own fields."""
    path = _intl_log(tmp_path)
    # Sorts before "19ba..." so it is processed first, and would abort the loop if unguarded.
    store._append(path, {
        "at": "2026-07-29T03:00:00+00:00", "outcome": store.PLACED, "network": "paper",
        "candidate_key": "00bad", "asset": "BAD", "coin": "BAD", "direction": "long",
        "size": 1.0, "entry": 1.0, "stop": "not-a-number", "target": 2.0,
        "risk": 1.0, "order_ids": ["badentry", "badtp", "badsl"],
    })
    store._append(path, {
        "at": "2026-07-29T04:00:00+00:00", "outcome": store.RECONCILED, "network": "paper",
        "candidate_key": "00bad", "status": "filled", "failed": False,
        "filled_qty": 1.0, "filled_avg_price": 1.0, "leg_statuses": ["new", "held"],
    })
    fills = _feed(
        ENTRY_ACTIVITY, EXIT_ACTIVITY,
        _activity("badentry", 1, 1.0, "2026-07-29T13:00:00Z", side="buy", symbol="BAD"),
        _activity("byhand", 1, 2.0, "2026-07-30T13:00:00Z", symbol="BAD"),
    )
    assert _close_out(path, ExitBroker(fills=fills)) == 1
    assert next(r for r in store.load(path)
                if r["outcome"] == store.CLOSED)["candidate_key"] == KEY


def test_a_reconciled_row_with_no_placement_does_not_crash_the_pass(tmp_path):
    """``min()`` over an empty sequence raises. Reachable from a hand-edited log, or a placement
    recorded under a different network than its settlement."""
    path = tmp_path / "orders.jsonl"
    store._append(path, {
        "at": "2026-07-29T20:00:00+00:00", "outcome": store.RECONCILED, "network": "paper",
        "candidate_key": "orphan", "status": "filled", "failed": False,
        "filled_qty": 5.0, "filled_avg_price": 10.0, "leg_statuses": ["new", "held"],
    })
    assert _close_out(path, ExitBroker(fills=_feed(EXIT_ACTIVITY))) == 0


def test_a_gap_collapsed_trade_is_disqualified_on_its_own_merits(tmp_path):
    """VRT's shape, on a LIVE network so paper cannot be doing the work. Planned entry 266.52
    against a 241.18 stop; filled at 243.33, leaving 8.5% of the planned distance. The trade
    that happened was not the trade that was approved."""
    path = tmp_path / "orders.jsonl"
    store._append(path, {
        "at": "2026-07-29T04:00:31+00:00", "outcome": store.PLACED, "network": "live",
        "candidate_key": "vrt1", "asset": "VRT", "coin": "VRT", "direction": "long",
        "size": 39.0, "entry": 266.52, "stop": 241.18, "target": 379.93,
        "risk": 988.26, "order_ids": ["vrtentry", "vrttp", "vrtsl"],
    })
    store._append(path, {
        "at": "2026-07-29T20:00:00+00:00", "outcome": store.RECONCILED, "network": "live",
        "candidate_key": "vrt1", "status": "filled", "failed": False,
        "filled_qty": 39.0, "filled_avg_price": 243.33, "leg_statuses": ["new", "held"],
    })
    fills = _feed(
        _activity("vrtentry", 39, 243.33, "2026-07-29T13:31:52Z", side="buy", symbol="VRT"),
        _activity("vrtsl", 39, 243.44, "2026-07-29T13:32:41Z", symbol="VRT"),
    )
    # A liquid market, so participation cannot be the disqualifier either.
    liquid = Depth(sessions=33, median_volume=2_000_000.0, median_trades=20_000.0,
                   median_dollar_volume=500_000_000.0)
    assert _close_out(path, ExitBroker(fills=fills, depth=liquid), network="live") == 1

    row = next(r for r in store.load(path) if r["outcome"] == store.CLOSED)
    assert row["paper"] is False
    assert round(row["stop_survival"], 3) == 0.085
    assert row["credible"] is False
    assert any("stop distance" in r for r in row["not_evidence"])


# ── costs across an exit that closed in pieces ───────────────────────────────────────────────
#
# Both bugs here were silent: they wrote a wrong number that ``costs_known`` still called
# measured, so the row read as evidence. A single-leg exit hides both, because ``share`` is 1.0.

def _fee_fill(order_id, qty, price, at, fee, side="sell", symbol="INTL", closing=None):
    return outcome.ExitFill(order_id=order_id, symbol=symbol, side=side, qty=qty, price=price,
                            at=datetime.fromisoformat(at), fee=fee, closing=closing)


def test_entry_fees_are_pro_rated_but_a_legs_own_fees_are_not(tmp_path):
    """An exit leg's fee belongs entirely to that leg — only the ENTRY's fee is shared between
    them. Scaling the whole sum by ``share`` charged half of each leg's own fee to nobody, so a
    two-part exit under-reported fees by half of the exit side."""
    path = _intl_log(tmp_path)
    fills = (
        _fee_fill(ENTRY_ID, 1639, 29.621233, "2026-07-29T13:34:57+00:00", 10.0, side="buy"),
        _fee_fill(TP_ID, 800, 32.34, "2026-08-05T14:00:00+00:00", 4.0),
        _fee_fill("byhand", 839, 30.50, "2026-08-07T13:39:09+00:00", 6.0),
    )
    assert _close_out(path, ExitBroker(fills=fills)) == 2
    closes = [r for r in store.load(path) if r["outcome"] == store.CLOSED]
    # 10 entry (split across the two) + 4 + 6 exit = 20, whatever the split.
    assert round(sum(r["fees"] for r in closes), 4) == 20.0


def test_funding_is_read_once_over_the_whole_hold_not_once_per_leg(tmp_path):
    """Called per leg with the same start, the window ``opened -> first exit`` sits inside
    ``opened -> second exit`` and its funding is counted in both rows. Read once over the whole
    hold and pro-rated, the rows sum to what was actually charged."""
    path = _intl_log(tmp_path)
    fills = (
        _fee_fill(ENTRY_ID, 1639, 29.621233, "2026-07-29T13:34:57+00:00", 0.0, side="buy"),
        _fee_fill(TP_ID, 800, 32.34, "2026-08-05T14:00:00+00:00", 0.0),
        _fee_fill("byhand", 839, 30.50, "2026-08-07T13:39:09+00:00", 0.0),
    )
    broker = ExitBroker(fills=fills, funding=-100.0)
    assert _close_out(path, broker) == 2
    closes = [r for r in store.load(path) if r["outcome"] == store.CLOSED]
    assert round(sum(r["funding"] for r in closes), 4) == -100.0
    # One read per candidate, and the window runs to the LAST exit.
    assert len(broker.funding_calls) == 1
    assert broker.funding_calls[0][1] == datetime.fromisoformat("2026-08-07T13:39:09+00:00")
