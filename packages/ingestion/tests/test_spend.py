"""The real-money ledger.

`ingest-x` is the only command in the repo that spends dollars, and the nightly cycle's
monthly cap gates on this file. It used to be written by `nightly.sh` *about* ingest-x,
which meant every manual run was invisible to the cap: measured 2026-08-06, the tracked
August total was $3.07 against $7.18 actually spent, and July $5.55 against $7.44.
"""
from __future__ import annotations

import json

from ingestion import spend


def test_recording_accumulates_within_a_month(tmp_path):
    p = tmp_path / "spend.json"
    spend.record(0.50, month="2026-08", path=p)
    total = spend.record(0.25, month="2026-08", path=p)

    assert total == 0.75
    assert spend.total("2026-08", path=p) == 0.75


def test_months_are_kept_apart(tmp_path):
    p = tmp_path / "spend.json"
    spend.record(1.00, month="2026-07", path=p)
    spend.record(0.25, month="2026-08", path=p)

    assert spend.total("2026-07", path=p) == 1.00
    assert spend.total("2026-08", path=p) == 0.25


def test_an_unseen_month_is_zero_not_an_error(tmp_path):
    assert spend.total("2026-12", path=tmp_path / "nope.json") == 0.0


def test_a_corrupt_ledger_does_not_stop_the_command_that_spends(tmp_path):
    """The ledger is bookkeeping. A truncated write must not stop ingestion — losing a day
    of unrecoverable X posts to protect an accounting file is the wrong trade. It is
    replaced rather than appended to, so the damage is bounded to what was already lost."""
    p = tmp_path / "spend.json"
    p.write_text("{not json", encoding="utf-8")

    total = spend.record(0.25, month="2026-08", path=p)

    assert total == 0.25
    assert json.loads(p.read_text())["2026-08"] == 0.25


def test_history_carries_over_from_the_legacy_location(tmp_path):
    """The ledger moved out of data/logs/nightly/ when ingest-x took ownership of it. A
    move that silently reset the running total would hand back exactly the under-counting
    this change exists to fix, so prior months are read across once."""
    legacy = tmp_path / "logs" / "nightly" / "spend.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"2026-07": 7.44, "2026-08": 3.07}), encoding="utf-8")
    p = tmp_path / "spend.json"

    total = spend.record(0.82, month="2026-08", path=p, legacy=legacy)

    assert total == 3.89, "the new month must build on what was already tracked"
    assert spend.total("2026-07", path=p) == 7.44


def test_the_legacy_file_is_not_re_read_once_migrated(tmp_path):
    """Reading it again on every call would re-add history each time."""
    legacy = tmp_path / "old.json"
    legacy.write_text(json.dumps({"2026-08": 3.00}), encoding="utf-8")
    p = tmp_path / "spend.json"

    spend.record(1.00, month="2026-08", path=p, legacy=legacy)
    total = spend.record(1.00, month="2026-08", path=p, legacy=legacy)

    assert total == 5.00


class TestReconcile:
    """Rebuilding the ledger from what the responses themselves reported.

    Needed because the ledger can only ever be a floor: a timed-out call bills at xAI and
    returns nothing to read a cost from. Reconciling from `data/raw/x/` recovers everything
    whose response *did* arrive, which is how the $3.07-vs-$7.18 gap was found.
    """

    def _raw(self, root, name, ticks, month="2026-08"):
        root.mkdir(parents=True, exist_ok=True)
        p = root / name
        p.write_text(json.dumps({"usage": {"cost_in_usd_ticks": ticks}}), encoding="utf-8")
        return p

    def test_sums_what_each_response_reported(self, tmp_path):
        raw = tmp_path / "raw"
        self._raw(raw, "a.json", 2_500_000_000)   # $0.25
        self._raw(raw, "b.json", 5_000_000_000)   # $0.50
        led = tmp_path / "spend.json"

        found = spend.reconcile(raw_dir=raw, path=led)

        assert round(sum(found.values()), 2) == 0.75

    def test_never_lowers_a_recorded_total(self, tmp_path):
        """Raw responses are ore and are not pruned, but if one ever went missing the
        ledger must not quietly forget spend it already knew about. The reconciled figure
        is a floor taken against the existing one, not a replacement for it."""
        raw = tmp_path / "raw"
        self._raw(raw, "a.json", 1_000_000_000)   # $0.10
        led = tmp_path / "spend.json"
        led.write_text(json.dumps({"2026-08": 99.0}), encoding="utf-8")

        spend.reconcile(raw_dir=raw, path=led)

        assert spend.total("2026-08", path=led) == 99.0

    def test_a_response_with_no_cost_field_is_skipped_not_counted_as_zero(self, tmp_path):
        raw = tmp_path / "raw"
        (raw).mkdir(parents=True)
        (raw / "no_usage.json").write_text(json.dumps({"output": []}), encoding="utf-8")
        self._raw(raw, "a.json", 1_000_000_000)
        led = tmp_path / "spend.json"

        found = spend.reconcile(raw_dir=raw, path=led)

        assert round(sum(found.values()), 2) == 0.10

    def test_a_missing_raw_dir_is_not_an_error(self, tmp_path):
        assert spend.reconcile(raw_dir=tmp_path / "nope", path=tmp_path / "s.json") == {}
