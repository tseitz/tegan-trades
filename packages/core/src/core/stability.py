"""Is a ranking difference real, or just resampling noise?

Built after the phase-3.3a horizon sweep reported "top-3 CHURNS" and that turned out to be
uninterpretable on its own. Reshuffling under a parameter change only means something if it
exceeds the reshuffling you'd get from *nothing* — bootstrap-resampling the same calls at a
fixed parameter. On this corpus the horizon churn sat inside that band, which is how we
learned the horizons weren't the problem.
"""
from __future__ import annotations

from itertools import combinations


def kendall_tau(left: list[str], right: list[str]) -> float | None:
    """Rank correlation over the names present in *both* orderings.

    Membership genuinely differs between orderings here — a longer horizon leaves fewer
    people above the min-sample floor — so intersecting is the honest comparison rather
    than penalising a dropout as if it were a rank change. None when under two names
    overlap, because tau is undefined rather than 1.0 there.
    """
    common = [name for name in left if name in right]
    if len(common) < 2:
        return None
    lhs = {name: i for i, name in enumerate(left)}
    rhs = {name: i for i, name in enumerate(right)}
    concordant = discordant = 0
    for a, b in combinations(common, 2):
        sign = (lhs[a] - lhs[b]) * (rhs[a] - rhs[b])
        concordant += sign > 0
        discordant += sign < 0
    total = concordant + discordant
    return (concordant - discordant) / total if total else None


# A hard threshold at the 5th percentile flips on hundredths — observed 0.43 against a
# floor of 0.44 is a tie, not a finding. Anything this close is reported as marginal so a
# borderline result can't be read as a clean verdict.
MARGIN = 0.05


def rank_stability(*, observed: float | None, noise_floor: tuple[float, float] | None) -> str:
    """Classify an observed tau against a bootstrap noise band.

    ``within-noise`` — the swept parameter moved the ranking no more than chance would, so
    the parameter is not what's wrong. ``beyond-noise`` — it genuinely matters.
    ``marginal`` — too close to the boundary to call either way.
    """
    if observed is None or noise_floor is None:
        return "unknown"
    lower = noise_floor[0]
    if observed >= lower:
        return "within-noise"
    return "marginal" if lower - observed <= MARGIN else "beyond-noise"
