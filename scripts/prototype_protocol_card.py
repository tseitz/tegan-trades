"""PROTOTYPE — throwaway. Answers wayfinder ticket #54: what does "HYPE beside LIGHTER" look like?

Not wired to any live source. Every number below is copied by hand from
docs/research/protocol-comparison-metrics.md (measured 2026-09-13/14 UTC) — the point of this
script is card shape, not data plumbing. Nothing here should be merged as-is; fold the
validated shape into a real `review`/`digest`-style module once Tegan picks a variant.

    uv run python scripts/prototype_protocol_card.py --variant columns
    uv run python scripts/prototype_protocol_card.py --variant rows
    uv run python scripts/prototype_protocol_card.py --variant both   # default
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

NO_FREE_SOURCE = "— no free source"  # oracle/carry.py's rule: absent and free are different.


@dataclass(frozen=True)
class Metric:
    label: str
    tier: int  # 1 = decides the comparison, 2 = supporting, 3 = wanted but not free
    hype: str | None
    lit: str | None
    note: str = ""


# Order mirrors the research doc's own ranking: "prefer the number that costs someone real
# money to produce." Tier 1 first, in the doc's stated order; tier 2 after; tier 3 (all None)
# last, so the card shows what it could not get rather than silently dropping it.
METRICS = [
    Metric("Revenue, 30d", 1, "$59.49M", "$4.17M"),
    Metric("Open interest", 1, "$14.18B", "$1.09B", "CoinGecko /derivatives, one source both sides"),
    Metric("Market cap", 1, "$17.57B", "$1.10B"),
    Metric("Unlock schedule", 1, "ends 2026-09-15, 611.8M TBD", "0 now, 3.19M/wk from ~2026-12-29",
           "the field that can reverse the other three"),
    Metric("Fees, 30d", 2, "$76.07M", "$5.47M"),
    Metric("Volume, 24h", 2, "$5.45B", "$1.05B"),
    Metric("TVL", 2, "$6.70B", "$696.8M", "bridged collateral, not lending liquidity"),
    Metric("Turnover (vol/OI)", 2, "0.38x/day", "1.90x/day"),
    Metric("Float share", 2, "23.3%", "25.0%"),
    Metric("FDV", 2, "$75.47B", "$4.41B"),
    Metric("Active users", 3, None, None),
    Metric("Liquidations", 3, None, None),
    Metric("Book depth", 3, None, None),
]

RATIOS = [
    ("mcap / OI", "1.24x", "1.99x", "Lighter is the MORE expensive per dollar of real risk"),
    ("mcap / revenue", "24.3x", "21.6x", "naive read: Lighter cheaper"),
    ("FDV / revenue", "104.4x", "86.5x", "naive read: Lighter cheaper — unlocks say otherwise"),
]

TIER_LABEL = {1: "TIER 1 — decides the comparison", 2: "TIER 2 — supporting, never headline",
              3: "TIER 3 — wanted, no free source"}


def cell(value: str | None) -> str:
    return value if value is not None else NO_FREE_SOURCE


def render_columns() -> str:
    """Variant A: two columns side by side, one row per metric."""
    lines = ["", "PROTOCOL COMPARISON — HYPE vs LIGHTER (variant: columns)", ""]
    name_w = max(len(m.label) for m in METRICS)
    hype_w = max(len(cell(m.hype)) for m in METRICS)
    last_tier = None
    for m in METRICS:
        if m.tier != last_tier:
            lines.append(f"  {TIER_LABEL[m.tier]}")
            last_tier = m.tier
        row = f"    {m.label.ljust(name_w)}   {cell(m.hype).ljust(hype_w)}   {cell(m.lit)}"
        lines.append(row + (f"   ({m.note})" if m.note else ""))
    lines.append("")
    lines.append("  DERIVED RATIOS")
    for label, h, lit, note in RATIOS:
        lines.append(f"    {label:<16} HYPE {h:<8} LIT {lit:<8} — {note}")
    return "\n".join(lines)


def render_rows() -> str:
    """Variant B: one block per protocol, metrics stacked underneath (mirrors probe_chart_first's
    per-asset-block shape rather than a table)."""
    lines = ["", "PROTOCOL COMPARISON — HYPE vs LIGHTER (variant: rows)", ""]
    for name, key in (("HYPE (Hyperliquid)", "hype"), ("LIT (Lighter)", "lit")):
        lines.append(f"  {name}")
        last_tier = None
        for m in METRICS:
            if m.tier != last_tier:
                lines.append(f"    {TIER_LABEL[m.tier]}")
                last_tier = m.tier
            value = cell(getattr(m, key))
            lines.append(f"      {m.label:<20} {value}" + (f"  ({m.note})" if m.note else ""))
        lines.append("")
    lines.append("  DERIVED RATIOS")
    for label, h, lit, note in RATIOS:
        lines.append(f"    {label:<16} HYPE {h:<8} LIT {lit:<8} — {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=["columns", "rows", "both"], default="both")
    args = parser.parse_args(argv)
    if args.variant in ("columns", "both"):
        print(render_columns())
    if args.variant in ("rows", "both"):
        print(render_rows())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
