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
asset. Only the second belongs here, which is why the file is not derived automatically from
``reason_note`` — the notes cannot be told apart mechanically, and inferring a permanent rule
from a temporary one would silently delete a market from the queue forever.

**``append`` does not weaken that rule, and the distinction is worth stating precisely.** It is
only ever called when the archive prompt has *asked which kind this is* and been told "asset".
That is a stated meaning, not an inference from prose — the thing the paragraph above refuses
is reading permanence out of free text, and no such reading happens. The reason typed at that
prompt becomes the entry's required reason.

The append is textual rather than a ``safe_dump`` of the parsed file, because re-dumping would
round-trip the data faithfully and destroy this header — which is where the whole
rejection-versus-exclusion distinction is written down, and is the part a future session needs
most. Every write is parsed and verified *before* it lands, so a file that gates the entire
queue can never be left half-edited by a mistyped prompt.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_EXCLUSIONS = "exclusions.yaml"


class _Quoted(str):
    """A reason, marked so the dumper emits it double-quoted like the hand-written entries.

    Cosmetic but not pointless: this file is curated and reviewed by hand, and an appended
    entry that renders in a different style than its neighbours reads as machine spoor in a
    file whose whole value is that a human vouched for every line. Escaping is still entirely
    the dumper's job — this only selects a style, which is the difference between it and the
    hand-formatting ``append`` deliberately avoids.
    """


yaml.SafeDumper.add_representer(
    _Quoted,
    lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"'),
)


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


def append(path: Path, symbol: str, reason: str) -> bool:
    """Add ``symbol`` to the gate. ``True`` if written, ``False`` if already excluded.

    Called from the archive prompt once you have said the archive is about the *asset*. See the
    module docstring for why that is not the automatic derivation this file otherwise refuses.

    Already-excluded returns ``False`` rather than raising or appending: archiving the same
    asset again across sessions is ordinary, and a duplicate mapping key is the one edit that
    would make the file quietly lose an entry, since PyYAML keeps only the last. The existing
    reason wins — it is the one that has been reviewed and committed.

    A blank reason raises, because ``load`` refuses reason-less entries. Writing one would turn
    a mistyped prompt into a queue that cannot start, which is far worse than a failed archive:
    the caller still records the decision in the sidecar either way.
    """
    path = Path(path)
    symbol = str(symbol).strip().upper()
    reason = str(reason).strip()
    if not reason:
        raise ValueError(f"excluded asset {symbol!r} needs a reason")
    if symbol in load(path):
        return False

    original = path.read_text(encoding="utf-8") if path.exists() else ""
    body = original if original.endswith("\n") or not original else original + "\n"
    # ``safe_dump`` of the single pair, indented — never an f-string. The reason is free text
    # typed at a prompt, so it will eventually hold a colon, a quote or a leading '#', and the
    # dumper is what decides when those need quoting. Hand-formatting is how this breaks.
    entry = "  " + yaml.safe_dump({symbol: _Quoted(reason)}, allow_unicode=True,
                                  default_flow_style=False, sort_keys=False)
    if "assets" not in (yaml.safe_load(body) or {}):
        body += "assets:\n"
    candidate = body + entry

    # Verify before writing, not after. This file gates the whole queue, so the failure mode
    # worth designing against is a half-written edit that makes the next run unstartable.
    # Unparseable and parses-to-the-wrong-thing are the same outcome here, so both surface as
    # ValueError — the caller's contract is "the file is untouched", not which way it failed.
    try:
        parsed = yaml.safe_load(candidate) or {}
        entries = parsed.get("assets") or {}
        round_tripped = str(entries.get(symbol, "")).strip()
    except yaml.YAMLError as exc:
        raise ValueError(f"appending {symbol!r} would not parse; file left untouched") from exc
    if round_tripped != reason:
        raise ValueError(f"appending {symbol!r} would not round-trip; file left untouched")

    path.write_text(candidate, encoding="utf-8")
    return True


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
