"""What the recorded candidates actually did, replayed against cached daily bars.

Free, local, re-runnable. Reads ``data/setups/decisions.jsonl`` and ``data/prices/`` only —
no network, no LLM, no price fetch. Nothing here mutates anything, and in particular nothing
is ever written back onto a decision row (``oracle.decisions`` forbids backfilling, and a
replayed outcome is exactly the kind of derived value that would look captured-live a month
from now).

**Why this probe exists.** ``docs/IMPROVEMENTS.md`` §4 says revealed preference is the only
ground truth available for the four scorers. That was true when it was written and it is not
true now: every decision row carries ``entry``/``stop``/``target`` and a timestamp, and
``data/prices/`` holds a year of daily OHLC for most of the assets involved. Walking those
bars forward turns 142 hand-entered *preferences* into outcomes — including for the rows that
were rejected and deferred, which live trading can never supply, because those trades were
never taken.

**It answers five questions.**

1. *Would the limit have filled at all?* Nothing has ever measured this, and an entry that
   never trades is the quietest way for the whole pipeline to produce nothing.
2. *Does ``score`` order outcomes?* Not preferences — outcomes. Same AUC statistic
   ``probe_freshness_weight`` uses, so the two are directly comparable.
3. *Was the approve/reject judgement additive?* If the rejected candidates resolved better
   than the approved ones, the queue is fine and the clicking is the problem.
4. *Did the promised reward:risk survive?* Realized R against what the engine advertised.
5. *How far away are the two levels, in units of the instrument's own daily range?*

**Section 5 is the one to read first, which is not how it was designed.** 1-4 are all
censored by how young the sample is; 5 is a property of the candidate itself and is settled
the moment it is drawn. It is also where the sharpest result landed — 69% of stops are reached
on the very bar that filled the entry, while the median target sits ~7 daily ranges away. A
level a day out and a level a month out are not two ends of one trade.

**Three properties of the measurement, each of which can invert a conclusion.**

*Instrument identity is checked, not assumed.* Curation changed repeatedly during the window
these decisions span (§29, §31, §32), so the instrument an asset routes to **today** is not
always the one its stop was drawn on a week ago, and replaying a recorded stop against a
different instrument's bars produces a confident, wrong number. Every row is therefore checked
against the spot it recorded at decision time. **Read ``IDENTITY_PAD`` before tightening it**:
the first version of this check compared against the same day's *close* within 2% and threw
away 24% of the sample as route drift, every row of which was the right instrument. The check
that is useful is for scale, not for accuracy.

*The same bar cannot order two touches.* Daily OHLC says a bar traded through both the stop
and the target; it cannot say which came first. Those rows are counted as ``ambiguous`` and
resolved **pessimistically** — as stops — in the headline rate, with the count printed
separately so a reader can see how much of the result rests on that convention. Intraday bars
would settle it and this probe deliberately does not guess in their absence.

*There is no horizon constant here, on purpose.* §2 says the fixed 7/30/180/365-day horizons
are unvalidated guesses that should go, and inventing a "trade expires after N days" cutoff to
tidy this table would resurrect exactly that. ``open`` and ``nofill`` are honest terminal
states, printed with the days elapsed so the reader can see whether a rate is settled or still
maturing. ``--max-wait-days`` exists only to probe sensitivity, and defaults to off.

**Read the whole thing as a shape, not a fit.** The oldest decision is days old, not months,
so a large share of rows are legitimately unresolved; that fraction is printed first because
it bounds everything after it.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import median

from core.canon import load_registry
from oracle import cache, corpus, listings
from oracle.assemble import CONFIG_DIR, load_daily
from oracle.route import Unpriceable, load_routing_table, route

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS = REPO_ROOT / "data" / "setups" / "decisions.jsonl"

# ── outcome states ──────────────────────────────────────────────────────────

TARGET = "target"
STOP = "stop"
AMBIGUOUS = "ambiguous"   # one bar touched both; daily data cannot order them
OPEN = "open"             # filled, neither level reached yet
NOFILL = "nofill"         # the limit was never traded through
UNREPLAYABLE = "unreplayable"

# States that represent a completed trade. ``AMBIGUOUS`` is resolved into ``STOP`` before any
# rate is formed — see the module docstring — so it is settled, just not cleanly.
RESOLVED = (TARGET, STOP, AMBIGUOUS)

# How far outside the local trading range the recorded decision-time spot may sit before the
# row is treated as naming a different instrument than the one now cached.
#
# **This is an identity check, not an accuracy check, and the first version got that wrong.**
# Comparing the recorded spot against the decision date's *close* within 2% rejected 33 of 137
# rows — 24% — as "route drift". Every one of them was the right instrument: NVDA 190.01 against
# a 195.04 close, HOOD 92.76 against 89.84. The recorded ``price`` is an intraday mark taken
# whenever the sitting happened, and sittings run at 03:38 and 20:57 UTC, so it is routinely a
# day off the close and legitimately anywhere inside the session's range.
#
# What actually distinguishes a wrong instrument is *scale*: ^DJI at 50,000 against DIA at 500,
# or Chainlink at $8.50 against Interlink Electronics. So the test is whether the spot falls
# within the high/low envelope of the bars around the decision, padded by this fraction. A 25%
# pad is wide enough for a gap or a volatile alt and still refuses anything off by an order of
# magnitude, which is the only error this can usefully catch.
IDENTITY_PAD = 0.25

# Bars either side of the decision date that form that envelope. Two is enough to absorb a
# weekend, a decision stamped in UTC after the US close, and a cache fetched before the day's
# bar existed — the three ways the recorded mark and the bar legitimately disagree on *which*
# day they describe.
IDENTITY_WINDOW = 2

# Bars before the decision that define "an ordinary session of movement" for that instrument.
# Two weeks of trading: long enough that one wild day does not set the scale, short enough to
# still describe the regime the candidate was drawn in.
RANGE_LOOKBACK = 14
MIN_RANGE_BARS = 5

BOOTSTRAP_SEED = 11
BOOTSTRAP_ROUNDS = 20_000
MIN_GROUP = 3
CELL_WIDTH = 21


# ── loading ─────────────────────────────────────────────────────────────────

def load_rows(path: Path) -> list[dict]:
    """Every decision, latest-per-candidate, in file order.

    Unlike ``probe_freshness_weight`` this does **not** partition on ``score_version`` at load
    time. An outcome is an absolute fact about an instrument — a target was hit or it was not —
    so it stays comparable across scoring generations in a way a *score* never is. Only the
    score-orders-outcomes table below partitions, and it says so there.

    Re-decided candidates collapse to their latest row, matching ``oracle.decisions``: a zone
    that was deferred and then approved was, in the end, approved once.
    """
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row["candidate_key"]] = row
    return list(latest.values())


def build_series_loader():
    """``asset -> PriceSeries | None``, resolved exactly as ``setups`` resolves it.

    Deliberately reuses ``setups_cli.load_daily`` rather than reimplementing the lookup. The
    replay is only meaningful if it reads the same bars the candidate's stop was drawn on, and
    that function is the single place that knows to prefer ``trade_symbol`` over ``symbol``
    (RUT's zone is quoted on IWM). A second path from asset to price is the bug §32 and §44
    are both about; this probe is not going to add a third.
    """
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    table = load_routing_table(
        CONFIG_DIR, [(r.asset, r.domain) for r in rows], listings=listings_map
    )
    series_cache: dict = {}

    def load(asset: str):
        resolved = route(asset, table)
        if isinstance(resolved, Unpriceable):
            return None, resolved.reason
        series = load_daily(resolved, table=table, series_cache=series_cache)
        return series, None if series else "no cached bars"

    return load


# ── the walk ────────────────────────────────────────────────────────────────

def identity_ok(row: dict, series) -> bool | None:
    """Was this instrument trading anywhere near the spot this row recorded, when it recorded it?

    Not "was the price right" — see ``IDENTITY_PAD``. The envelope is the high/low span of the
    bars within ``IDENTITY_WINDOW`` days of the decision, so an intraday mark taken at any hour
    of any of those sessions passes, and a different instrument at a different order of
    magnitude does not.

    Returns ``None`` when the row predates the ``price`` field, or when the series has no bars
    near the decision at all. Both are genuinely "cannot check" and must not be reported as
    "checked and passed" — the count is printed separately for exactly that reason.
    """
    recorded = row.get("price")
    if not recorded:
        return None
    decided = datetime.fromisoformat(row["decided_at"]).date()
    near = [b for b in series.bars if abs((b.date - decided).days) <= IDENTITY_WINDOW]
    if not near:
        return None
    low = min(b.low for b in near) * (1 - IDENTITY_PAD)
    high = max(b.high for b in near) * (1 + IDENTITY_PAD)
    return low <= recorded <= high


def _touches(bar, level: float, *, above: bool) -> bool:
    return bar.high >= level if above else bar.low <= level


def walk(row: dict, series, *, max_wait_days: int | None = None) -> dict:
    """Classify one candidate by walking bars forward from the day after it was decided.

    **Strictly after.** A decision was made partway through its own session, so that session's
    high and low include ticks that had not happened when the entry was chosen. Including the
    decision-day bar would let a target that was hit *before* the sitting count as a win — the
    same look-ahead ``oracle.series`` opens by forbidding.

    Fill and exit are evaluated in one pass. A bar that touches both the stop and the target is
    ``ambiguous`` — daily data cannot order them — but a bar that *fills and then stops* is not:
    a long's stop sits below its entry, so price had to trade through the entry to reach the
    stop, and fill-then-stop is the only ordering available. It is recorded as ``same_bar``
    rather than waved through, because a stop reached inside the fill session is a statement
    about ``STOP_PAD_ATR`` — that the stop is inside one day's noise — and nothing else here
    would surface it.
    """
    decided = datetime.fromisoformat(row["decided_at"]).date()
    long = row["direction"] == "long"
    entry, stop, target = row["entry"], row["stop"], row["target"]

    forward = [b for b in series.bars if b.date > decided]
    if not forward:
        return {"state": OPEN, "detail": "no bars after the decision", "bars": 0}

    filled_on: date | None = None
    for i, bar in enumerate(forward):
        if max_wait_days is not None and filled_on is None and i >= max_wait_days:
            return {"state": NOFILL, "detail": f"not reached in {max_wait_days}d", "bars": i}

        if filled_on is None:
            # A long rests below the market and fills when price trades down to it.
            if not _touches(bar, entry, above=not long):
                continue
            filled_on = bar.date

        hit_target = _touches(bar, target, above=long)
        hit_stop = _touches(bar, stop, above=not long)

        same_bar = bar.date == filled_on
        if hit_target and hit_stop:
            return {"state": AMBIGUOUS, "filled_on": filled_on, "bars": i + 1,
                    "same_bar": same_bar}
        if hit_target:
            return {"state": TARGET, "filled_on": filled_on, "bars": i + 1,
                    "same_bar": same_bar}
        if hit_stop:
            return {"state": STOP, "filled_on": filled_on, "bars": i + 1,
                    "same_bar": same_bar}

    last = forward[-1].date
    if filled_on is None:
        return {"state": NOFILL, "detail": f"never traded through {entry:g}",
                "bars": len(forward), "through": last}
    return {"state": OPEN, "filled_on": filled_on, "bars": len(forward), "through": last}


def classify(row: dict, load_series, *, max_wait_days: int | None = None) -> dict:
    """One decision row -> its outcome, or why it has none.

    The two refusals come before the walk and in this order on purpose: an asset that no
    longer routes anywhere is a *coverage* gap, while an asset that routes to bars disagreeing
    with the recorded spot is a *correctness* one, and conflating them would hide the second
    inside the first.
    """
    series, why = load_series(row["asset"])
    if series is None or not series.bars:
        return {"state": UNREPLAYABLE, "detail": why or "empty series"}

    verified = identity_ok(row, series)
    if verified is False:
        return {"state": UNREPLAYABLE,
                "detail": "recorded spot disagrees with cached bars — route drift?"}

    result = walk(row, series, max_wait_days=max_wait_days)
    result["identity"] = verified
    return result


def span(row: dict, load_series) -> tuple[float, float] | None:
    """(stop, target) distance from entry, in units of the instrument's median daily range.

    The denominator is the median high-low of the ``RANGE_LOOKBACK`` bars *before* the decision
    — median rather than mean so one earnings gap cannot flatter a stop, and prior bars only so
    the measure is available at decision time rather than only in hindsight.

    This is not ATR and deliberately not compared to ``STOP_PAD_ATR``: true range includes the
    overnight gap and this does not. It answers a narrower question — how many ordinary
    sessions of movement each level is away — which is the unit the same-bar stop result above
    is denominated in.
    """
    series, _ = load_series(row["asset"])
    if series is None or not series.bars:
        return None
    decided = datetime.fromisoformat(row["decided_at"]).date()
    prior = [b for b in series.bars if b.date <= decided][-RANGE_LOOKBACK:]
    if len(prior) < MIN_RANGE_BARS:
        return None
    daily = median(b.high - b.low for b in prior)
    if not daily:
        return None
    return (abs(row["entry"] - row["stop"]) / daily,
            abs(row["target"] - row["entry"]) / daily)


def realized_r(row: dict, state: str) -> float | None:
    """The R multiple a resolved row actually returned, on the risk it actually took.

    Not the recorded ``reward_risk``: that was the ratio the engine *promised*, and comparing
    the two is half the point of this probe. A stop is -1R by construction. ``AMBIGUOUS`` is
    charged as a stop, consistent with every other rate here.
    """
    if state == TARGET:
        risk = abs(row["entry"] - row["stop"])
        return abs(row["target"] - row["entry"]) / risk if risk else None
    if state in (STOP, AMBIGUOUS):
        return -1.0
    return None


# ── statistics ──────────────────────────────────────────────────────────────

def auc(positive: list[float], negative: list[float]) -> float:
    """P(a random positive outranks a random negative); ties count as half.

    Same statistic and same tie convention as ``probe_freshness_weight``, so a score-orders-
    *outcomes* number here can be read against a score-orders-*preferences* number there
    without a conversion. 0.5 is chance; below 0.5 orders backwards.
    """
    if not positive or not negative:
        return float("nan")
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0
        for p in positive for n in negative
    )
    return wins / (len(positive) * len(negative))


def auc_ci(positive: list[float], negative: list[float]) -> tuple[float, float]:
    """95% bootstrap CI, resampling both groups independently."""
    if not positive or not negative:
        return (float("nan"), float("nan"))
    rng = random.Random(BOOTSTRAP_SEED)
    vals = sorted(
        auc([rng.choice(positive) for _ in positive], [rng.choice(negative) for _ in negative])
        for _ in range(BOOTSTRAP_ROUNDS)
    )
    return vals[int(0.025 * BOOTSTRAP_ROUNDS)], vals[int(0.975 * BOOTSTRAP_ROUNDS)]


def cell(positive: list[float], negative: list[float]) -> str:
    """``AUC [lo, hi]``, marked ``?`` when the interval spans chance.

    Refuses to bootstrap below ``MIN_GROUP`` a side: resampling one observation returns that
    same value every round and prints a zero-width interval, which reads as the most confident
    result in the table while resting on two data points.
    """
    if len(positive) < MIN_GROUP or len(negative) < MIN_GROUP:
        return f"n too small ({len(positive)}v{len(negative)})".rjust(CELL_WIDTH)
    point = auc(positive, negative)
    lo, hi = auc_ci(positive, negative)
    marker = "?" if lo <= 0.5 <= hi else " "
    return f"{point:.3f} [{lo:.2f},{hi:.2f}]{marker}".rjust(CELL_WIDTH)


def win_rate(states: list[str]) -> str:
    """Share of *resolved* rows that reached target, ambiguous counted as stops."""
    resolved = [s for s in states if s in RESOLVED]
    if not resolved:
        return "  no resolved rows"
    wins = sum(1 for s in resolved if s == TARGET)
    return f"{wins / len(resolved):.0%} ({wins}/{len(resolved)})"


# ── report ──────────────────────────────────────────────────────────────────

def report(rows: list[dict], outcomes: dict[str, dict], load_series) -> None:
    states = {k: o["state"] for k, o in outcomes.items()}
    by_key = {r["candidate_key"]: r for r in rows}

    print(f"\n{len(rows)} candidates, latest decision each\n")

    print("── what could be replayed at all ──")
    tally = Counter(states.values())
    for state in (TARGET, STOP, AMBIGUOUS, OPEN, NOFILL, UNREPLAYABLE):
        n = tally.get(state, 0)
        print(f"  {state:14} {n:4}  {n / len(rows):5.0%}")
    unchecked = sum(1 for o in outcomes.values() if o.get("identity") is None
                    and o["state"] != UNREPLAYABLE)
    print(f"\n  of the replayed rows, {unchecked} could not be identity-checked "
          f"(no recorded spot, or no bars near the decision) — see IDENTITY_PAD")
    why = Counter(o.get("detail") for o in outcomes.values() if o["state"] == UNREPLAYABLE)
    for detail, n in why.most_common():
        print(f"  unreplayable: {n:3}  {detail}")

    # The censoring warning, printed before any rate so it cannot be read past. Stops sit ~1R
    # away and targets several R away, so the near level is reached first almost by
    # construction on a young sample — a low win rate here is substantially a statement about
    # elapsed time, not about edge.
    resolved_n = sum(tally.get(s, 0) for s in RESOLVED)
    pending = tally.get(OPEN, 0) + tally.get(NOFILL, 0)
    print(f"\n  ** {pending} of {resolved_n + pending} replayable rows have not resolved. **")
    print("  a stop is ~1R away and a target several R away, so the near level resolves first")
    print("  on a young sample whatever the edge is. read section 1 as settled and sections")
    print("  3 and 4 as censored until the open rows mature.")

    print("\n── 1. would the limit have filled? ──")
    print("  a candidate nobody could enter is not a win or a loss, and nothing has")
    print("  measured this before. 'open' counts as filled.")
    reachable = [s for s in states.values() if s != UNREPLAYABLE]
    filled = [s for s in reachable if s != NOFILL]
    if reachable:
        print(f"\n  filled: {len(filled)}/{len(reachable)}  ({len(filled) / len(reachable):.0%})")
    for decision in ("approved", "rejected", "later", "archived"):
        group = [states[k] for k, r in by_key.items() if r["decision"] == decision
                 and states[k] != UNREPLAYABLE]
        if not group:
            continue
        n_filled = sum(1 for s in group if s != NOFILL)
        print(f"    {decision:10} {n_filled:3}/{len(group):3}  {n_filled / len(group):5.0%}")

    print("\n── 2. does the score order outcomes? ──")
    print("  AUC of score, target-reaching vs stopped-out. Partitioned on score_version:")
    print("  the scale changed at every bump and pooling compares numbers that were never")
    print("  on one scale (oracle/decisions.py). 0.5 is chance; below it orders backwards.")
    print()
    versions = sorted({r["score_version"] for r in rows}, reverse=True)
    for version in [*versions, "all (invalid — mixed scales)"]:
        if isinstance(version, str):
            keys = list(by_key)
        else:
            keys = [k for k, r in by_key.items() if r["score_version"] == version]
        won = [by_key[k]["score"] for k in keys if states[k] == TARGET]
        lost = [by_key[k]["score"] for k in keys if states[k] in (STOP, AMBIGUOUS)]
        label = f"v{version}" if not isinstance(version, str) else version
        print(f"  {label:28} {cell(won, lost)}")

    print("\n── 3. was the approve/reject judgement additive? ──")
    print("  the question live trading can never answer, because the rejects were never")
    print("  taken. sittings are NOT conditioned here — an outcome is absolute, so §4's")
    print("  moving-threshold trap does not apply to a win rate the way it does to a score.")
    print()
    for decision in ("approved", "rejected", "later", "archived"):
        group = [states[k] for k, r in by_key.items() if r["decision"] == decision]
        print(f"  {decision:10} win rate {win_rate(group)}")
    print("\n  archived rows are mixed evidence — the old 'x' key meant both 'I don't trade")
    print("  this asset' and 'stale, bury it'. See oracle/decisions.py.")

    print("\n── 4. did the promised reward:risk survive? ──")
    print("  realized R against the R:R the engine advertised, resolved rows only.")
    print()
    for decision in ("approved", "rejected", "later", "archived"):
        keys = [k for k, r in by_key.items()
                if r["decision"] == decision and states[k] in RESOLVED]
        if not keys:
            continue
        realized = [realized_r(by_key[k], states[k]) for k in keys]
        realized = [r for r in realized if r is not None]
        promised = [by_key[k].get("reward_risk_from_price") or by_key[k].get("reward_risk")
                    for k in keys]
        promised = [p for p in promised if p is not None]
        if not realized:
            continue
        exp = sum(realized) / len(realized)
        adv = sum(promised) / len(promised) if promised else float("nan")
        print(f"  {decision:10} n={len(realized):3}  expectancy {exp:+.2f}R   "
              f"advertised {adv:.2f}R on the winners")

    print("\n── 5. the geometry: how far away are the two levels? ──")
    print("  distances in units of the instrument's own median daily range over the 14 bars")
    print("  before the decision. this is the section that explains all the others, and it is")
    print("  not censored — it is a property of the candidate, not of what happened next.")
    print()
    stopped = [k for k, s in states.items() if s in (STOP, AMBIGUOUS)]
    same = [k for k in stopped if outcomes[k].get("same_bar")]
    if stopped:
        print(f"  {len(same)}/{len(stopped)} stops landed on the very bar that filled the "
              f"entry ({len(same) / len(stopped):.0%}).")
        print("  that is not a small-stop artifact — the median stop sits well beyond one")
        print("  daily range. it is a selection effect, and the sharpest thing this probe")
        print("  found: the bar that reaches down to a resting limit is by construction a bar")
        print("  moving hard against the entry, and most of the time it keeps going.")
        for decision in ("approved", "rejected", "later", "archived"):
            group = [k for k in stopped if by_key[k]["decision"] == decision]
            if not group:
                continue
            hit = sum(1 for k in group if outcomes[k].get("same_bar"))
            print(f"    {decision:10} {hit:3}/{len(group):3}")

    spans = [(k, s) for k, s in ((k, span(by_key[k], load_series)) for k in by_key) if s]
    if spans:
        print()
        for label, idx in (("stop", 0), ("target", 1)):
            vals = sorted(s[idx] for _, s in spans)
            pct = [vals[int(p * len(vals))] for p in (0.1, 0.5, 0.9)]
            print(f"  {label:7} distance  p10 {pct[0]:5.1f}  median {pct[1]:5.1f}  "
                  f"p90 {pct[2]:5.1f}  daily ranges")
        print()
        print("  a stop is reachable in a day and a target is weeks away. that asymmetry is")
        print("  why sections 3 and 4 read as they do, and no amount of waiting fixes it on")
        print("  its own — it is the engine's own choice of levels (§27).")

    print("\n── how long the open rows have been waiting ──")
    print("  no horizon constant is applied (§2). this is what 'open' and 'nofill' are made")
    print("  of, and it says whether the rates above are settled or still maturing.")
    print()
    for state in (OPEN, NOFILL):
        bars = sorted(o.get("bars", 0) for o in outcomes.values() if o["state"] == state)
        if not bars:
            continue
        print(f"  {state:8} n={len(bars):3}  bars elapsed: "
              f"min {bars[0]}, median {bars[len(bars) // 2]}, max {bars[-1]}")

    ambiguous = tally.get(AMBIGUOUS, 0)
    resolved = sum(tally.get(s, 0) for s in RESOLVED)
    if resolved:
        print(f"\n  {ambiguous} of {resolved} resolved rows are ambiguous "
              f"({ambiguous / resolved:.0%}) — one daily bar touched both levels and is")
        print("  charged as a stop. that convention moves every rate above by at most "
              f"{ambiguous / resolved:.0%}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--decisions", type=Path, default=DECISIONS)
    parser.add_argument("--max-wait-days", type=int, default=None,
                        help="give up on an unfilled limit after N bars. off by default — "
                             "see the module docstring on why there is no horizon here.")
    parser.add_argument("--dump", type=Path,
                        help="write per-candidate outcomes as JSONL for further slicing. "
                             "never write this into data/setups/decisions.jsonl.")
    args = parser.parse_args()

    rows = load_rows(args.decisions)
    load_series = build_series_loader()

    outcomes = {
        row["candidate_key"]: classify(row, load_series, max_wait_days=args.max_wait_days)
        for row in rows
    }

    report(rows, outcomes, load_series)

    if args.dump:
        with args.dump.open("w", encoding="utf-8") as f:
            for row in rows:
                out = outcomes[row["candidate_key"]]
                f.write(json.dumps({
                    "candidate_key": row["candidate_key"],
                    "asset": row["asset"],
                    "decision": row["decision"],
                    "direction": row["direction"],
                    "score": row["score"],
                    "score_version": row["score_version"],
                    "decided_at": row["decided_at"],
                    "state": out["state"],
                    "realized_r": realized_r(row, out["state"]),
                    "bars": out.get("bars"),
                    "detail": out.get("detail"),
                }, default=str) + "\n")
        print(f"\nper-candidate outcomes -> {args.dump}")


if __name__ == "__main__":
    main()
