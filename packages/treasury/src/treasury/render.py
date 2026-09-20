"""Turn a ``TreasuryResult`` into the terminal card.

Pure — a ``TreasuryResult`` in, one string out, the same split ``review.render`` and
``compare.render`` draw between gathering data and deciding how to print it. Tested separately
against a constructed result, per spec #63.

**This is the first rendering of a `benchmarks.report()` dict, and of a `core.safety.GateResult`,
anywhere in the repo** — keep both plain. #75's `digest` and #94's dashboard both now copy this
shape rather than inventing their own, which is why the wording helpers below are public and the
fixed-width layout helpers (`_rows_block`, `_idle_block`) stay private.
"""
from __future__ import annotations

from core.safety import UNCONFIGURED
from oracle.benchmarks import Unresolved

from treasury.book import NOT_FETCHED, TreasuryResult

# ADR-0003's reporting-window order, verbatim.
WINDOWS = (("7d", "7d"), ("30d", "30d"), ("90d", "90d"), ("1y", "1y"),
           ("since_inception", "since inception"))

IDLE_TITLE = "IDLE CASH"
ADVICE_TITLE = "ADVICE — Safety-gate cleared, ranked by APY"


def render(result: TreasuryResult) -> str:
    clause = written_text(result.age_days)
    written = "" if clause is None else f" · {clause}"
    head = (f"{result.mandate.name} · {len(result.rows)} row(s) · "
            f"{money(result.total)} total · as of {result.as_of.isoformat()}{written}")

    lines = [head, "", _rows_block(result), "",
             apy_line(result.weighted_apy, result.apy_rows, len(result.rows),
                       result.apy_amount, result.total),
             "", _benchmark_block(result)]

    line = readings_line(result.readings_as_of.freshest, result.readings_as_of.oldest)
    if line:
        lines += ["", line]

    idle_block = _idle_block(result)
    if idle_block:
        lines += ["", idle_block]

    return "\n".join(lines)


def _rows_block(result: TreasuryResult) -> str:
    out = []
    for row in result.rows:
        apy = row_apy_text(row.apy)
        since = row_since_text(row.since)
        line = f"  {row.what:<10} {money(row.amount):>14}  {row.venue:<12}  {apy:>7}{since}"
        line += safety_suffix(result.safety.get(row.venue))
        out.append(line)
    return "\n".join(out)


def row_apy_text(apy: float | None) -> str:
    return f"{apy:.2f}%" if apy is not None else "—"


def row_since_text(since) -> str:
    return f", since {since.isoformat()}" if since is not None else ""


def safety_suffix(entry) -> str:
    """Nothing at all for `UNCONFIGURED` — an off-chain venue like SoFi was never meant to
    clear a DeFi gate, and printing "no audit on record" about it would be a confident wrong
    answer about an account this gate does not judge. Takes the row's own already-looked-up
    `safety` entry rather than `(venue, result)`, so a caller holding one row's entry — the
    dashboard wire included — can use it without a second lookup."""
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


def apy_line(weighted_apy: float | None, apy_rows: int, row_count: int,
             apy_amount: float, total: float) -> str:
    if weighted_apy is None:
        return "  weighted APY — no row states one"
    line = f"  weighted APY {weighted_apy:.2f}%"
    if apy_rows < row_count:
        line += (f" ({apy_rows}/{row_count} rows, "
                  f"{money(apy_amount)} of {money(total)} — partial)")
    return line


def benchmark_cells(benchmark: dict | Unresolved) -> list[tuple[str, str]]:
    """The `(label, text)` pairs — `_benchmark_block` composes them into the terminal's line;
    the dashboard wire wants the cells on their own. `[]` for an `Unresolved` benchmark —
    `benchmark_unresolved_note` is the wire's other half of that branch, so neither caller
    has to import `oracle.benchmarks.Unresolved` to ask the question itself."""
    if isinstance(benchmark, Unresolved):
        return []
    cells = []
    for key, label in WINDOWS:
        value = benchmark.get(key)
        text = "—" if value is None else f"{value:+.1%}"
        cells.append((label, text))
    return cells


def benchmark_unresolved_note(benchmark: dict | Unresolved) -> str | None:
    """`None` for a priced benchmark; "could not be priced: <reason>" for an `Unresolved` one —
    so a caller across the dashboard boundary (`test_boundaries.FORBIDDEN` blocks `oracle`) can
    ask this without importing `Unresolved` itself. Deliberately just the sentence, not the
    `"BENCHMARK — "` heading — that is structure, the same split `LevelsSection.empty_note` and
    `AltSignalSection.empty_note` already draw between a title and its note."""
    return f"could not be priced: {benchmark.reason}" if isinstance(benchmark, Unresolved) else None


def _benchmark_block(result: TreasuryResult) -> str:
    note = benchmark_unresolved_note(result.benchmark)
    if note is not None:
        return f"  BENCHMARK — {note}"
    cells = benchmark_cells(result.benchmark)
    return "  BENCHMARK  " + "  ".join(f"{label} {text}" for label, text in cells)


def readings_line(freshest, oldest) -> str:
    """A stale store reads as current unless this prints — the age banner
    `compare.card`/`render` already carries for the same reason."""
    if freshest is None:
        return ""
    if oldest == freshest:
        return f"  safety readings as of {freshest:%Y-%m-%d}"
    return f"  safety readings {oldest:%Y-%m-%d} to {freshest:%Y-%m-%d}"


def shows_idle(result: TreasuryResult) -> bool:
    """Whether the idle/advice block has anything to print. One definition, so the dashboard
    wire asks the same question rather than growing a second copy of the rule."""
    return bool(result.idle and result.advice)


def idle_lines(idle) -> list[str]:
    return [f"  {f'{row.mandate}/{row.account}':<30} {money(row.amount):>14}" for row in idle]


def advice_lines(advice) -> list[str]:
    return [f"  {ranked.facts.slug:<14} {row_apy_text(ranked.facts.apy):>7}" for ranked in advice]


def _idle_block(result: TreasuryResult) -> str:
    """Only when there is idle cash **and** at least one venue clears the gate — nothing to
    say prints no block, the rule #75 inherits."""
    if not shows_idle(result):
        return ""
    return "\n".join([
        IDLE_TITLE, *idle_lines(result.idle), "", ADVICE_TITLE, *advice_lines(result.advice),
    ])


def written_text(age_days: int | None) -> str | None:
    """The clause naming how old the book is, with no leading separator — `render()` composes
    the ` · ` itself. Deliberately the same name and signature as `review.render.written_text`:
    it is the same sentence about a different file, and a second spelling would drift from it."""
    if age_days is None:
        return None
    return f"written {'today' if age_days == 0 else f'{age_days} days ago'}"


def money(value: float) -> str:
    return f"${value:,.2f}"
