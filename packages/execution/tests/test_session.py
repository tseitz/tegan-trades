"""The seam ``setups_cli`` talks to, exercised end-to-end against a fake broker."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from execution import store
from execution.account import Account
from execution.config import Config
from execution.guards import Refusal
from execution.liquidity import Liquidity
from execution.plan import Market, OrderPlan
from execution.portfolio import Book, combine
from execution import session as session_module
from execution.session import Session, describe, describe_book
from execution.wire import Placement


# ── fakes ───────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StubCandidate:
    asset: str = "ETH"
    direction: str = "long"
    entry: float = 3_200.0
    stop: float = 3_050.0
    target: float = 3_900.0
    key: str = "abc123"


@dataclass(frozen=True)
class StubListing:
    symbol: str = "ETH"
    scale: float | None = None

    @property
    def is_proxy(self) -> bool:
        return self.scale is not None and self.scale != 1.0


class FakeBroker:
    """Implements the ``Broker`` protocol. Records what it was asked to do."""

    def __init__(self, *, equity_by_dex=None, placement=None, live=None, account=None):
        self._equity = equity_by_dex or {"": 10_000.0, "xyz": 500.0}
        self._placement = placement or Placement(ok=True, order_ids=(1, 2, 3))
        self._live = live                 # None -> conservative: every key is still live
        # None is the perp venue's answer — no account-wide budget, so the gate stays off.
        self._account = account
        self.placed: list[OrderPlan] = []
        self.equity_calls: list[str] = []
        self.account_calls = 0
        self.live_keys_calls: list[set] = []
        self.cancelled: list[str] = []

    def live_keys(self, keys) -> set:
        self.live_keys_calls.append(set(keys))
        return set(keys) if self._live is None else set(keys) & set(self._live)

    def markets(self):
        return {
            "ETH": Market(coin="ETH", sz_decimals=4),
            "xyz:GOLD": Market(coin="xyz:GOLD", sz_decimals=2),
        }

    def equity(self, dex: str = "") -> float:
        self.equity_calls.append(dex)
        return self._equity.get(dex, 0.0)

    def account(self):
        self.account_calls += 1
        return self._account

    def resting(self):
        return None

    def positions(self):
        return None

    def cancel(self, order_id: str):
        self.cancelled.append(order_id)
        return None

    def liquidity(self, coin):
        return Liquidity(coin=coin, day_volume=50_000_000.0, open_interest=100_000_000.0,
                         bid_depth=500_000.0, ask_depth=500_000.0, spread=0.0001)

    def depth(self, coin):
        # A perp broker reports no sessions, so the participation cap never engages here.
        # Overridden in the tests that exercise it.
        return None

    def place(self, plan):
        self.placed.append(plan)
        return self._placement


def _session(tmp_path, broker=None, **cfg):
    broker = broker or FakeBroker()
    return Session(
        broker=broker,
        config=Config(**cfg),
        markets=broker.markets(),
        orders_path=tmp_path / "orders.jsonl",
    )


# ── prepare ─────────────────────────────────────────────────────────────────────────────────

def test_prepare_builds_a_plan(tmp_path):
    plan = _session(tmp_path).prepare(StubCandidate(), StubListing())
    assert isinstance(plan, OrderPlan)
    assert plan.coin == "ETH"
    assert plan.equity == 10_000.0


def test_prepare_writes_nothing(tmp_path):
    """It must be safe to call speculatively — the preview depends on it."""
    session = _session(tmp_path)
    session.prepare(StubCandidate(), StubListing())
    assert store.load(session.orders_path) == []
    assert session.broker.placed == []


def test_prepare_refuses_a_candidate_that_already_has_an_order(tmp_path):
    """Re-approving a zone across sessions is ordinary; sending a second bracket is not."""
    session = _session(tmp_path)
    session.already_placed.add("abc123")
    refusal = session.prepare(StubCandidate(), StubListing())
    assert isinstance(refusal, Refusal)
    assert refusal.code == "duplicate"


def test_prepare_passes_refusals_through(tmp_path):
    refusal = _session(tmp_path).prepare(StubCandidate(), None)
    assert isinstance(refusal, Refusal)


# ── equity is per margin pool ───────────────────────────────────────────────────────────────

def test_equity_is_read_per_hip3_margin_pool(tmp_path):
    """Each HIP-3 builder holds its own collateral, so a GOLD trade must size against the
    builder's balance, not the core book's."""
    session = _session(tmp_path)
    assert session.equity("ETH") == 10_000.0
    assert session.equity("xyz:GOLD") == 500.0
    assert session.broker.equity_calls == ["", "xyz"]


def test_equity_is_cached_per_pool(tmp_path):
    """Two approvals in one sitting must not size against two different balances."""
    session = _session(tmp_path)
    session.equity("ETH")
    session.equity("ETH")
    assert session.broker.equity_calls == [""]


# ── execute ─────────────────────────────────────────────────────────────────────────────────

def test_execute_places_and_records(tmp_path):
    session = _session(tmp_path)
    plan = session.prepare(StubCandidate(), StubListing())
    placement = session.execute(plan)

    assert placement.ok is True
    assert session.broker.placed == [plan]
    rows = store.load(session.orders_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == store.PLACED
    assert rows[0]["candidate_key"] == "abc123"


def test_execute_records_a_rejection_too(tmp_path):
    """The case a caller is most likely to forget, and the one most worth having on disk."""
    broker = FakeBroker(placement=Placement(ok=False, error="insufficient margin"))
    session = _session(tmp_path, broker=broker)
    plan = session.prepare(StubCandidate(), StubListing())
    placement = session.execute(plan)

    assert placement.ok is False
    assert store.load(session.orders_path)[0]["outcome"] == store.FAILED


def test_a_failed_placement_does_not_suppress_a_retry(tmp_path):
    """Only a live order blocks a re-send."""
    broker = FakeBroker(placement=Placement(ok=False, error="nope"))
    session = _session(tmp_path, broker=broker)
    session.execute(session.prepare(StubCandidate(), StubListing()))
    assert "abc123" not in session.already_placed


def test_a_successful_placement_blocks_a_re_send_within_the_session(tmp_path):
    session = _session(tmp_path)
    session.execute(session.prepare(StubCandidate(), StubListing()))
    again = session.prepare(StubCandidate(), StubListing())
    assert isinstance(again, Refusal)
    assert again.code == "duplicate"


def test_decline_records_the_reason(tmp_path):
    session = _session(tmp_path)
    session.decline(StubCandidate(), Refusal("unlisted", "no listing"))
    row = store.load(session.orders_path)[0]
    assert row["outcome"] == store.REFUSED
    assert row["reason"] == "unlisted"


# ── the preview ─────────────────────────────────────────────────────────────────────────────

def test_describe_shows_realised_risk_not_the_configured_percent(tmp_path):
    """Flooring the size to a whole lot always leaves the two different. The number shown
    must be the one the venue will act on."""
    session = _session(tmp_path)
    plan = session.prepare(StubCandidate(), StubListing())
    text = describe(plan)
    assert "BUY" in text
    assert "3200" in text
    assert f"{plan.risk:,.2f}" in text
    assert plan.risk < 100.0


def test_describe_labels_a_short_as_sell(tmp_path):
    session = _session(tmp_path)
    plan = session.prepare(
        StubCandidate(direction="short", entry=3_200, stop=3_350, target=2_800),
        StubListing(),
    )
    assert "SELL" in describe(plan)


# ── the duplicate guard asks the venue, not just the log ────────────────────────────────────

def test_a_candidate_whose_bracket_is_gone_is_offered_again(tmp_path):
    """THE BUG. A bracket that filled through its own stop on a gapped open round-trips flat
    within seconds — but it was `ok`, so the log records PLACED. Reading the log alone would
    refuse that candidate forever, burning a setup that never actually traded."""
    orders = tmp_path / "orders.jsonl"
    broker = FakeBroker(live=set())                       # venue: nothing is still working
    plan = OrderPlan(asset="ETH", coin="ETH", direction="long", size=0.03,
                     entry=3_200.0, stop=3_050.0, target=3_900.0, risk=4.5,
                     notional=96.0, equity=10_000.0, candidate_key="abc123")
    store.record_placement(orders, plan, Placement(ok=True, order_ids=(1,)), network="testnet")

    session = Session(
        broker=broker, config=Config(network="testnet"), markets=broker.markets(),
        orders_path=orders,
        already_placed=broker.live_keys(store.placed_keys(orders, network="testnet")),
    )
    assert session.already_placed == set()
    assert broker.live_keys_calls == [{"abc123"}]         # the log's answer was asked about


def test_a_candidate_with_a_working_bracket_is_still_refused(tmp_path):
    """The guard's real job, unchanged: never two live brackets on one candidate."""
    orders = tmp_path / "orders.jsonl"
    broker = FakeBroker(live={"abc123"})
    plan = OrderPlan(asset="ETH", coin="ETH", direction="long", size=0.03,
                     entry=3_200.0, stop=3_050.0, target=3_900.0, risk=4.5,
                     notional=96.0, equity=10_000.0, candidate_key="abc123")
    store.record_placement(orders, plan, Placement(ok=True, order_ids=(1,)), network="testnet")

    session = Session(
        broker=broker, config=Config(network="testnet"), markets=broker.markets(),
        orders_path=orders,
        already_placed=broker.live_keys(store.placed_keys(orders, network="testnet")),
    )
    assert session.already_placed == {"abc123"}
    refusal = session.prepare(StubCandidate(key="abc123"), StubListing())
    assert isinstance(refusal, Refusal)
    assert refusal.code == "duplicate"


# ── the running total ───────────────────────────────────────────────────────────────────────
# `docs/IMPROVEMENTS.md` §40: sizing is per-trade and nothing added it up, so eight approvals
# in one sitting each looked like 1% and together wanted 123.6% of the account.

FUNDED = Account(equity=10_000.0, buying_power=10_000.0, committed=0.0,
                 multiplier=1.0, can_short=True)


def _funded_session(tmp_path, buying_power=10_000.0, **cfg):
    broker = FakeBroker(account=Account(equity=10_000.0, buying_power=buying_power,
                                        committed=10_000.0 - buying_power,
                                        multiplier=1.0, can_short=True))
    return _session(tmp_path, broker, **cfg)


def test_a_placed_order_is_subtracted_from_the_next_ones_headroom(tmp_path):
    """The property the whole entry is about. Before the open an accepted bracket does not
    necessarily reduce buying power at the venue, so re-reading it per candidate would show
    every order the same untouched balance — as it did on 2026-07-29."""
    session = _funded_session(tmp_path, buying_power=3_000.0, max_position_frac=None)
    first = session.prepare(StubCandidate(), StubListing())
    assert isinstance(first, OrderPlan)
    assert first.cap_reason is None          # $2,133 of notional fits $3,000 of room
    session.execute(first)

    # $867 left. The venue would still report $3,000 at this hour; the running total is the
    # only thing that knows otherwise.
    second = session.prepare(StubCandidate(key="second"), StubListing())
    assert isinstance(second, Refusal)
    assert second.code == "no_headroom"


def test_a_refused_placement_does_not_consume_the_budget(tmp_path):
    """Only what the venue took counts. Charging the budget for a rejected order would refuse
    the rest of the night's candidates on the strength of an order that does not exist."""
    broker = FakeBroker(account=FUNDED, placement=Placement(ok=False, error="nope"))
    session = _session(tmp_path, broker)
    plan = session.prepare(StubCandidate(), StubListing())
    session.execute(plan)
    assert session._committed == 0.0


def test_a_venue_with_no_account_leaves_the_budget_gate_off(tmp_path):
    """The perp broker reports None, which must mean "not measured" and never "no money" —
    the same asymmetry ``check_depth`` is built on."""
    session = _session(tmp_path, max_position_frac=None)  # FakeBroker reports no account
    plan = session.prepare(StubCandidate(), StubListing())
    assert isinstance(plan, OrderPlan)
    assert plan.cap_reason is None


def test_a_full_account_refuses_the_next_candidate(tmp_path):
    session = _funded_session(tmp_path, buying_power=50.0)
    refused = session.prepare(StubCandidate(), StubListing())
    assert isinstance(refused, Refusal)
    assert refused.code == "no_headroom"


def test_headroom_is_read_fresh_for_every_candidate(tmp_path):
    """The opposite caching decision to ``equity``, and deliberately: a position filling
    mid-session changes headroom without anyone placing anything."""
    session = _funded_session(tmp_path)
    session.prepare(StubCandidate(), StubListing())
    session.prepare(StubCandidate(key="b"), StubListing())
    assert session.broker.account_calls == 2


# ── the preview ─────────────────────────────────────────────────────────────────────────────

def test_describe_shows_the_account_total(tmp_path):
    """The number that was on nobody's screen. Printed on every order, not only when it binds,
    because the point is to see the total while there is still a decision to make about it."""
    session = _funded_session(tmp_path)
    plan = session.prepare(StubCandidate(), StubListing())
    text = describe(plan, session.account)
    assert "$10,000.00 free" in text
    assert "left after this" in text


def test_describe_says_nothing_about_an_account_it_cannot_read(tmp_path):
    plan = _session(tmp_path).prepare(StubCandidate(), StubListing())
    assert "free" not in describe(plan, None)


def test_describe_names_the_ceiling_that_bound(tmp_path):
    """Four unrelated causes produce the same small number, and each calls for a different
    response — so the line has to say which one, not merely that something was cut."""
    session = _funded_session(tmp_path, max_position_frac=0.20)
    plan = session.prepare(StubCandidate(), StubListing())
    text = describe(plan, session.account)
    assert "capped from" in text
    assert "at most 20% of equity" in text


# ── the pooled risk line ────────────────────────────────────────────────────────────────────

def _plan(risk=1_000.0, key="k"):
    return OrderPlan(asset="ETH", coin="ETH", direction="long", size=1.0, entry=3_200.0,
                     stop=3_050.0, target=3_900.0, risk=risk, notional=3_200.0,
                     equity=100_000.0, candidate_key=key)


def _opened(tmp_path, path, broker, monkeypatch):
    """``Session.open`` against a fake broker. Patched at ``open_broker`` rather than given a
    broker parameter — the seam already exists and production code should not grow one for a
    test."""
    monkeypatch.setattr(session_module, "open_broker", lambda config, creds, dexs=(): broker)
    return Session.open(config=Config(network="testnet"), orders_path=path,
                        credentials=object())


def _book(spent=3_000.0, **kw):
    reported = kw.pop("reported", {"alpaca": 100_000.0})
    return Book(pool=combine(reported), spent=spent, **kw)


def test_the_preview_shows_what_the_whole_book_already_risks():
    line = describe_book(_plan(), _book(3_000.0))
    assert line is not None
    assert "3.00% of $100,000.00" in line


def test_the_preview_shows_where_the_book_lands_after_this_order():
    # The number that was on nobody's screen on 2026-07-29: each order read 1% and the eighth
    # took the account to 7.94%.
    line = describe_book(_plan(1_000.0), _book(3_000.0))
    assert line is not None
    assert "4.00% after this" in line


def test_the_preview_names_the_ceiling_so_the_total_can_be_judged_against_something():
    line = describe_book(_plan(), _book(3_000.0, max_risk=0.05))
    assert line is not None
    assert "ceiling 5%" in line


def test_the_preview_names_a_venue_the_total_could_not_reach():
    """A lower bound presented as a total is exactly how "unmeasured" comes to read as "zero" —
    the failure this whole slice keeps finding. The caveat rides with the number.
    """
    line = describe_book(_plan(), _book(reported={"alpaca": 100_000.0, "hyperliquid": None}))
    assert line is not None
    assert "hyperliquid not counted" in line


def test_the_preview_names_live_orders_whose_risk_is_unrecorded():
    line = describe_book(_plan(), _book(unpriced=2))
    assert line is not None
    assert "2 live order(s)" in line


def test_a_clean_total_carries_no_caveat():
    line = describe_book(_plan(), _book())
    assert line is not None
    assert "!" not in line


def test_there_is_no_book_line_when_the_ceiling_does_not_apply():
    # Rather than a line reading 0% of $0, which would look like an empty account.
    assert describe_book(_plan(), _book(reported={"alpaca": None})) is None


def test_the_book_line_is_separate_from_the_buying_power_line():
    """Two quantities, two lines, deliberately. One pools across venues and one cannot, and a
    reader who conflates them cancels the wrong order.
    """
    text = describe(_plan(), Account(equity=100_000.0, buying_power=25_000.0), _book())
    assert "free" in text and "at risk across" in text


def test_the_preview_is_unchanged_when_no_book_is_supplied():
    text = describe(_plan(), Account(equity=100_000.0, buying_power=25_000.0))
    assert "at risk across" not in text


# ── the risk a session opens with, and what it adds ─────────────────────────────────────────

def test_a_session_opens_carrying_the_risk_of_what_is_still_live(tmp_path, monkeypatch):
    """Seeded from the order log, not from zero. A position opened last night is still at risk,
    and a pooled ceiling that reset itself every evening would be no ceiling at all.
    """
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, _plan(risk=400.0, key="alive"),
                           Placement(ok=True), network="testnet")
    store.record_placement(path, _plan(risk=900.0, key="closed"),
                           Placement(ok=True), network="testnet")

    opened = _opened(tmp_path, path, FakeBroker(live={"alive"}), monkeypatch)
    assert opened.risk_at_stake == 400.0, "the closed candidate is no longer at risk"
    assert opened.risk_unpriced == 0


def test_risk_from_another_network_is_not_carried(tmp_path, monkeypatch):
    # Same reason ``placed_keys`` is scoped: a testnet rehearsal must not spend the real budget.
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, _plan(risk=400.0, key="alive"),
                           Placement(ok=True), network="mainnet")
    opened = _opened(tmp_path, path, FakeBroker(live={"alive"}), monkeypatch)
    assert opened.risk_at_stake == 0.0


def test_a_live_order_with_no_recorded_risk_is_counted_as_unpriced(tmp_path, monkeypatch):
    """Not as zero. An under-counted total *loosens* the pooled ceiling, which is the dangerous
    direction, so the count travels with the number.
    """
    path = tmp_path / "orders.jsonl"
    store._append(path, {"outcome": store.PLACED, "candidate_key": "alive",
                         "network": "testnet"})
    opened = _opened(tmp_path, path, FakeBroker(live={"alive"}), monkeypatch)
    assert opened.risk_at_stake == 0.0
    assert opened.risk_unpriced == 1


def test_a_placed_order_adds_its_risk_to_the_running_total(tmp_path):
    live = _session(tmp_path)
    before = live.risk_at_stake
    live.execute(_plan(risk=250.0, key="new"))
    assert live.risk_at_stake == before + 250.0


def test_a_rejected_order_adds_nothing(tmp_path):
    live = _session(tmp_path, broker=FakeBroker(placement=Placement(ok=False, error="no")))
    live.execute(_plan(risk=250.0, key="new"))
    assert live.risk_at_stake == 0.0
