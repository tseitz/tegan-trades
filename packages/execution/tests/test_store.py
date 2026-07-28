"""The order log — what executed, what was blocked, and which candidate each came from."""
from __future__ import annotations

from dataclasses import dataclass

from execution import store
from execution.guards import REFUSAL_UNLISTED, Refusal
from execution.plan import OrderPlan
from execution.wire import Placement

PLAN = OrderPlan(
    asset="ETH", coin="ETH", direction="long", size=0.6666,
    entry=3_200.0, stop=3_050.0, target=3_900.0,
    risk=99.99, notional=2_133.12, equity=10_000.0, candidate_key="abc123",
)


@dataclass(frozen=True)
class StubCandidate:
    asset: str = "GOLD"
    direction: str = "long"
    key: str = "gold-long-xyz"


def test_records_a_successful_placement(tmp_path):
    path = tmp_path / "orders.jsonl"
    placement = Placement(ok=True, order_ids=(1, 2, 3), statuses=("resting",) * 3,
                          raw={"status": "ok"})
    store.record_placement(path, PLAN, placement, network="testnet")

    rows = store.load(path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == store.PLACED
    assert rows[0]["candidate_key"] == "abc123"
    assert rows[0]["order_ids"] == [1, 2, 3]
    assert rows[0]["network"] == "testnet"


def test_records_a_rejected_placement_as_failed(tmp_path):
    """A bracket the venue refused still reaches the log — an attempt that vanished is
    indistinguishable from one never made."""
    path = tmp_path / "orders.jsonl"
    placement = Placement(ok=False, error="insufficient margin", raw={"status": "err"})
    store.record_placement(path, PLAN, placement, network="testnet")
    assert store.load(path)[0]["outcome"] == store.FAILED
    assert store.load(path)[0]["error"] == "insufficient margin"


def test_keeps_the_raw_reply_verbatim(tmp_path):
    """The parsed verdict is what gets read; the raw text is the only recourse when the
    parser was wrong, and it cannot be added retroactively."""
    path = tmp_path / "orders.jsonl"
    raw = {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"x": 1}]}}}
    store.record_placement(path, PLAN, Placement(ok=True, raw=raw), network="testnet")
    assert store.load(path)[0]["raw"] == raw


def test_records_a_refusal(tmp_path):
    path = tmp_path / "orders.jsonl"
    store.record_refusal(path, StubCandidate(),
                         Refusal(REFUSAL_UNLISTED, "GOLD has no listing"),
                         network="testnet")
    row = store.load(path)[0]
    assert row["outcome"] == store.REFUSED
    assert row["reason"] == REFUSAL_UNLISTED
    assert row["candidate_key"] == "gold-long-xyz"


def test_is_append_only(tmp_path):
    path = tmp_path / "orders.jsonl"
    for _ in range(3):
        store.record_placement(path, PLAN, Placement(ok=True), network="testnet")
    assert len(store.load(path)) == 3


def test_placed_keys_ignores_refusals_and_failures(tmp_path):
    """Only a live order should suppress a re-send.

    A refused or rejected candidate never reached the book, so it must stay eligible — this
    is the difference between "don't double-send" and "never retry after a failure".
    """
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=(1,)), network="testnet")
    store.record_placement(path, PLAN, Placement(ok=False, error="nope"), network="testnet")
    store.record_refusal(path, StubCandidate(), Refusal(REFUSAL_UNLISTED, "x"),
                         network="testnet")
    assert store.placed_keys(path) == {"abc123"}


def test_missing_file_reads_as_empty(tmp_path):
    assert store.load(tmp_path / "nope.jsonl") == []
    assert store.placed_keys(tmp_path / "nope.jsonl") == set()


# ── the duplicate guard is per-network ──────────────────────────────────────────────────────

def test_a_testnet_placement_does_not_block_the_same_candidate_on_mainnet(tmp_path):
    """Rehearse on testnet, then trade for real — the intended workflow, and the one an
    unfiltered guard breaks by refusing the real order as a duplicate of the practice run."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=(1,)), network="testnet")

    assert store.placed_keys(path, network="testnet") == {"abc123"}
    assert store.placed_keys(path, network="mainnet") == set()


def test_a_mainnet_placement_blocks_a_second_mainnet_send(tmp_path):
    """The case the guard is actually for."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=(1,)), network="mainnet")
    assert store.placed_keys(path, network="mainnet") == {"abc123"}


def test_unfiltered_placed_keys_still_sees_every_network(tmp_path):
    """``network=None`` is for auditing the log, not for gating an order."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True), network="testnet")
    assert store.placed_keys(path) == {"abc123"}
