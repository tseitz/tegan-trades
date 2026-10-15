from __future__ import annotations

from core import networth
from core.review import Holding, Location, Reading, RosterLean
from oracle.portfolios import Mandate, Portfolio, Position
from review.cli import ReviewResult


def _reading(ticker, price, shares=1.0) -> Reading:
    return Reading(
        holding=Holding(ticker=ticker, shares=shares, cost=None),
        roster=RosterLean(lean="bullish", bulls=1, bears=0, people=1, newest=None,
                          age_days=None, voices=("A",)),
        location=Location(where="at_support", basis="range", position=0.1),
        verdict="HOLD", price=price, weekly_trend="uptrend",
    )


def test_against_a_real_review_result_and_portfolio():
    """`of()` reaches two attributes deep into types it never imports
    (`.readings[*].market_value` and `.book.cash`/`.book.cash_by_account`), which a table of
    `SimpleNamespace`s cannot catch drifting."""
    mandate = Mandate(name="retirement", benchmarks=(), horizon="macro",
                      risk_posture="conservative")
    position = Position(holding=Holding(ticker="SPY", shares=2.0, cost=90.0), domain="stock")
    book = Portfolio(name="retirement", mandate=mandate, positions=(position,), cash=25.0)
    result = ReviewResult(book=book, readings=[_reading("SPY", 100.0, shares=2.0)],
                          contexts=(None,), mismatched=(), levels=((), (), 0), chains=(),
                          macro=())

    net = networth.of([result], parked=0.0)
    assert net.total == 225.0
    assert net.pots == 1
    assert net.unpriced == 0
    assert net.by_book == {"retirement": 225.0}
