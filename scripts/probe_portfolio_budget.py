"""What one sitting of approvals actually asks the account for, in total.

The measurement behind ``execution.budget`` and ``Config.max_position_frac``, and the one to
re-run before changing either. Replays the recorded order log through the current sizing rules
with the budget decrementing as each order is placed — which is the thing that did not exist
on the night the log was written.

WHAT IT FOUND, 2026-07-29 (the sitting that produced `docs/IMPROVEMENTS.md` §40). Eight
brackets went out between 03:24 and 04:01 ET, every one of them sized to risk 1% of a $100,000
account. Alpaca accepted all eight — the market was shut — and rejected three at the open:
``RKLB`` for buying power, and two ``CRM`` shorts because the account cannot short at all. The
repo's own log still reads ``placed`` for all three.

Run it with no arguments for the replay. ``--sizing`` prints the distribution behind
``max_position_frac`` instead, and the finding worth not re-deriving is that a concentration
ceiling is **not** a tail guard: at 1% risk the median approved candidate wants 17.4% of
equity, so a 20% ceiling binds on 22 of 47 and cuts their realised risk to a median 0.56%.
That is not a flaw in the number, it is arithmetic — ``1/ceiling`` positions fit at 1x, so
concurrency and per-trade risk are one choice with two names and cannot be set independently.
The sweep in that output is what to read before moving it.

Reads ``data/execution/orders.jsonl`` and ``data/setups/decisions.jsonl`` only — no network,
no venue, no cost.

    uv run python scripts/probe_portfolio_budget.py
    uv run python scripts/probe_portfolio_budget.py --sizing
    uv run python scripts/probe_portfolio_budget.py --equity 25000
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path

from execution.account import Account
from execution.config import load as load_config
from execution.guards import Refusal
from execution.plan import SHARE_GRID, Market, OrderPlan, build

REPO_ROOT = Path(__file__).resolve().parents[1]
ORDERS = REPO_ROOT / "data" / "execution" / "orders.jsonl"
DECISIONS = REPO_ROOT / "data" / "setups" / "decisions.jsonl"


@dataclass(frozen=True)
class Replayed:
    """One row of the log, read structurally the way ``plan.build`` reads a candidate."""
    asset: str
    direction: str
    entry: float
    stop: float
    target: float
    key: str


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def placements(network: str | None = None) -> list[dict]:
    """Orders that reached the venue, oldest first. Refusals carry no geometry to replay.

    Scoped to one network by default — the log spans both venues at different account sizes,
    and totalling a $999 testnet rehearsal against a $100,000 paper sitting would describe a
    night that never happened.
    """
    rows = [r for r in _rows(ORDERS) if r.get("outcome") in ("placed", "failed")]
    if network is None and rows:
        network = rows[-1].get("network")
    return [r for r in rows if network is None or r.get("network") == network]


def replay(rows, *, equity: float, config, can_short: bool | None) -> None:
    """Send the log through the current rules, with the budget shrinking as it goes."""
    markets = {r["asset"]: Market(coin=r["asset"], sz_decimals=0, grid=SHARE_GRID)
               for r in rows}
    listings = {a: type("L", (), {"symbol": a, "scale": None, "is_proxy": False})()
                for a in markets}

    print(f"  {rows[0].get('network', '?')}, {len(rows)} orders from "
          f"{rows[0].get('at', '?')[:10]}")
    print(f"  equity ${equity:,.2f}   risk {config.risk_pct:.2%}   "
          f"concentration {config.max_position_frac or 0:.0%}   "
          f"fill floor {config.min_budget_fill:.0%}\n")
    print(f"  {'asset':<7} {'dir':<6} {'as sent':>12} {'now':>12}  outcome")

    committed = 0.0
    wanted_total = 0.0
    sent_total = 0.0
    for row in rows:
        account = Account(equity=equity, buying_power=equity, committed=committed,
                          multiplier=1.0, can_short=can_short)
        candidate = Replayed(row["asset"], row["direction"], row["entry"], row["stop"],
                             row["target"], row["candidate_key"])
        outcome = build(
            candidate, markets=markets, listing=listings[row["asset"]],
            equity=equity, risk_pct=config.risk_pct, enforce_liquidity=False,
            max_notional_frac=config.max_notional_frac,
            max_position_frac=config.max_position_frac,
            headroom=account.headroom(committed),
            min_budget_fill=config.min_budget_fill,
            can_short=can_short,
        )
        as_sent = row.get("notional") or 0.0
        wanted_total += as_sent
        if isinstance(outcome, Refusal):
            print(f"  {row['asset']:<7} {row['direction']:<6} {as_sent:>12,.2f} "
                  f"{'—':>12}  REFUSED [{outcome.code}]")
            continue
        assert isinstance(outcome, OrderPlan)
        committed += outcome.notional
        sent_total += outcome.notional
        cap = f"capped [{outcome.cap_reason}]" if outcome.cap_reason else "ok"
        print(f"  {row['asset']:<7} {row['direction']:<6} {as_sent:>12,.2f} "
              f"{outcome.notional:>12,.2f}  {cap}")

    print(f"\n  as sent that night: ${wanted_total:,.2f} "
          f"({wanted_total / equity:.1%} of equity)")
    print(f"  under these rules:  ${sent_total:,.2f} "
          f"({sent_total / equity:.1%} of equity)")


def sizing(config) -> None:
    """What each approved decision asks of the account, so the ceiling can be set on evidence."""
    approved = [r for r in _rows(DECISIONS)
                if r.get("decision") == "approved" and r.get("entry") and r.get("stop")]
    if not approved:
        print("  no approved decisions recorded")
        return

    def frac(r):
        return config.risk_pct / (abs(r["entry"] - r["stop"]) / r["entry"])

    fracs = sorted(((frac(r), r) for r in approved), key=lambda pair: pair[0])
    values = [f for f, _ in fracs]
    ceiling = config.max_position_frac or 1.0
    bound = [f for f in values if f > ceiling]

    print(f"  {len(approved)} approved decisions, sized at {config.risk_pct:.2%} risk\n")
    for label, value in (
        ("median", statistics.median(values)),
        ("p75", values[3 * len(values) // 4]),
        ("p90", values[9 * len(values) // 10]),
        ("max", values[-1]),
    ):
        print(f"    {label:<8} {value:>8.1%} of equity")
    print(f"\n  a {ceiling:.0%} ceiling binds on {len(bound)} of {len(values)} "
          f"({len(bound) / len(values):.0%})")
    print(f"  all of them together want {sum(values):.0%} of equity\n")

    # The sweep, because "how often does it bind" is only half the question. The other half is
    # what it costs when it does: a capped order risks ceiling/wanted of the budget, so a
    # ceiling is simultaneously a statement about concurrency and about per-trade risk, and
    # the two cannot be chosen independently. At 1% risk you get 1/ceiling positions at 1x.
    print(f"  {'ceiling':>8} {'binds':>10} {'median risk':>13} {'worst':>9} {'at 1x':>7}")
    for candidate_ceiling in (0.06, 0.10, 0.20, 0.35, 0.50, 1.00):
        over = [f for f in values if f > candidate_ceiling]
        realised = [config.risk_pct * candidate_ceiling / f for f in over] or [config.risk_pct]
        print(f"  {candidate_ceiling:>8.0%} {len(over):>4}/{len(values):<5} "
              f"{statistics.median(realised):>13.2%} {min(realised):>9.3%} "
              f"{1 / candidate_ceiling:>6.0f}x")

    print("\n  the tail it exists for:")
    for f, r in reversed(fracs[-8:]):
        stop_pct = abs(r["entry"] - r["stop"]) / r["entry"]
        print(f"    {r['asset']:<8} {r['direction']:<6} {r.get('zone_timeframe', ''):<7} "
              f"stop {stop_pct:>6.2%}  ->  {f:>8.1%} of equity")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizing", action="store_true",
                        help="the distribution behind max_position_frac, not the replay")
    parser.add_argument("--equity", type=float, default=100_000.0,
                        help="account size to replay against (default: 100000, the paper "
                             "account on the night in question)")
    parser.add_argument("--network", default=None,
                        help="which network's sitting to replay (default: the most recent "
                             "in the log — the two venues run at different account sizes)")
    parser.add_argument("--can-short", action="store_true",
                        help="replay as a margin account; by default the cash account that "
                             "actually rejected the two CRM shorts")
    args = parser.parse_args(argv)

    config = load_config()
    if args.sizing:
        sizing(config)
        return 0

    rows = placements(args.network)
    if not rows:
        print(f"  no orders recorded in {ORDERS}")
        return 0
    replay(rows, equity=args.equity, config=config, can_short=args.can_short or False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
