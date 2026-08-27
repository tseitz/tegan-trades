from datetime import date, timedelta
from types import SimpleNamespace

from core.canon import load_registry
from core.review import NO_VIEW, UNREADABLE, Holding
from oracle.portfolios import Portfolio, Position
from oracle.route import RoutingTable
from oracle.series import Bar, PriceSeries
from review.cli import CONFIG_DIR, build_readings

AS_OF = date(2025, 6, 30)
REGISTRY = load_registry(CONFIG_DIR)


def _series(symbol="VTI", *, source="yahoo", bars=400, start=100.0):
    """A long, gently trending series — enough history for weekly swings, a dealing range
    and at least one order block to exist."""
    out = []
    day = AS_OF - timedelta(days=bars)
    price = start
    for i in range(bars):
        price += 0.9 if (i // 15) % 2 == 0 else -0.6
        out.append(Bar(date=day + timedelta(days=i), open=price, high=price + 1.5,
                       low=price - 1.5, close=price))
    return PriceSeries(symbol=symbol, source=source, bars=tuple(out))


def _table(**consensus):
    return RoutingTable(curated={}, coinbase_symbols=frozenset(),
                        kraken_symbols=frozenset(), domain_consensus=consensus)


def _book(*tickers, domain="stock"):
    return Portfolio(
        name="test", horizon="long",
        positions=tuple(
            Position(holding=Holding(ticker=t, shares=1.0, cost=None), domain=domain)
            for t in tickers
        ),
    )


def _folded(person, lean, *, published_at="2025-06-01"):
    return SimpleNamespace(
        person_canonical=person, asset_canonical="VTI",
        current=SimpleNamespace(lean=lean,
                                source=SimpleNamespace(published_at=published_at)),
    )


def test_a_priced_holding_gets_a_price_and_a_weekly_trend():
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(VTI="stock"), folded_by_asset={}, as_of=AS_OF,
        series_cache={"VTI": _series()},
    )
    assert len(readings) == 1
    assert readings[0].price is not None
    assert readings[0].weekly_trend is not None


def test_an_unroutable_holding_is_reported_not_dropped():
    """Silently skipping it would leave a position you own out of a review of what you own —
    the one failure this whole command exists to prevent."""
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(), folded_by_asset={}, as_of=AS_OF, series_cache={},
    )
    assert [r.holding.ticker for r in readings] == ["VTI"]
    assert readings[0].price is None
    assert readings[0].location.where == UNREADABLE


def test_a_routable_but_uncached_holding_is_also_reported():
    """'Nobody has fetched this yet' and 'this is not an instrument' are opposite problems
    with opposite fixes, and both end here as a row with no price rather than as no row."""
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(VTI="stock"), folded_by_asset={}, as_of=AS_OF,
        series_cache={"VTI": None},
    )
    assert readings[0].price is None


def test_the_roster_split_reaches_the_reading():
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(VTI="stock"), as_of=AS_OF,
        folded_by_asset={"VTI": [_folded("A", "bearish"), _folded("B", "bearish")]},
        series_cache={"VTI": _series()},
    )
    assert readings[0].roster.bears == 2


def test_an_asset_with_no_stances_reads_as_silent_not_as_an_error():
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(VTI="stock"), folded_by_asset={}, as_of=AS_OF,
        series_cache={"VTI": _series()},
    )
    assert readings[0].verdict == NO_VIEW


def test_readings_come_back_in_file_order():
    """Ranking is the renderer's job. Reordering here too would give two places that decide
    what you look at first, and they would drift."""
    readings, _ = build_readings(
        _book("AAA", "BBB", "CCC"), registry=REGISTRY, table=_table(), folded_by_asset={}, as_of=AS_OF,
        series_cache={},
    )
    assert [r.holding.ticker for r in readings] == ["AAA", "BBB", "CCC"]


def test_the_structure_each_reading_was_drawn_from_comes_back_alongside_it():
    """Scanning for levels needs it, and rebuilding structure is the expensive half of the
    loop. One entry per position, aligned with the readings, `None` where nothing priced."""
    result = build_readings(
        _book("VTI", "NOPE"), registry=REGISTRY, table=_table(VTI="stock"),
        folded_by_asset={}, as_of=AS_OF, series_cache={"VTI": _series()},
    )
    assert len(result.contexts) == len(result.readings) == 2
    assert result.contexts[0] is not None
    assert result.contexts[1] is None
