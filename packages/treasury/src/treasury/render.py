"""Turn a ``TreasuryResult`` into the terminal card.

Pure — a ``TreasuryResult`` in, one string out, the same split ``review.render`` and
``compare.render`` draw between gathering data and deciding how to print it. Tested separately
against a constructed result, per spec #63.

**This is the first rendering of a `benchmarks.report()` dict anywhere in the repo** — keep it
plain, since #75's `digest` section and any future dashboard will copy this shape rather than
invent their own.
"""
from __future__ import annotations

from oracle.benchmarks import Unresolved

from treasury.book import TreasuryResult

# ADR-0003's reporting-window order, verbatim.
_WINDOWS = (("7d", "7d"), ("30d", "30d"), ("90d", "90d"), ("1y", "1y"),
            ("since_inception", "since inception"))


def render(result: TreasuryResult) -> str:
    written = ""
    if result.age_days is not None:
        written = f" · written {'today' if result.age_days == 0 else f'{result.age_days} days ago'}"
    head = (f"{result.mandate.name} · {len(result.rows)} row(s) · "
            f"{_money(result.total)} total · as of {result.as_of.isoformat()}{written}")

    lines = [head, "", _rows_block(result), "", _apy_line(result), "", _benchmark_block(result)]
    return "\n".join(lines)


def _rows_block(result: TreasuryResult) -> str:
    out = []
    for row in result.rows:
        apy = f"{row.apy:.2f}%" if row.apy is not None else "—"
        since = f", since {row.since.isoformat()}" if row.since is not None else ""
        out.append(f"  {row.what:<10} {_money(row.amount):>14}  {row.venue:<12}  {apy:>7}{since}")
    return "\n".join(out)


def _apy_line(result: TreasuryResult) -> str:
    if result.weighted_apy is None:
        return "  weighted APY — no row states one"
    line = f"  weighted APY {result.weighted_apy:.2f}%"
    if result.apy_rows < len(result.rows):
        line += (f" ({result.apy_rows}/{len(result.rows)} rows, "
                  f"{_money(result.apy_amount)} of {_money(result.total)} — partial)")
    return line


def _benchmark_block(result: TreasuryResult) -> str:
    if isinstance(result.benchmark, Unresolved):
        return f"  BENCHMARK — could not be priced: {result.benchmark.reason}"
    cells = []
    for key, label in _WINDOWS:
        value = result.benchmark.get(key)
        text = "—" if value is None else f"{value:+.1%}"
        cells.append(f"{label} {text}")
    return "  BENCHMARK  " + "  ".join(cells)


def _money(value: float) -> str:
    return f"${value:,.2f}"
