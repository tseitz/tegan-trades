"""Assets you don't trade, whatever the setup says.

**A gate, and deliberately not a score.** Per the rule at the top of ``core.setups``: gate a
rule you wrote or a fact that is missing, score a measurement on a continuum. "Zero interest in
PNUT" is a rule, and there is no confluence strong enough to make it false.

**Why rejecting the candidate is not enough.** ``drop_decided`` keys on the *zone*, so a
rejection buries one order block and the next block to form on the same instrument asks again.
Measured on the v3 queue, 2026-07-27: OIL and CL had both been rejected for the asset — the
notes say so — and reappeared on **4 of the 59 undecided rows**, one at score 0.711. Without a
standing rule the same "no" has to be re-entered indefinitely.

**Why it matters beyond the annoyance.** These decisions are the ground truth §4 exists to
mine, and an asset-level "no" is not scorer signal. Of 7 rejections in the first real v2
session, 3 were disinterest in the instrument rather than a judgment on the setup — 43% of the
negative labels, which §4c warns would poison the correlation if pooled. Filtering them out of
the queue means the rejections that remain are all about trade quality.

**A conditional pass is a rejection, not an exclusion.** "not sure I'm shorting oil at these
prices with the Iran conflict going on" is about *now*; "Zero interest in PNUT" is about the
asset. Only the second belongs here, which is why the file is hand-curated rather than derived
automatically from ``reason_note`` — the notes cannot be told apart mechanically, and inferring
a permanent rule from a temporary one would silently delete a market from the queue forever.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_EXCLUSIONS = "exclusions.yaml"


def load(path: Path) -> dict[str, str]:
    """``{CANONICAL_SYMBOL: reason}``, or empty when the file is absent.

    Absent is a legitimate state — the gate is opt-in — but a *malformed* entry is not, so a
    missing reason raises rather than defaulting to blank. The note is the only record of why
    an asset stopped appearing, and "" would make "excluded, no reason given" and "excluded for
    a reason nobody wrote down" indistinguishable.
    """
    path = Path(path)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("assets") or {}
    out: dict[str, str] = {}
    for symbol, reason in entries.items():
        if not reason or not str(reason).strip():
            raise ValueError(f"excluded asset {symbol!r} needs a reason")
        out[str(symbol).upper()] = str(reason).strip()
    return out


def unmatched_symbols(excluded: dict[str, str], corpus_assets) -> tuple[str, ...]:
    """Excluded symbols that no thesis in the corpus mentions.

    A typo fails *open*: ``PNUTT`` matches nothing, so the asset it was meant to suppress keeps
    appearing while the config looks correct. That is the §6h failure class — a hand-recorded
    fact with no expiry and no verification — and the cheapest guard is to say so on every run.

    **Checked against the corpus, not against the canon registry**, and the first version got
    this wrong in a way worth not repeating. ``CL`` resolves as *unresolved* — it is in neither
    ``registry.assets`` nor ``registry.tickers`` — yet it produces real candidates, so a
    registry check flagged a legitimately-excluded asset as a typo in the same breath as
    correctly excluding it. A warning that fires on correct config is worse than none: it
    teaches you to skip the line where the real typo will eventually appear.
    """
    known = {str(asset).upper() for asset in corpus_assets}
    return tuple(symbol for symbol in sorted(excluded) if symbol not in known)


def partition(candidates, excluded: dict[str, str]):
    """``(kept, removed)``. Removed is returned, never quietly dropped — a filter nobody can
    see is indistinguishable from a corpus that went quiet, which is the whole complaint §6d
    and §6h are about."""
    if not excluded:
        return list(candidates), []
    kept, removed = [], []
    for candidate in candidates:
        (removed if candidate.asset in excluded else kept).append(candidate)
    return kept, removed
