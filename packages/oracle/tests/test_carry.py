from datetime import UTC, datetime, timedelta

import pytest
from core.funding import FundingRate
from oracle import carry, funding_store

NOW = datetime(2026, 7, 27, 20, 0, tzinfo=UTC)


def _seed(tmp_path, rows):
    funding_store.append(rows, root=tmp_path)


def _rate(symbol, venue="hyperliquid:xyz", rate=1.354e-05, hours=1.0, ago_days=1):
    return FundingRate(
        venue=venue, symbol=symbol, rate=rate, interval_hours=hours,
        observed_at=NOW - timedelta(days=ago_days),
    )


def test_a_hip3_symbol_joins_to_its_canonical_asset(tmp_path):
    # venue_map says NVDA -> "xyz:NVDA"; the log stores the bare "NVDA" with the dex in the
    # venue string. The join has to bridge exactly that.
    _seed(tmp_path, [_rate("NVDA")])
    got = carry.outlooks_for(["NVDA"], root=tmp_path, now=NOW)
    assert "NVDA" in got
    assert got["NVDA"].venue == "hyperliquid"
    assert got["NVDA"].median == pytest.approx(0.1186, abs=1e-3)


def test_a_canonical_name_differing_from_the_venue_ticker_still_joins(tmp_path):
    # GOLD -> "xyz:GOLD" on hyperliquid. The corpus never says "xyz:GOLD".
    _seed(tmp_path, [_rate("GOLD")])
    assert "GOLD" in carry.outlooks_for(["GOLD"], root=tmp_path, now=NOW)


def test_spx_joins_to_the_index_not_the_memecoin(tmp_path):
    # The log holds both: SP500 (the index) and SPX (SPX6900). Only one is SPX's listing.
    _seed(tmp_path, [_rate("SP500", rate=1e-05), _rate("SPX", venue="hyperliquid", rate=9e-05)])
    got = carry.outlooks_for(["SPX"], root=tmp_path, now=NOW)
    assert got["SPX"].median == pytest.approx(0.0876, abs=1e-3)   # from SP500, not SPX


def test_an_asset_no_venue_lists_yields_no_outlook(tmp_path):
    _seed(tmp_path, [_rate("NVDA")])
    got = carry.outlooks_for(["GLXY", "ILMN", "NVDA"], root=tmp_path, now=NOW)
    # Absent, so core skips the adjustment rather than costing them at an invented zero.
    assert set(got) == {"NVDA"}


def test_a_listed_but_unobserved_asset_yields_no_outlook(tmp_path):
    _seed(tmp_path, [_rate("NVDA")])
    assert "TSLA" not in carry.outlooks_for(["TSLA"], root=tmp_path, now=NOW)


def test_only_the_requested_venue_is_summarised(tmp_path):
    _seed(tmp_path, [
        _rate("NVDA", venue="hyperliquid:xyz", rate=1.354e-05),
        _rate("NVDA", venue="lighter", rate=3.2e-05, hours=8.0),
        _rate("NVDAUSDT", venue="aster", rate=0.0, hours=8.0),
    ])
    hl = carry.outlooks_for(["NVDA"], venue="hyperliquid", root=tmp_path, now=NOW)
    assert hl["NVDA"].median == pytest.approx(0.1186, abs=1e-3)
    assert hl["NVDA"].n == 1

    aster = carry.outlooks_for(["NVDA"], venue="aster", root=tmp_path, now=NOW)
    assert aster["NVDA"].median == 0.0   # measured zero, not a gap


def test_observations_outside_the_window_are_excluded(tmp_path):
    _seed(tmp_path, [_rate("NVDA", ago_days=1), _rate("NVDA", ago_days=90, rate=9e-05)])
    got = carry.outlooks_for(["NVDA"], root=tmp_path, window_days=30, now=NOW)
    assert got["NVDA"].n == 1


def test_the_spread_survives_into_the_outlook(tmp_path):
    _seed(tmp_path, [_rate("NVDA", rate=r / 1e6, ago_days=d)
                     for d, r in enumerate((5, 10, 15, 20, 25, 30, 35, 40, 45, 50), start=1)])
    got = carry.outlooks_for(["NVDA"], root=tmp_path, now=NOW)
    assert got["NVDA"].n == 10
    assert got["NVDA"].p90 > got["NVDA"].median


def test_an_empty_log_yields_nothing_rather_than_zeros(tmp_path):
    assert carry.outlooks_for(["NVDA", "TSLA"], root=tmp_path, now=NOW) == {}


def test_intervals_are_normalised_before_summarising(tmp_path):
    # Same economic rate, two venues, two conventions. Whichever is asked for, the annualized
    # figure must be the same -- this is the 8x error the whole subsystem guards against.
    _seed(tmp_path, [
        _rate("NVDA", venue="hyperliquid:xyz", rate=1e-05, hours=1.0),
        _rate("NVDAUSDT", venue="aster", rate=8e-05, hours=8.0),
    ])
    hl = carry.outlooks_for(["NVDA"], venue="hyperliquid", root=tmp_path, now=NOW)
    aster = carry.outlooks_for(["NVDA"], venue="aster", root=tmp_path, now=NOW)
    assert hl["NVDA"].median == pytest.approx(aster["NVDA"].median)
