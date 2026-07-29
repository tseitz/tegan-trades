"""The listing and the selection. Driven against a fake broker, so nothing here is live.

The selection tests carry the weight: this is the only command in the repo that cancels, and
a menu that obeys *part* of what was typed is worse than one that refuses — the reader walks
away believing three orders are gone when two are still holding the budget.
"""
from __future__ import annotations

from datetime import UTC, datetime

from execution.account import Account
from execution.book import parse_positions, parse_resting
from execution.book_cli import main, offer, render, selected

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

    def resting(self):
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
