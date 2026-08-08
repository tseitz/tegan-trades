"""The order log — what executed, what was blocked, and which candidate each came from."""
from __future__ import annotations

from dataclasses import dataclass, replace

from execution import outcome, store
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


# ── reconciliation ──────────────────────────────────────────────────────────────────────────

class StubState:
    """An ``execution.book.OrderState`` shaped stub — the store reads it structurally."""

    def __init__(self, key="abc123", status="rejected", failed=True, filled_qty=0.0,
                 filled_avg_price=None, legs=("canceled", "canceled")):
        self.candidate_key = key
        self.status = status
        self.failed = failed
        self.filled_qty = filled_qty
        self.filled_avg_price = filled_avg_price
        self.leg_statuses = legs


def test_a_reconciliation_is_appended_not_a_correction(tmp_path):
    """Both facts are true and the timing between them is the story: the submission WAS
    accepted, and it WAS killed six hours later at the open."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1",)), network="paper")
    store.record_reconciliation(path, StubState(), network="paper")

    rows = store.load(path)
    assert [r["outcome"] for r in rows] == [store.PLACED, store.RECONCILED]
    assert rows[1]["status"] == "rejected"
    assert rows[1]["failed"] is True


def test_the_venues_own_word_is_kept_beside_our_reading_of_it(tmp_path):
    """``failed`` is this repo's interpretation; a status Alpaca adds later still has to be
    interpretable from what is on disk."""
    path = tmp_path / "orders.jsonl"
    store.record_reconciliation(
        path, StubState(status="something_new", failed=False), network="paper")
    row = store.load(path)[0]
    assert row["status"] == "something_new"
    assert row["failed"] is False


def test_a_fill_records_its_price(tmp_path):
    """So §39's question — did the open gap the entry away from its plan — stays answerable
    without a second pass over the venue."""
    path = tmp_path / "orders.jsonl"
    store.record_reconciliation(
        path, StubState(status="filled", failed=False, filled_qty=39.0,
                        filled_avg_price=243.33), network="paper")
    row = store.load(path)[0]
    assert row["filled_qty"] == 39.0
    assert row["filled_avg_price"] == 243.33


# ── the work list ───────────────────────────────────────────────────────────────────────────

def test_a_placed_order_is_unsettled(tmp_path):
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1",)), network="paper")
    assert store.unsettled_keys(path, network="paper") == {"abc123"}


def test_a_reconciled_order_drops_off_the_work_list(tmp_path):
    """Otherwise every run re-asks about every order this repo has ever sent, and writes a
    duplicate verdict each time."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1",)), network="paper")
    store.record_reconciliation(path, StubState(), network="paper")
    assert store.unsettled_keys(path, network="paper") == set()


def test_a_re_placed_candidate_becomes_unsettled_again(tmp_path):
    """The case a set of reconciled keys gets wrong. Rejected, the duplicate guard releases
    the candidate, it is approved again and sent again — and the second placement must not be
    marked settled by the first one's verdict."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1",)), network="paper")
    store.record_reconciliation(path, StubState(), network="paper")
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("2",)), network="paper")
    assert store.unsettled_keys(path, network="paper") == {"abc123"}


def test_a_failed_placement_is_never_unsettled(tmp_path):
    """It never reached the book, so there is nothing at the venue to ask about."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=False, error="nope"), network="paper")
    assert store.unsettled_keys(path, network="paper") == set()


def test_the_work_list_is_scoped_to_one_network(tmp_path):
    """A testnet rehearsal's orders cannot be asked about on the mainnet connection, and the
    ids would not resolve there anyway."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1",)), network="testnet")
    assert store.unsettled_keys(path, network="paper") == set()
    assert store.unsettled_keys(path, network="testnet") == {"abc123"}


# ── what is at stake, per candidate ─────────────────────────────────────────────────────────
#
# The only record of it: no venue reports "how much would this position cost if it stopped out",
# because the size and the stop are separate objects there. See ``store.risk_by_key``.

def _placed(tmp_path, rows):
    path = tmp_path / "orders.jsonl"
    for row in rows:
        store._append(path, {"outcome": store.PLACED, **row})
    return path


def test_risk_is_read_back_per_candidate(tmp_path):
    path = _placed(tmp_path, [
        {"candidate_key": "a", "network": "paper", "risk": 999.79},
        {"candidate_key": "b", "network": "paper", "risk": 994.77},
    ])
    assert store.risk_by_key(path, network="paper") == {"a": 999.79, "b": 994.77}


def test_risk_is_scoped_by_network(tmp_path):
    # Same reason ``placed_keys`` is: a testnet rehearsal must not spend the mainnet budget.
    path = _placed(tmp_path, [
        {"candidate_key": "a", "network": "testnet", "risk": 7.0},
        {"candidate_key": "b", "network": "paper", "risk": 999.0},
    ])
    assert store.risk_by_key(path, network="paper") == {"b": 999.0}


def test_the_latest_row_for_a_candidate_wins(tmp_path):
    # A candidate can legitimately be placed twice — a re-entry after a bracket round-tripped
    # flat. Summing both would charge the book for a position that no longer exists.
    path = _placed(tmp_path, [
        {"candidate_key": "a", "network": "paper", "risk": 100.0},
        {"candidate_key": "a", "network": "paper", "risk": 250.0},
    ])
    assert store.risk_by_key(path, network="paper") == {"a": 250.0}


def test_a_row_with_no_risk_is_absent_rather_than_zero(tmp_path):
    """The distinction that matters: "nothing at stake" and "this log cannot say" must not read
    the same. A missing field counted as 0.0 would quietly enlarge the pooled ceiling.
    """
    path = _placed(tmp_path, [{"candidate_key": "a", "network": "paper"}])
    assert store.risk_by_key(path, network="paper") == {}


def test_a_later_row_without_risk_retracts_an_earlier_one(tmp_path):
    path = _placed(tmp_path, [
        {"candidate_key": "a", "network": "paper", "risk": 100.0},
        {"candidate_key": "a", "network": "paper", "risk": None},
    ])
    assert store.risk_by_key(path, network="paper") == {}


def test_refusals_carry_no_risk(tmp_path):
    path = tmp_path / "orders.jsonl"
    store._append(path, {"outcome": store.REFUSED, "candidate_key": "a",
                         "network": "paper", "risk": 100.0})
    assert store.risk_by_key(path, network="paper") == {}


def test_a_missing_log_has_nothing_at_stake(tmp_path):
    assert store.risk_by_key(tmp_path / "absent.jsonl", network="paper") == {}


# ── the close: the second work list, and the outcome row ────────────────────────────────────
#
# The gap this closes: ``unsettled_keys`` drops a candidate the moment ANY reconciled row lands,
# which is at ENTRY fill. So a trade left the work lists before it had an outcome, and realised
# P/L was recorded nowhere. See ``execution.outcome``.

def _filled(key="abc123", qty=1639.0, price=29.621233):
    return StubState(key=key, status="filled", failed=False, filled_qty=qty,
                     filled_avg_price=price, legs=("new", "held"))


def _entered(tmp_path, *, network="paper"):
    """A candidate whose entry has filled and whose exit has not been recorded."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1", "2", "3")),
                           network=network)
    store.record_reconciliation(path, _filled(), network=network)
    return path


# The real INTL numbers throughout, so a wrong sign or a swapped denominator reads as an
# obviously wrong trade rather than as a plausible one.
CLOSE = outcome.TradeClose(
    candidate_key="abc123", symbol="INTL", order_id="c29d3a96", qty=1639.0, price=31.21,
    at=None, reason=outcome.MANUAL, prints=2)
REALIZED = outcome.Realized(
    pnl=2603.99, risk_planned=999.79, risk_at_fill=706.79, r_planned=2.6046, r_at_fill=3.6842)
QUALITY = outcome.FillQuality(participation=0.0851, paper=True, credible=False)


def test_an_entry_that_filled_is_awaiting_its_exit(tmp_path):
    path = _entered(tmp_path)
    assert store.awaiting_exit_keys(path, network="paper") == {"abc123"}


def test_an_order_the_venue_killed_is_never_awaiting_an_exit(tmp_path):
    """It never traded, so there is no position to come off. This is the case that would
    otherwise put every rejected order on the exit work list forever."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1",)), network="paper")
    store.record_reconciliation(path, StubState(), network="paper")  # rejected
    assert store.awaiting_exit_keys(path, network="paper") == set()


def test_a_still_resting_entry_is_not_awaiting_an_exit(tmp_path):
    """It belongs to the ENTRY work list. A candidate must never sit on both — the two phases
    would each write a row about the other's business."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1",)), network="paper")
    assert store.unsettled_keys(path, network="paper") == {"abc123"}
    assert store.awaiting_exit_keys(path, network="paper") == set()


def test_a_closed_trade_drops_off_the_exit_work_list(tmp_path):
    """Otherwise every night re-derives the same close and appends a duplicate outcome."""
    path = _entered(tmp_path)
    store.record_close(path, CLOSE, REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233)
    assert store.awaiting_exit_keys(path, network="paper") == set()


def test_a_re_entered_candidate_awaits_a_second_exit(tmp_path):
    """A candidate can legitimately trade twice. The second entry's outcome must not be
    suppressed by the first one's close — the same trap ``latest_outcomes`` exists for."""
    path = _entered(tmp_path)
    store.record_close(path, CLOSE, REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233)
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("4", "5", "6")),
                           network="paper")
    store.record_reconciliation(path, _filled(), network="paper")
    assert store.awaiting_exit_keys(path, network="paper") == {"abc123"}


def test_a_filled_row_with_no_quantity_is_not_awaiting_an_exit(tmp_path):
    """Defensive: ``filled`` with nothing filled is a contradiction, and treating it as a
    position would have the exit phase hunt forever for fills that cannot exist."""
    path = tmp_path / "orders.jsonl"
    store.record_placement(path, PLAN, Placement(ok=True, order_ids=("1",)), network="paper")
    store.record_reconciliation(path, _filled(qty=0.0), network="paper")
    assert store.awaiting_exit_keys(path, network="paper") == set()


def test_the_exit_work_list_is_scoped_to_one_network(tmp_path):
    path = _entered(tmp_path, network="testnet")
    assert store.awaiting_exit_keys(path, network="paper") == set()
    assert store.awaiting_exit_keys(path, network="testnet") == {"abc123"}


def test_the_entry_work_list_is_unchanged_by_a_close(tmp_path):
    """The invariant this whole change has to protect: ``unsettled_keys`` feeds the duplicate
    guard's sibling and must keep meaning exactly what it meant."""
    path = _entered(tmp_path)
    assert store.unsettled_keys(path, network="paper") == set()
    store.record_close(path, CLOSE, REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233)
    assert store.unsettled_keys(path, network="paper") == set()


def test_a_close_records_the_outcome_and_both_denominators(tmp_path):
    path = tmp_path / "orders.jsonl"
    store.record_close(path, CLOSE, REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233)
    row = store.load(path)[0]
    assert row["outcome"] == store.CLOSED
    assert row["candidate_key"] == "abc123"
    assert row["asset"] == "INTL"
    assert row["exit_reason"] == "manual"
    assert row["exit_price"] == 31.21
    assert row["exit_qty"] == 1639.0
    assert row["entry_price"] == 29.621233
    assert row["pnl"] == 2603.99
    assert row["risk_planned"] == 999.79
    assert row["risk_at_fill"] == 706.79
    assert row["r_planned"] == 2.6046
    assert row["r_at_fill"] == 3.6842


def test_a_close_records_whether_the_fill_was_believable(tmp_path):
    """Without this the INTL row enters calibration as a clean +2.6R. See ``outcome``."""
    path = tmp_path / "orders.jsonl"
    store.record_close(path, CLOSE, REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233)
    row = store.load(path)[0]
    assert row["participation"] == 0.0851
    assert row["paper"] is True
    assert row["credible"] is False


def test_a_reconstructed_close_says_so(tmp_path):
    """``decisions.py``'s rule, carried here: a value derived after the fact must never be
    presented as captured live."""
    path = tmp_path / "orders.jsonl"
    store.record_close(path, CLOSE, REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233, reconstructed=True)
    assert store.load(path)[0]["reconstructed"] is True


def test_a_close_captured_live_is_not_marked_reconstructed(tmp_path):
    path = tmp_path / "orders.jsonl"
    store.record_close(path, CLOSE, REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233)
    assert store.load(path)[0]["reconstructed"] is False


def test_a_close_records_the_holding_period_when_both_ends_are_known(tmp_path):
    from datetime import UTC, datetime
    path = tmp_path / "orders.jsonl"
    entry_at = datetime(2026, 7, 29, 13, 34, 57, tzinfo=UTC)
    exit_at = datetime(2026, 8, 7, 13, 39, 9, tzinfo=UTC)
    store.record_close(path, replace(CLOSE, at=exit_at), REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233, entry_at=entry_at)
    assert round(store.load(path)[0]["held_days"], 2) == 9.0


def test_an_unknown_entry_time_leaves_the_holding_period_absent(tmp_path):
    path = tmp_path / "orders.jsonl"
    store.record_close(path, CLOSE, REALIZED, QUALITY, network="paper",
                       asset="INTL", entry_price=29.621233)
    assert store.load(path)[0]["held_days"] is None
