"""Turn a ``TreasuryResult`` into the terminal card.

Pure — a ``TreasuryResult`` in, one string out, the same split ``review.render`` and
``compare.render`` draw between gathering data and deciding how to print it. Tested separately
against a constructed result, per spec #63.

**This is the first rendering of a `benchmarks.report()` dict, and of a `core.safety.GateResult`,
anywhere in the repo** — keep both plain, since #75's `digest` section and any future dashboard
will copy this shape rather than invent their own.
"""
from __future__ import annotations

from core.safety import UNCONFIGURED
from oracle.benchmarks import Unresolved

from treasury.book import NOT_FETCHED, TreasuryResult

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

    readings_line = _readings_line(result)
    if readings_line:
        lines += ["", readings_line]

    idle_block = _idle_block(result)
    if idle_block:
        lines += ["", idle_block]

    return "\n".join(lines)


def _rows_block(result: TreasuryResult) -> str:
    out = []
    for row in result.rows:
        apy = f"{row.apy:.2f}%" if row.apy is not None else "—"
        since = f", since {row.since.isoformat()}" if row.since is not None else ""
        line = f"  {row.what:<10} {_money(row.amount):>14}  {row.venue:<12}  {apy:>7}{since}"
        line += _safety_suffix(row.venue, result)
        out.append(line)
    return "\n".join(out)


def _safety_suffix(venue: str, result: TreasuryResult) -> str:
    """Nothing at all for `UNCONFIGURED` — an off-chain venue like SoFi was never meant to
    clear a DeFi gate, and printing "no audit on record" about it would be a confident wrong
    answer about an account this gate does not judge."""
    entry = result.safety.get(venue)
    if entry is None or entry == UNCONFIGURED:
        return ""
    if entry == NOT_FETCHED:
        return "  · Safety: not fetched yet"
    gate_result, score = entry
    if not gate_result.passed:
        return f"  · Safety FAILS: {'; '.join(gate_result.reasons)}"
    parts = ["Safety OK"]
    if score.incidents is not None:
        parts.append(f"{score.incidents} incident(s)")
    return "  · " + ", ".join(parts)


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


def _readings_line(result: TreasuryResult) -> str:
    """A stale store reads as current unless this prints — the age banner
    `compare.card`/`render` already carries for the same reason."""
    freshest = result.readings_as_of.freshest
    if freshest is None:
        return ""
    oldest = result.readings_as_of.oldest
    if oldest == freshest:
        return f"  safety readings as of {freshest:%Y-%m-%d}"
    return f"  safety readings {oldest:%Y-%m-%d} to {freshest:%Y-%m-%d}"


def _idle_block(result: TreasuryResult) -> str:
    """Only when there is idle cash **and** at least one venue clears the gate — nothing to
    say prints no block, the rule #75 inherits."""
    if not result.idle or not result.advice:
        return ""
    idle_lines = [
        f"  {f'{row.mandate}/{row.account}':<30} {_money(row.amount):>14}" for row in result.idle
    ]
    advice_lines = []
    for ranked in result.advice:
        apy = f"{ranked.facts.apy:.2f}%" if ranked.facts.apy is not None else "—"
        advice_lines.append(f"  {ranked.facts.slug:<14} {apy:>7}")
    return "\n".join([
        "IDLE CASH", *idle_lines, "", "ADVICE — Safety-gate cleared, ranked by APY", *advice_lines,
    ])


def _money(value: float) -> str:
    return f"${value:,.2f}"
