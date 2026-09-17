from datetime import UTC, date, datetime

import pytest
from oracle import portfolios
from oracle.treasury_file import TreasuryError, load

GOOD = """\
mandate:
  name: treasury
  benchmarks:
    - type: flat_rate
      rate: 3.1
  horizon: macro
  risk_posture: conservative

updated: 2026-09-16

parked:
  - what: USDC
    amount: 5000.00
    venue: aave-v3
    apy: 4.21
    since: 2026-08-01
  - what: USDC
    amount: 1200.00
    venue: sofi
"""


def _write(tmp_path, body):
    path = tmp_path / "treasury.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_missing_file_returns_none(tmp_path):
    assert load(path=tmp_path / "treasury.yaml") is None


def test_a_good_file_loads(tmp_path):
    path = _write(tmp_path, GOOD)
    book = load(path=path)
    assert book.mandate.name == "treasury"
    assert book.updated == date(2026, 9, 16)
    assert [r.what for r in book.rows] == ["USDC", "USDC"]
    assert book.rows[0].venue == "aave-v3"
    assert book.rows[0].amount == 5000.00
    assert book.rows[0].apy == 4.21
    assert book.rows[0].since == date(2026, 8, 1)
    assert book.rows[1].apy is None
    assert book.rows[1].since is None


def test_an_empty_parked_list_is_refused(tmp_path):
    path = _write(
        tmp_path,
        "mandate:\n  name: treasury\n  benchmarks: [{type: flat_rate, rate: 3.1}]\n"
        "  horizon: macro\n  risk_posture: conservative\nparked: []\n",
    )
    with pytest.raises(TreasuryError, match="no `parked` rows"):
        load(path=path)


def test_a_symbol_benchmark_is_refused(tmp_path):
    """ADR-0008: Treasury is judged against the cash rate alone, never an index — a gated
    rule, so it raises rather than warns."""
    path = _write(
        tmp_path,
        "mandate:\n  name: treasury\n  benchmarks: [{type: symbol, key: sp500}]\n"
        "  horizon: macro\n  risk_posture: conservative\n"
        "parked:\n  - {what: USDC, amount: 100, venue: aave-v3}\n",
    )
    with pytest.raises(TreasuryError, match="flat_rate"):
        load(path=path)


def test_a_held_flat_benchmark_is_refused(tmp_path):
    path = _write(
        tmp_path,
        "mandate:\n  name: treasury\n  benchmarks: [{type: held_flat}]\n"
        "  horizon: macro\n  risk_posture: conservative\n"
        "parked:\n  - {what: USDC, amount: 100, venue: aave-v3}\n",
    )
    with pytest.raises(TreasuryError, match="flat_rate"):
        load(path=path)


def test_a_second_flat_rate_entry_is_not_refused(tmp_path):
    """`benchmarks.py` already accepts two `flat_rate` entries sharing one anchor as an
    accepted limit, not a silent bug — this loader must not be stricter than the module it
    feeds."""
    path = _write(
        tmp_path,
        "mandate:\n  name: treasury\n  benchmarks:\n"
        "    - {type: flat_rate, rate: 3.1}\n    - {type: flat_rate, rate: 4.0}\n"
        "  horizon: macro\n  risk_posture: conservative\n"
        "parked:\n  - {what: USDC, amount: 100, venue: aave-v3}\n",
    )
    book = load(path=path)
    assert len(book.mandate.benchmarks) == 2


def test_mandate_name_other_than_treasury_is_refused(tmp_path):
    """`benchmarks._anchor` keys the anchor file by mandate name — a file saying
    `name: parked` would silently anchor under a second key and nothing would fail."""
    path = _write(
        tmp_path,
        "mandate:\n  name: parked\n  benchmarks: [{type: flat_rate, rate: 3.1}]\n"
        "  horizon: macro\n  risk_posture: conservative\n"
        "parked:\n  - {what: USDC, amount: 100, venue: aave-v3}\n",
    )
    with pytest.raises(TreasuryError, match="treasury"):
        load(path=path)


def test_a_duplicate_venue_and_what_pair_is_refused(tmp_path):
    """Two rows for the same money in one venue is a typo, and a net-worth total built on
    top of this file would double-count it."""
    path = _write(
        tmp_path,
        "mandate:\n  name: treasury\n  benchmarks: [{type: flat_rate, rate: 3.1}]\n"
        "  horizon: macro\n  risk_posture: conservative\n"
        "parked:\n"
        "  - {what: USDC, amount: 100, venue: aave-v3}\n"
        "  - {what: USDC, amount: 200, venue: aave-v3}\n",
    )
    with pytest.raises(TreasuryError, match="one row per venue"):
        load(path=path)


def test_a_duplicate_yaml_key_is_refused(tmp_path):
    path = _write(
        tmp_path,
        "mandate:\n  name: treasury\n  benchmarks: [{type: flat_rate, rate: 3.1}]\n"
        "  horizon: macro\n  risk_posture: conservative\n"
        "parked:\n  - {what: USDC, amount: 100, venue: aave-v3}\n"
        "parked:\n  - {what: USDC, amount: 100, venue: aave-v3}\n",
    )
    with pytest.raises(TreasuryError, match="duplicate key"):
        load(path=path)


def test_an_unparseable_amount_raises_rather_than_reading_as_absent(tmp_path):
    path = _write(
        tmp_path,
        "mandate:\n  name: treasury\n  benchmarks: [{type: flat_rate, rate: 3.1}]\n"
        "  horizon: macro\n  risk_posture: conservative\n"
        "parked:\n  - {what: USDC, amount: 'lots', venue: aave-v3}\n",
    )
    with pytest.raises(TreasuryError, match="amount"):
        load(path=path)


def test_a_malformed_mandate_block_arrives_as_treasury_error(tmp_path):
    path = _write(
        tmp_path,
        "parked:\n  - {what: USDC, amount: 100, venue: aave-v3}\n",
    )
    with pytest.raises(TreasuryError) as err:
        load(path=path)
    assert not isinstance(err.value, portfolios.PortfolioError)


def test_a_missing_updated_falls_back_to_mtime(tmp_path):
    path = _write(
        tmp_path,
        "mandate:\n  name: treasury\n  benchmarks: [{type: flat_rate, rate: 3.1}]\n"
        "  horizon: macro\n  risk_posture: conservative\n"
        "parked:\n  - {what: USDC, amount: 100, venue: aave-v3}\n",
    )
    book = load(path=path)
    assert book.updated == datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date()
