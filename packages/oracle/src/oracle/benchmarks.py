"""What "just holding the market" means, per domain.

Lives in its own module because both halves need the same answer: ``fetch_cli`` must
guarantee the series exist over the **whole corpus span**, and ``score_cli`` reads them.

The full-span guarantee is the subtle part. A benchmark is also an ordinary asset — SPX is
discussed 204 times — so if it were fetched on the normal path its window would start at
its own first *mention*. ^GSPC was first mentioned 2024-09-25 while the corpus opens
2024-07-31, which silently stripped the benchmark off every stock/macro call in those
first two months and dropped them from the headline metric without a word.
"""
from __future__ import annotations

DEFAULT_DOMAIN = "_default"

# BTC for crypto (what a crypto book is actually measured against), S&P 500 otherwise.
BENCHMARKS: dict[str, tuple[str, str]] = {
    "crypto": ("coinbase", "BTC-USD"),
    DEFAULT_DOMAIN: ("yahoo", "^GSPC"),
}


def benchmark_for(domain: str) -> tuple[str, str]:
    return BENCHMARKS.get(domain, BENCHMARKS[DEFAULT_DOMAIN])


def benchmark_refs() -> set[tuple[str, str]]:
    """The distinct (source, symbol) pairs that must be cached over the full corpus span."""
    return set(BENCHMARKS.values())
