"""Tests for the nightly's preflight.

The case that matters is the one the droplet hit: a file that *exists* and does not load must
abort, and a file that is simply absent must not. Those two look identical from the outside —
both are "no usable mandate here" — and getting the line wrong in either direction is expensive.
Treating absence as failure stops a fresh clone running at all; treating malformed as absence
restores the exact silence #83 exists to remove.

Files are written to `tmp_path` rather than read from `data/`, which is gitignored ore: a test
against it would pass on a populated laptop and fail on a clean checkout.
"""
from __future__ import annotations

from preflight import check

MANDATE = (
    "mandate:\n"
    "  name: p\n"
    "  benchmarks:\n"
    "    - type: held_flat\n"
    "  horizon: macro\n"
    "  risk_posture: conservative\n"
)

PORTFOLIO = MANDATE + "positions:\n  - ticker: VTI\n    shares: 42.5\n"

TREASURY = (
    "mandate:\n"
    "  name: treasury\n"
    "  benchmarks:\n"
    "    - type: flat_rate\n"
    "      rate: 3.1\n"
    "  horizon: macro\n"
    "  risk_posture: conservative\n"
    "parked:\n"
    "  - what: USDC\n"
    "    amount: 5000.00\n"
    "    venue: aave-v3\n"
)


def roots(tmp_path):
    root = tmp_path / "portfolios"
    root.mkdir()
    return root, tmp_path / "treasury.yaml"


def test_a_fresh_clone_with_no_data_passes(tmp_path):
    assert check(root=tmp_path / "portfolios", treasury_path=tmp_path / "treasury.yaml") == []


def test_good_files_pass(tmp_path):
    root, treasury = roots(tmp_path)
    (root / "retirement.yaml").write_text(PORTFOLIO, encoding="utf-8")
    treasury.write_text(TREASURY, encoding="utf-8")

    assert check(root=root, treasury_path=treasury) == []


def test_a_portfolio_missing_its_mandate_block_is_named(tmp_path):
    root, treasury = roots(tmp_path)
    (root / "crypto.yaml").write_text(
        "positions:\n  - ticker: btc\n    shares: 0.35\n", encoding="utf-8"
    )

    problems = check(root=root, treasury_path=treasury)
    assert len(problems) == 1
    assert "crypto.yaml" in problems[0]


def test_every_bad_file_is_reported_not_just_the_first(tmp_path):
    root, treasury = roots(tmp_path)
    (root / "crypto.yaml").write_text("positions: []\n", encoding="utf-8")
    (root / "savings.yaml").write_text("positions: []\n", encoding="utf-8")
    (root / "retirement.yaml").write_text(PORTFOLIO, encoding="utf-8")

    problems = check(root=root, treasury_path=treasury)
    assert len(problems) == 2
    assert not any("retirement.yaml" in p for p in problems)


def test_a_malformed_treasury_is_reported(tmp_path):
    root, treasury = roots(tmp_path)
    treasury.write_text(MANDATE + "parked: []\n", encoding="utf-8")

    problems = check(root=root, treasury_path=treasury)
    assert len(problems) == 1
    assert "treasury.yaml" in problems[0]


def test_unreadable_yaml_reports_rather_than_escaping(tmp_path):
    root, treasury = roots(tmp_path)
    (root / "crypto.yaml").write_text("positions:\n  - ticker: [unclosed\n", encoding="utf-8")

    problems = check(root=root, treasury_path=treasury)
    assert len(problems) == 1
    assert "crypto.yaml" in problems[0]
