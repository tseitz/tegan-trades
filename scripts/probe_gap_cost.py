"""How much does holding an equity overnight actually cost, per asset?

`core.gaps` is the model; this is what measured it, and what to re-run before trusting any number
quoted from it. Free — reads `data/prices/` only, no network, no order.

    uv run python scripts/probe_gap_cost.py                 # queue's approved assets, real stops
    uv run python scripts/probe_gap_cost.py --stop 0.0453   # fixed stop, comparable across assets
    uv run python scripts/probe_gap_cost.py --assets USAR XLE BE

## What this has already established — do not re-derive it

**The per-asset spread is 46x, and it is the entire justification for routing per asset.**
Measured 2026-07-29 at a fixed 4.53% stop over 4,496 sessions / 23 equities: pooled **4.72%** of
sessions gap past the stop (two-sided), against a per-asset range of `BE` 18.6%, `SBSW`/`SGML`
16.0%, `RKLB` 11.6% … `XLE` 0.40%, `WMB`/`GLNG` 0.00%. Pooling throws away the only thing worth
knowing.

**Two-sided versus one-sided halves or doubles every headline.** The rate above counts gaps in
either direction; only the adverse half hurts a given position. One-sided that is 2.36%, and the
chance of at least one adverse gap across a 21-session hold is **39%**. Quote which one you mean.

**The earlier hand measurement's pooled claim reproduces; its per-asset figures do not.**
It reported `USAR` at 34% of
sessions and `XLE` at 2.4%; at the same 4.53% stop they measure **9.16%** and **0.40%**. Its
ordering was right and its magnitudes were not — the same failure mode `probe_book_depth.py`
records for venue slippage. This is why the numbers live in a probe now.

**The rate is violently sensitive to the stop distance, so never quote one without the other.**
`USAR` moves from 2.29% one-sided at a 4.53% stop to 35.88% at a 1.0% stop. Comparing two assets
measured at their own different stops says more about the stops than the assets — use `--stop`
when you want to rank instruments, and the default (each candidate's real stop) when you want to
price the actual trades.

**Sample size is the binding limit and it is structural.** 19 of 28 approved assets carrying bars
hold under 250 sessions (`BE`, `GLNG`, `WMB`: 98), because `oracle/plan.py` scopes each fetch to
the asset's earliest thesis mention. At these rates 98 sessions is a handful of events, which is
why `core.gaps.measure` shrinks toward the pool rather than trusting a raw per-asset rate — and
why a newly listed instrument can never be measured on its own.

**Crypto has no gap term at all** — it trades continuously, so an absent price file for `SOL` or
`XMR` is correct rather than missing. Do not fill those with a pooled equity number.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as st
from pathlib import Path
from urllib.parse import quote

import yaml
from core.gaps import GapCost, adverse_excess, measure, overnight_gaps, pooled
from core.setups import CARRY_HOLD_DAYS

PRICES = Path("data/prices")
DECISIONS = Path("data/setups/decisions.jsonl")
ORACLE_MAP = Path("cfg/oracle_map.yaml")
VENUE_MAP = Path("cfg/venue_map.yaml")


class _Bar:
    """Minimal duck-typed bar — `core.gaps` reads `.open` and `.close` and nothing else."""
    __slots__ = ("close", "open")

    def __init__(self, o: float, c: float) -> None:
        self.open, self.close = o, c


def _price_index() -> dict[str, str]:
    return {os.path.basename(p)[:-5]: p for p in glob.glob(str(PRICES / "*" / "*.json"))}


def _bars_for(asset: str, curated: dict, index: dict[str, str]) -> list[_Bar] | None:
    """Bars for an asset, preferring the instrument it is actually *traded* on.

    `tradeable` before `symbol` deliberately: an index cannot be gapped into or out of, so
    `DJI`'s gap cost is `DIA`'s. Same precedence `setups` uses to draw zones.
    """
    entry = curated.get(asset)
    candidates: list[str] = []
    if isinstance(entry, dict):
        candidates += [str(entry[k]) for k in ("tradeable", "symbol") if entry.get(k)]
    candidates.append(asset)
    for symbol in candidates:
        path = index.get(quote(symbol, safe=""))
        if path:
            with open(path) as handle:
                raw = json.load(handle)["bars"]
            return [_Bar(b["o"], b["c"]) for b in raw]
    return None


def _approvals() -> list[dict]:
    rows = [json.loads(line) for line in DECISIONS.open()]
    return [r for r in rows if r.get("decision") == "approved"]


def _alpaca_listed() -> set[str]:
    """Assets `cfg/venue_map.yaml` gives an `alpaca` row.

    THE COHORT MUST BE ALPACA-LISTED, and this is not a convenience filter. Gap risk is a
    property of an instrument's *trading hours*, not of the asset, and this whole measurement
    exists to price one venue's cost column. Pooling a continuously-traded instrument with cash
    equities corrupts both: `YM` is `YM=F`, a futures contract that trades nearly 24h and barely
    gaps, and pooled against cash equities at its 0.88% stop it was charged 6.33% over the hold.
    Indices and futures are not tradable on Alpaca at all (see `venue_map.yaml`'s header), so
    they have no Alpaca gap cost to compute — their cost is funding, on a perp venue.

    What survives is homogeneous by construction: EQUITY and ETF on US exchanges, one session,
    one closing auction, one overnight.
    """
    assets = yaml.safe_load(VENUE_MAP.open()).get("assets") or {}
    return {a for a, row in assets.items() if isinstance(row, dict) and row.get("alpaca")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assets", nargs="*", help="override the asset list")
    parser.add_argument("--stop", type=float, default=None,
                        help="fixed stop distance as a fraction, e.g. 0.0453. Omit to use each "
                             "candidate's own approved stop.")
    parser.add_argument("--hold", type=int, default=CARRY_HOLD_DAYS)
    parser.add_argument("--all-assets", action="store_true",
                        help="skip the Alpaca-listed filter. Diagnostic only — see _alpaca_listed")
    args = parser.parse_args(argv)

    curated = yaml.safe_load(ORACLE_MAP.open()).get("assets") or {}
    index = _price_index()
    approvals = _approvals()
    listed = _alpaca_listed()

    # asset -> (side, stop distance). A candidate's own stop unless one was forced.
    wanted: dict[str, tuple[str, float]] = {}
    for row in approvals:
        if not args.all_assets and row["asset"] not in listed:
            continue
        side = "long" if row["direction"] == "long" else "short"
        distance = args.stop if args.stop is not None \
            else abs(row["entry"] - row["stop"]) / row["entry"]
        wanted.setdefault(row["asset"], (side, distance))
    if args.assets:
        wanted = {a: wanted.get(a, ("long", args.stop or 0.0453)) for a in args.assets}

    gaps_by_asset: dict[str, tuple[float, ...]] = {}
    for asset in sorted(wanted):
        bars = _bars_for(asset, curated, index)
        if not bars:
            continue
        gaps = overnight_gaps(bars)
        if gaps:
            gaps_by_asset[asset] = gaps
    if not gaps_by_asset:
        print("no assets with cached bars — nothing to measure")
        return 1

    print(f"{'asset':8}{'side':6}{'stop%':>7}{'sess':>6}{'past':>6}{'rate%':>7}"
          f"{'raw%':>8}{'pool%':>8}{'shrunk%':>9}{'pool wt':>9}{'P(>=1)%':>9}")
    print("-" * 83)
    costs: list[GapCost] = []
    for asset, gaps in gaps_by_asset.items():
        side, distance = wanted[asset]
        # The pool is recomputed AT THIS ASSET'S STOP — see ``gaps.pooled_excess`` for why a
        # single scalar corrupts wide-stop assets. The asset is excluded from its own pool so
        # a long history cannot shrink toward itself and look better evidenced than it is.
        cohort = [g for other, g in gaps_by_asset.items() if other != asset]
        pool = pooled(cohort, distance, side)
        past, raw_excess = adverse_excess(gaps, distance, side)
        cost = measure(asset, _bars_for(asset, curated, index),
                       stop_distance=distance, side=side, pool=pool)
        assert cost is not None  # it has bars; measure returns None only without bars and pool
        costs.append(cost)
        print(f"{asset:8}{side:6}{100 * distance:>7.2f}{len(gaps):>6}{past:>6}"
              f"{100 * cost.rate:>7.2f}{100 * raw_excess * args.hold:>8.3f}"
              f"{100 * (pool.excess if pool else 0.0) * args.hold:>8.3f}"
              f"{100 * cost.over(args.hold):>9.3f}{("--" if cost.pooled_weight is None else f"{cost.pooled_weight:.2f}"):>9}"
              f"{100 * cost.at_least_one(args.hold):>9.1f}")

    print("-" * 83)
    print(f"assets {len(costs)}  sessions {sum(c.sessions for c in costs)}  "
          f"median shrunk cost {100 * st.median(c.over(args.hold) for c in costs):.3f}% "
          f"over {args.hold} sessions")
    thin = [c.asset for c in costs if c.borrowed]
    print(f"leaning mostly on the pool ({len(thin)}): {' '.join(thin) or '—'}")
    lone = [c.asset for c in costs if c.pooled_weight is None]
    if lone:
        print(f"NO POOL AVAILABLE, unshrunk ({len(lone)}): {' '.join(lone)}")
    missing = sorted(a for a in wanted if a not in gaps_by_asset)
    print(f"no cached bars ({len(missing)}): {' '.join(missing) or '—'}"
          f"   <- crypto belongs here; it does not gap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
