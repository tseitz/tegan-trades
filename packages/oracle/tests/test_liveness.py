"""Dormancy is a comparison, never an absolute count — these tests exist to hold that line.

The tempting implementation ("zero rows means dead") is wrong in the one case that matters:
a night the fetcher did not run produces zero rows for *everything*, and a gate built on
absolute counts would then refuse the entire queue while reporting it as market structure.
Every test below that expects ``False`` is guarding some version of that mistake.
"""
from datetime import UTC, datetime, timedelta

from core.funding import FundingRate

from oracle import funding_store, liveness

NOW = datetime(2026, 7, 27, 20, 0, tzinfo=UTC)


def _seed(tmp_path, rows):
    funding_store.append(rows, root=tmp_path)


def _rate(symbol, venue="hyperliquid:xyz", rate=1.354e-05, hours=1.0, ago_days=1):
    return FundingRate(
        venue=venue, symbol=symbol, rate=rate, interval_hours=hours,
        observed_at=NOW - timedelta(days=ago_days),
    )


# The real shape of the finding: xyz:DXY is mapped, polled with its cohort, and has never
# once reported. Its siblings on the same dex have 502 observations each.
def test_a_listed_market_its_cohort_reports_and_it_does_not_is_dormant(tmp_path):
    _seed(tmp_path, [_rate("NVDA"), _rate("GOLD"), _rate("SP500")])
    assert liveness.dormant("DXY", root=tmp_path, now=NOW) is True


def test_a_market_that_reports_is_not_dormant(tmp_path):
    _seed(tmp_path, [_rate("NVDA"), _rate("DXY")])
    assert liveness.dormant("DXY", root=tmp_path, now=NOW) is False


def test_an_asset_no_venue_lists_is_not_dormant(tmp_path):
    # A different fact, and one REFUSAL_UNLISTED already names. Conflating them would report
    # "this market is dead" about a symbol that was never claimed to exist.
    _seed(tmp_path, [_rate("NVDA")])
    assert liveness.dormant("GLXY", root=tmp_path, now=NOW) is False


def test_an_asset_unlisted_on_this_venue_is_not_dormant(tmp_path):
    # NFLX is on hyperliquid and aster but not lighter.
    _seed(tmp_path, [_rate("US500", venue="lighter", hours=8.0)])
    assert liveness.dormant("NFLX", venue="lighter", root=tmp_path, now=NOW) is False


def test_an_empty_log_cannot_conclude_dormancy(tmp_path):
    # Nothing observed anywhere is a statement about the fetcher, not about the market.
    assert liveness.dormant("DXY", root=tmp_path, now=NOW) is False


def test_a_silent_cohort_cannot_conclude_dormancy(tmp_path):
    # The core book reported; the xyz dex did not. That is a dex-wide outage, and DXY's
    # silence carries no information on such a night.
    _seed(tmp_path, [_rate("BTC", venue="hyperliquid"), _rate("ETH", venue="hyperliquid")])
    assert liveness.dormant("DXY", root=tmp_path, now=NOW) is False


def test_the_core_book_is_not_a_cohort_for_a_hip3_market(tmp_path):
    # Mirror image of the above: xyz reported, the core book did not. BTC is a core-book
    # listing, so a busy dex says nothing about it.
    _seed(tmp_path, [_rate("NVDA"), _rate("GOLD")])
    assert liveness.dormant("BTC", root=tmp_path, now=NOW) is False


def test_observations_outside_the_window_do_not_keep_a_market_alive(tmp_path):
    # A market that stopped reporting three months ago is dormant now, however healthy the
    # log looks in aggregate.
    _seed(tmp_path, [_rate("DXY", ago_days=90), _rate("NVDA", ago_days=1)])
    assert liveness.dormant("DXY", window_days=30, root=tmp_path, now=NOW) is True
    assert liveness.dormant("DXY", window_days=365, root=tmp_path, now=NOW) is False


def test_dormancy_is_per_venue_not_per_asset(tmp_path):
    # RUT: IWM on lighter, IWMUSDT on aster. Trading on one venue is not evidence for the
    # other -- that is the whole reason the map is keyed (asset, venue).
    _seed(tmp_path, [
        _rate("IWMUSDT", venue="aster", hours=8.0),
        _rate("BTCUSDT", venue="aster", hours=8.0),
        _rate("US500", venue="lighter", hours=8.0),
    ])
    assert liveness.dormant("RUT", venue="aster", root=tmp_path, now=NOW) is False
    assert liveness.dormant("RUT", venue="lighter", root=tmp_path, now=NOW) is True


def test_an_aster_symbol_joins_on_its_own_native_ticker(tmp_path):
    # The map says NVDAUSDT; a naive join on the canonical "NVDA" would find the hyperliquid
    # row and call a dead aster market healthy.
    _seed(tmp_path, [_rate("NVDA", venue="hyperliquid:xyz"), _rate("BTCUSDT", venue="aster")])
    assert liveness.dormant("NVDA", venue="aster", root=tmp_path, now=NOW) is True
