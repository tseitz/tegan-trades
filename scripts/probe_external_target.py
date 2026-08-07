"""Should the target be the nearest obstruction, or the range boundary?

Motivation: ``core.exits`` picks ``levels[0]`` — the *nearest* structural level — as the target
and calls everything past it a runner. The roster's methodology says the opposite. TraderMayne,
``NSTmMdnQg7Y``: "An order block itself is internal range liquidity, so generally we're going to
be targeting external range liquidity." And ``v2CY0WvFj8o`` names our behaviour as the mistake:
"they're going to fill a long down here, and then it's going to go up, and it's going to take
out this buy-side liquidity here *that's still within the range*, and they're going to close
their trade… and they're going to go, 'What the heck? I closed early.'"

Under that doctrine the internal levels are partials and the destination is external range
liquidity — the dealing-range boundary, which ``_truncate_at_range`` already computes and caps
every ladder at. So the proposed target is not a new construct: it is the level the engine
already found and then declined to use.

Three questions, in the order they change the answer:

1. **Entry liquidity** — is the entry inside the dealing range (internal, target external) or
   outside it (external sweep, target internal)? Mayne's test is purely positional:
   "if it's outside of the current dealing range, that is your external range liquidity."
   This is the branch that was an open design question, and section 1 is here to say how much
   of the queue actually rides on it.
2. **Target migration** — for internal entries, what does the target and its R:R become?
3. **Gate survival** — Mayne's floor is 2:1 ("the moment you go below that two to one
   threshold, the math is no longer protecting you"), against ``MIN_REWARD_RISK``'s current
   1.0. How many candidates survive each combination of target rule and floor?

``permits()`` does **not** fence out the external case: ``position_in_range`` clamps rather than
returning None (``core/structure.py:152``), so a long whose price swept below the range low
reads position 0.0 → DISCOUNT → passes the gate as a maximally-deep discount zone. Section 1
exists because that case is reachable, not hypothetical.

Contexts here are daily+weekly and candidates are built with ``triggers_on=False``, matching
``probe_target_reachability.py`` so the two read the same rung. The live queue promotes some
assets to H12, so an individual row can differ from what ``setups`` prints; the population
still answers the question.

**What it measured, 2026-08-06, and what shipped as v8.** Of 52 candidates: 36 internal
entries, 11 external with price still inside its range, and 5 whose *price* had left the range
entirely. Those last 5 were a clamping bug rather than a trading question — TSLA long at 321.55
against a 368.60-432.86 range read as a maximally deep discount — and are now refused
``wrong_side_of_range`` by ``DealingRange.position_at``. Of the 11, six sat within
``MAX_SWEEP_WIDTHS`` of the boundary and keep the nearest-level target; five did not and are
refused ``entry_outside_range``.

Target migration on the 36: 14 already targeted the boundary, 22 moved, median R:R **+0.89**
and worst **+7.13**, median 1 partial rung below the boundary and worst 6.

On the live queue afterwards — a different population from the 52 above, since it promotes to
H12 and applies triggers — the new refusals are ``wrong_side_of_range`` 1089,
``entry_outside_range`` 61, ``reward_risk_too_low`` 89, and ``no_external_target`` **zero**.
That last is expected: reaching it needs a block spanning from below the range's midpoint to
above its high, which is the only shape that puts price in discount with the entry past the
boundary. The branch is kept and tested because it is reachable, not because it fires.

**Re-running this after v8 does not reproduce the table above.** Sections 2 and 3 compare
"nearest" against "boundary" by reading ``target`` as the nearest — which it no longer is. The
numbers here are the before-and-after record; the probe still answers section 1 correctly.

Run: ``uv run python scripts/probe_external_target.py``
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from core.canon import load_registry
from core.exits import RANGE_BOUND
from core.setups import MIN_REWARD_RISK, build_context
from oracle import cache, corpus, listings
from oracle.resample import to_weekly
from oracle.route import DerivedRef, OracleRef, load_routing_table, route
from oracle.setups_cli import CONFIG_DIR, _load_daily, build_candidates

# Mayne's floor, against MIN_REWARD_RISK's current 1.0. Named here rather than imported
# because the point of the probe is to measure the two side by side.
PROPOSED_MIN_RR = 2.0


def levels_of(candidate) -> list:
    """The full nearest-first ladder, target included.

    ``core.exits`` returns one sequence and ``core.setups`` splits it into ``target`` +
    ``ladder``; this puts it back together rather than re-deriving it, so the probe measures
    what the engine actually computed.
    """
    from core.exits import ExitLevel

    head = ExitLevel(price=candidate.target, kind=candidate.target_source,
                     reward_risk=candidate.reward_risk)
    return [head, *candidate.ladder]


def outside_by(candidate, dealing_range) -> float:
    """How far outside the range the entry sits, in range-widths. <=0 means inside.

    Measured in widths rather than price or ATR because the question is about the range's own
    geometry: a sweep is price poking *just* past the boundary before reversing, which is a
    small fraction of a width. An entry a whole width beyond it is not a sweep — it is a zone
    from a different price regime than the range it is being measured against, i.e. the range
    is stale. Mayne draws that line himself: "as price breaks out of this range, it forms a new
    range" (``v2CY0WvFj8o``), so beyond the boundary the range is supposed to be redrawn.
    """
    width = dealing_range.high - dealing_range.low
    if width <= 0:
        return 0.0
    if candidate.direction == "long":
        return (dealing_range.low - candidate.entry) / width
    return (candidate.entry - dealing_range.high) / width


def entry_is_external(candidate, dealing_range) -> bool:
    """Whether the entry sits outside the dealing range — Mayne's positional test.

    Anchored on ``entry`` rather than ``price`` deliberately: ``permits()`` tests price, but the
    question here is where the resting order sits, and the two can disagree.
    """
    return outside_by(candidate, dealing_range) >= 0.0


def proposed(candidate):
    """The external-range-liquidity level, or None when the ladder has no boundary rung.

    ``_truncate_at_range`` caps every ladder at the boundary and keeps the boundary itself, so
    when a RANGE_BOUND rung is present it is the furthest one. Its absence means no external
    liquidity lies beyond the entry on the trade's side — which under the proposed rule is a
    refusal, per Mayne's disqualifier #3: "if there's no clear liquidity, there is no trade."
    """
    return next((lv for lv in levels_of(candidate) if lv.kind == RANGE_BOUND), None)


def main() -> int:
    as_of = datetime.now(UTC).date()
    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    candidates, stats = build_candidates(
        rows, registry, as_of=as_of, listings_map=listings_map, triggers_on=False,
    )

    table = load_routing_table(
        CONFIG_DIR, [(r.asset, r.domain) for r in rows], listings=listings_map
    )
    series_cache: dict = {}
    contexts: dict = {}
    for asset in sorted({c.asset for c in candidates}):
        resolved = route(asset, table)
        if not isinstance(resolved, OracleRef | DerivedRef):
            continue
        daily = _load_daily(resolved, table=table, series_cache=series_cache)
        if daily is None:
            continue
        ctx = build_context(daily.bars, to_weekly(daily).bars, as_of=as_of)
        if ctx is not None:
            contexts[asset] = (ctx, daily)

    print(f"as_of {as_of.isoformat()}  |  {len(candidates)} candidates")
    print(f"target_source now: {Counter(c.target_source for c in candidates).most_common()}\n")

    internal, external, no_range, unscored = [], [], [], 0
    for candidate in sorted(candidates, key=lambda c: -c.score):
        found = contexts.get(candidate.asset)
        if found is None:
            unscored += 1
            continue
        ctx, _daily = found
        if ctx.dealing_range is None:
            no_range.append(candidate)
            continue
        bucket = external if entry_is_external(candidate, ctx.dealing_range) else internal
        bucket.append(candidate)

    scored = len(internal) + len(external) + len(no_range)
    print(f"── 1. entry liquidity: {len(internal)} internal, {len(external)} external, "
          f"{len(no_range)} no dealing range ({scored} scored, {unscored} unpriced) ──")
    if external:
        print("\n  external entries, furthest outside the range first. ``widths`` is the whole"
              "\n  discriminator: a sweep pokes just past the boundary, a stale range does not.")
        print(f"  {'asset':10} {'dir':5} {'tf':7} {'entry':>11} {'range_low':>11} "
              f"{'range_high':>11} {'widths':>7} {'R:R now':>8}")
        ranked = sorted(external,
                        key=lambda c: -outside_by(c, contexts[c.asset][0].dealing_range))
        for c in ranked:
            dr = contexts[c.asset][0].dealing_range
            print(f"  {c.asset:10} {c.direction:5} {c.zone_timeframe:7} {c.entry:11.4f} "
                  f"{dr.low:11.4f} {dr.high:11.4f} "
                  f"{outside_by(c, dr):7.2f} {c.reward_risk:8.2f}")

    print("\n── 2. target migration: internal entries, nearest → range boundary ──")
    print(f"  {'asset':10} {'dir':5} {'src now':12} {'target now':>11} {'R:R':>6} | "
          f"{'boundary':>11} {'R:R':>6} {'rungs':>6}")
    migrated, no_boundary = [], []
    for c in internal:
        far = proposed(c)
        if far is None:
            no_boundary.append(c)
            continue
        migrated.append((c, far))
        rungs = sum(1 for lv in levels_of(c) if lv.price != far.price)
        print(f"  {c.asset:10} {c.direction:5} {c.target_source:12} {c.target:11.4f} "
              f"{c.reward_risk:6.2f} | {far.price:11.4f} {far.reward_risk:6.2f} {rungs:6d}")

    if migrated:
        gains = sorted(far.reward_risk - c.reward_risk for c, far in migrated)
        unchanged = sum(1 for c, far in migrated if far.price == c.target)
        print(f"\n  {len(migrated)} internal entries have a boundary rung; "
              f"{unchanged} already target it (no change).")
        print(f"  R:R delta — median {gains[len(gains) // 2]:+.2f}, "
              f"range {min(gains):+.2f}..{max(gains):+.2f}")
        partials = sorted(sum(1 for lv in levels_of(c) if lv.price != far.price)
                          for c, far in migrated)
        print(f"  partial rungs below the boundary — median {partials[len(partials) // 2]}, "
              f"max {max(partials)}")
    if no_boundary:
        print(f"\n  {len(no_boundary)} internal entries have NO boundary rung → refused under "
              f"the proposed rule:")
        for c in no_boundary:
            print(f"    {c.asset:10} {c.direction:5} {c.target_source:12} "
                  f"R:R {c.reward_risk:5.2f}")

    print(f"\n── 3. gate survival: current floor {MIN_REWARD_RISK:.1f} vs proposed "
          f"{PROPOSED_MIN_RR:.1f} ──")
    pool = internal + external
    now_rr = {c.asset + c.direction: c.reward_risk for c in pool}
    new_rr = {}
    for c in internal:
        far = proposed(c)
        if far is not None:
            new_rr[c.asset + c.direction] = far.reward_risk
    for c in external:  # external entries keep today's nearest-level target
        new_rr[c.asset + c.direction] = c.reward_risk

    for label, table_rr, floor in (
        ("target=nearest, floor 1.0  (today)", now_rr, MIN_REWARD_RISK),
        ("target=nearest, floor 2.0        ", now_rr, PROPOSED_MIN_RR),
        ("target=boundary, floor 1.0       ", new_rr, MIN_REWARD_RISK),
        ("target=boundary, floor 2.0 (prop)", new_rr, PROPOSED_MIN_RR),
    ):
        survivors = [v for v in table_rr.values() if v >= floor]
        print(f"  {label}  →  {len(survivors):3d} of {len(pool)} survive")

    # The blind spot, stated rather than left for a reader to infer. ``candidates`` is the
    # POST-gate population, so every count above is drawn from rows that already cleared
    # today's target rule and today's floor. Rows refused as ``reward_risk_too_low`` were
    # measured against the *nearest* level; moving the target to the boundary raises their
    # ratio too, and some would qualify. None of them are visible here, so the survivor counts
    # are a floor on the new queue and not an estimate of it.
    refused = stats.rejections.get("reward_risk_too_low", 0)
    print("\n── 4. what this probe cannot see ──")
    print(f"  {refused} rows were refused ``reward_risk_too_low`` against the nearest level and")
    print("  never became candidates. A boundary target raises their R:R as well, so section 3")
    print("  is a LOWER BOUND on the proposed queue — re-run the engine to size the real one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
