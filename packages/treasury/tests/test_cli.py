from datetime import date
from pathlib import Path

import treasury.cli as cli

FIXTURE = """\
mandate:
  name: treasury
  benchmarks:
    - type: flat_rate
      rate: 3.1
  horizon: macro
  risk_posture: conservative

updated: 2026-09-01

parked:
  - what: USDC
    amount: 1000.00
    venue: aave-v3
    apy: 4.21
"""


def _empty_roots(tmp_path):
    """`--portfolios-root`/`--altsignal-root`, both empty — a real `data/portfolios/` or
    `data/altsignal/` on this machine must never leak into a CLI test's assertions."""
    portfolios_root = tmp_path / "portfolios"
    altsignal_root = tmp_path / "altsignal"
    portfolios_root.mkdir()
    altsignal_root.mkdir()
    return ["--portfolios-root", str(portfolios_root), "--altsignal-root", str(altsignal_root)]


def test_no_file_names_the_example_and_exits_zero(tmp_path, capsys):
    """Nothing parked is a valid state, unlike `compare`'s empty store — this returns 0."""
    missing = tmp_path / "treasury.yaml"
    assert cli.main(["--file", str(missing)]) == 0
    out = capsys.readouterr().out
    assert "cfg/treasury.example.yaml" in out
    assert str(missing) in out


def test_a_populated_file_renders_the_card_and_exits_zero(tmp_path, capsys):
    fixture = tmp_path / "treasury.yaml"
    fixture.write_text(FIXTURE, encoding="utf-8")
    args = ["--file", str(fixture), "--as-of", "2026-09-16", *_empty_roots(tmp_path)]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "treasury" in out
    assert "$1,000.00" in out


def test_the_example_file_itself_renders(tmp_path, capsys):
    """The exact command named in the ARCHITECTURE.md verify step and the plan's own smoke test."""
    example = Path(__file__).resolve().parents[4] / "cfg" / "treasury.example.yaml"
    args = ["--file", str(example), "--as-of", "2026-09-16", *_empty_roots(tmp_path)]
    assert cli.main(args) == 0
    assert "treasury" in capsys.readouterr().out


# ── `load_result` — the seam a surface calls instead of `main` ───────────────
#
# `_empty_roots` above proves a real `data/portfolios/`/`data/altsignal/` never leaks into
# `main`'s own assertions; these do the same for the seam a surface calls directly, and add the
# one guarantee `main` never needed: that `anchor_root` is honoured, so no test here writes
# `data/benchmarks/anchors.json`.

def test_load_result_returns_none_for_a_missing_file(tmp_path):
    said = []
    result = cli.load_result(
        file=tmp_path / "treasury.yaml", portfolios_root=tmp_path / "portfolios",
        altsignal_root=tmp_path / "altsignal", cfg_dir=tmp_path / "cfg",
        anchor_root=tmp_path / "anchors.json", warn=said.append)
    assert result is None
    assert said == []


def test_load_result_returns_a_treasury_result_for_a_fixture(tmp_path):
    fixture = tmp_path / "treasury.yaml"
    fixture.write_text(FIXTURE, encoding="utf-8")
    result = cli.load_result(
        file=fixture, portfolios_root=tmp_path / "portfolios",
        altsignal_root=tmp_path / "altsignal", cfg_dir=tmp_path / "cfg",
        anchor_root=tmp_path / "anchors.json", as_of=date(2026, 9, 16), warn=lambda m: None)
    assert result is not None
    assert result.total == 1000.0


def test_load_result_honours_anchor_root_rather_than_the_real_anchors_file(tmp_path):
    """A first-ever call for a mandate's `flat_rate` benchmark writes to `anchor_root` on first
    sight (`oracle.benchmarks._save_anchors`) — a test that let that land on the real
    `data/benchmarks/anchors.json` would have a side effect on first sight. Proven by the write
    landing at the injected path: if the code fell back to the real default instead, this path
    would stay untouched."""
    anchor_root = tmp_path / "anchors.json"
    fixture = tmp_path / "treasury.yaml"
    fixture.write_text(FIXTURE, encoding="utf-8")
    cli.load_result(
        file=fixture, portfolios_root=tmp_path / "portfolios",
        altsignal_root=tmp_path / "altsignal", cfg_dir=tmp_path / "cfg",
        anchor_root=anchor_root, as_of=date(2026, 9, 16), warn=lambda m: None)
    assert anchor_root.is_file()


def test_load_result_takes_pre_loaded_books_rather_than_reglobbing(tmp_path, monkeypatch):
    """A caller (`digest.cli.build`) that already loaded every portfolio must be able to pass
    them straight through — a second load means a bad file is warned about twice."""
    fixture = tmp_path / "treasury.yaml"
    fixture.write_text(FIXTURE, encoding="utf-8")

    def _explode(*, root, warn):
        raise AssertionError("must not re-glob portfolios when books is given")

    monkeypatch.setattr(cli, "_load_books", _explode)
    result = cli.load_result(
        file=fixture, altsignal_root=tmp_path / "altsignal", cfg_dir=tmp_path / "cfg",
        anchor_root=tmp_path / "anchors.json", as_of=date(2026, 9, 16), books=(),
        warn=lambda m: None)
    assert result is not None


# ── `balance:` rows resolve from the snapshot in `load_result` ──────────────

SYNCED = FIXTURE.split("parked:")[0] + """parked:
  - what: USD
    venue: sofi
    balance: {plaid: sofi, account: "SoFi Savings"}
"""
HAND_ROW = "  - what: USDC\n    amount: 1000.00\n    venue: aave-v3\n    apy: 4.21\n"


def _snapshot(tmp_path, accounts):
    import json
    root = tmp_path / "balances"
    root.mkdir(exist_ok=True)
    (root / "sofi.json").write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    return root


def _entry(current, available, fetched_at="2026-09-14T11:00:00+00:00"):
    return {"subtype": "savings", "current": current, "available": available,
            "fetched_at": fetched_at}


def _synced_result(tmp_path, body, balances_root, said):
    fixture = tmp_path / "treasury.yaml"
    fixture.write_text(body, encoding="utf-8")
    return cli.load_result(
        file=fixture, portfolios_root=tmp_path / "portfolios",
        altsignal_root=tmp_path / "altsignal", cfg_dir=tmp_path / "cfg",
        anchor_root=tmp_path / "anchors.json", as_of=date(2026, 9, 16),
        balances_root=balances_root, warn=said.append)


def test_a_synced_row_resolves_from_current_and_dates_the_book_by_its_fetch(tmp_path):
    root = _snapshot(tmp_path, {"SoFi Savings": _entry(34045.60, 30000.0)})
    said = []
    result = _synced_result(tmp_path, SYNCED, root, said)
    assert said == []
    assert result.total == 34045.60
    assert result.updated == date(2026, 9, 14)
    assert result.age_days == 2


def test_available_is_the_fallback_when_current_is_absent(tmp_path):
    entry = _entry(None, 500.0)
    del entry["current"]
    root = _snapshot(tmp_path, {"SoFi Savings": entry})
    assert _synced_result(tmp_path, SYNCED, root, []).total == 500.0


def test_a_zero_balance_resolves_to_zero(tmp_path):
    root = _snapshot(tmp_path, {"SoFi Savings": _entry(0.0, 0.0)})
    said = []
    result = _synced_result(tmp_path, SYNCED, root, said)
    assert said == []
    assert result.total == 0.0 and len(result.rows) == 1


def test_a_missing_snapshot_warns_naming_the_sync_and_drops_the_row(tmp_path):
    said = []
    result = _synced_result(tmp_path, SYNCED + HAND_ROW, tmp_path / "balances", said)
    assert len(said) == 1 and "uv run plaid-sync sofi" in said[0]
    assert [r.what for r in result.rows] == ["USDC"]


def test_a_missing_account_warns_and_drops_the_row(tmp_path):
    root = _snapshot(tmp_path, {"Other": _entry(1.0, 1.0)})
    said = []
    result = _synced_result(tmp_path, SYNCED + HAND_ROW, root, said)
    assert len(said) == 1 and "SoFi Savings" in said[0] and "plaid-sync sofi" in said[0]
    assert all(r.amount is not None for r in result.rows)


def test_a_malformed_snapshot_warns_rather_than_raising(tmp_path):
    root = tmp_path / "balances"
    root.mkdir()
    (root / "sofi.json").write_text("{nope", encoding="utf-8")
    said = []
    result = _synced_result(tmp_path, SYNCED + HAND_ROW, root, said)
    assert len(said) == 1 and "plaid-sync sofi" in said[0]
    assert len(result.rows) == 1


def test_a_mixed_file_takes_the_oldest_as_of(tmp_path):
    root = _snapshot(tmp_path, {"SoFi Savings": _entry(10.0, 10.0, "2026-09-10T11:00:00+00:00")})
    result = _synced_result(tmp_path, SYNCED + HAND_ROW, root, [])
    assert result.updated == date(2026, 9, 1)
    root = _snapshot(tmp_path, {"SoFi Savings": _entry(10.0, 10.0, "2026-08-20T11:00:00+00:00")})
    assert _synced_result(tmp_path, SYNCED + HAND_ROW, root, []).updated == date(2026, 8, 20)
