"""The seam ``setups_cli`` talks to, exercised end-to-end against a fake broker."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from execution import store
from execution.config import Config
from execution.guards import Refusal
from execution.liquidity import Liquidity
from execution.plan import Market, OrderPlan
from execution.session import Session, describe
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

    def __init__(self, *, equity_by_dex=None, placement=None):
        self._equity = equity_by_dex or {"": 10_000.0, "xyz": 500.0}
        self._placement = placement or Placement(ok=True, order_ids=(1, 2, 3))
        self.placed: list[OrderPlan] = []
        self.equity_calls: list[str] = []

    def markets(self):
        return {
            "ETH": Market(coin="ETH", sz_decimals=4),
            "xyz:GOLD": Market(coin="xyz:GOLD", sz_decimals=2),
        }

    def equity(self, dex: str = "") -> float:
        self.equity_calls.append(dex)
        return self._equity.get(dex, 0.0)

    def liquidity(self, coin):
        return Liquidity(coin=coin, day_volume=50_000_000.0, open_interest=100_000_000.0,
                         bid_depth=500_000.0, ask_depth=500_000.0, spread=0.0001)

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
