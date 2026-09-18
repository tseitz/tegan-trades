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
