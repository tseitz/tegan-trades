"""Turn a `CompareResult` into the #54-validated card shape: two columns, tier headings,
ratio lines at the bottom.

Pure — a `CompareResult` in, one string out, the same split `review.render` draws between
gathering data and deciding how to print it. Tested separately against a constructed result,
per spec #63.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from compare.card import (
    NO_FREE_SOURCE,
    NOT_COMPARABLE,
    NOT_FETCHED,
    PRESENT,
    TIER_1,
    TIER_2,
    TIER_3,
    CompareResult,
)

_CELL_TEXT = {
    NO_FREE_SOURCE: "— no free source",
    NOT_FETCHED: "— not fetched",
    NOT_COMPARABLE: "— categories differ",
}

_TIER_LABEL = {
    TIER_1: "TIER 1 — decides the comparison",
    TIER_2: "TIER 2 — supporting, never headline",
    TIER_3: "TIER 3 — wanted, no free source",
}

# How old the freshest reading on a side must be before the card warns about it, mirroring
# `review/render.py`'s STALE banner. DefiLlama's fee series alone runs ~27h behind live, so
# this sits comfortably above that floor rather than firing on every normal run. TUNE.
STALE_AFTER = timedelta(hours=48)


def _cell_text(row, cell) -> str:
    if cell.state == PRESENT:
        return row.format(cell.value)
    return _CELL_TEXT[cell.state]


def _age_banner(result: CompareResult, *, now: datetime) -> str | None:
    stale = []
    for name, freshest in zip((result.left.asset, result.right.asset), result.freshest, strict=True):
        if freshest is not None and now - freshest > STALE_AFTER:
            stale.append(f"{name} freshest reading is {(now - freshest).days}d old")
    if not stale:
        return None
    return f"  STALE — {'; '.join(stale)}. Run `uv run fetch-altsignal` to refresh."


def render(result: CompareResult, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    left_name, right_name = result.left.asset, result.right.asset
    lines = ["", f"PROTOCOL COMPARISON — {left_name} vs {right_name}", ""]

    banner = _age_banner(result, now=now)
    if banner:
        lines += [banner, ""]

    left_texts = [_cell_text(row, row.left) for row in result.metrics]
    right_texts = [_cell_text(row, row.right) for row in result.metrics]
    # Widths come from every row EXCEPT ``Unlock schedule`` -- its cell is a sentence, not a
    # number, and letting it into this `max()` would pad every other row's left column out to a
    # sentence's width, opening a wide gap before every short number. It still prints in full;
    # it just isn't the thing the rest of the table aligns to.
    #
    # `compare_for` always returns the full 13-row tier table, so `metrics` is only ever empty
    # in a test constructing a `CompareResult` by hand -- `default=0` keeps that legitimate
    # (ratio-only, table-only) construction from crashing on an empty `max()`.
    numeric_rows = [
        (row, text) for row, text in zip(result.metrics, left_texts, strict=True)
        if not isinstance(row.left.value, dict) and not isinstance(row.right.value, dict)
    ]
    name_w = max((len(row.label) for row, _ in numeric_rows), default=0)
    left_w = max((len(text) for _, text in numeric_rows), default=0)

    # Prints a heading whenever the tier changes from the row before it -- correct as long as
    # `card._ROWS` groups every tier contiguously. `test_card.py`'s
    # test_tier_order_matches_the_54_prototype hard-codes the full tier sequence, so a reorder
    # that broke contiguity would fail there rather than silently double-printing a heading here.
    last_tier = None
    for row, left_text, right_text in zip(result.metrics, left_texts, right_texts, strict=True):
        if row.tier != last_tier:
            lines.append(f"  {_TIER_LABEL[row.tier]}")
            last_tier = row.tier
        line = f"    {row.label.ljust(name_w)}   {left_text.ljust(left_w)}   {right_text}"
        lines.append(line + (f"   ({row.note})" if row.note else ""))

    lines += ["", "  DERIVED RATIOS"]
    for ratio in result.ratios:
        left_text = f"{ratio.left:.2f}x" if ratio.left is not None else "—"
        right_text = f"{ratio.right:.2f}x" if ratio.right is not None else "—"
        caption = f" — {ratio.caption}" if ratio.caption else ""
        lines.append(
            f"    {ratio.label:<16} {left_name} {left_text:<10} {right_name} {right_text:<10}{caption}"
        )
    return "\n".join(lines)
