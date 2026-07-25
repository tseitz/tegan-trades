"""Resolve the assets named in a plain-language question — deterministically.

No LLM. `cfg/assets.yaml` already encodes every alias the corpus uses, so a token scan
against it answers "which asset is this question about" without a call, and answers it
the same way every time.
"""
from __future__ import annotations

import re

from core.canon import Registry, normalize

# Longest curated alias is a few words ("bitcoin dominance", "s&p 500").
_MAX_ALIAS_WORDS = 3
# An all-caps token is an explicit ticker signal; bound the length so shouting or an
# acronym in prose doesn't get treated as a symbol.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,5}$")

_WORD_RE = re.compile(r"[A-Za-z0-9.&]+")


def parse_assets(question: str, registry: Registry) -> list[str]:
    """Canonical assets named in `question`, in order of first appearance.

    Two matchers, and the split between them is the whole point:

    - **curated aliases** (`registry.assets`) are matched case-insensitively, including
      multi-word ones, longest-first.
    - **bare tickers** (`registry.tickers`, the ~968-symbol CoinGecko snapshot) are
      matched ONLY when the token is written in caps.

    That second restriction is not fussiness. The snapshot contains `ALL`, `FOR`, `ONE`,
    `UP`, `ME` and friends, so matching lowercase words against it would resolve almost
    any English question to a random memecoin. Capitalisation is the user's own signal
    that they meant a symbol.
    """
    if not question:
        return []

    tokens = [(m.group(0), m.start()) for m in _WORD_RE.finditer(question)]
    found: list[str] = []
    consumed: set[int] = set()

    # Longest alias first, so "bitcoin dominance" wins over "bitcoin".
    for span in range(_MAX_ALIAS_WORDS, 0, -1):
        for i in range(len(tokens) - span + 1):
            if any(j in consumed for j in range(i, i + span)):
                continue
            phrase = normalize(" ".join(t for t, _ in tokens[i:i + span]))
            canonical = registry.assets.get(phrase) or registry.assets.get(phrase.rstrip("."))
            if canonical is None and span == 1:
                bare = tokens[i][0]
                if _TICKER_RE.match(bare) and bare in registry.tickers:
                    canonical = bare
            if canonical is not None:
                consumed.update(range(i, i + span))
                found.append((tokens[i][1], canonical))

    ordered = [c for _, c in sorted(found, key=lambda p: p[0])]
    seen: set[str] = set()
    return [c for c in ordered if not (c in seen or seen.add(c))]
