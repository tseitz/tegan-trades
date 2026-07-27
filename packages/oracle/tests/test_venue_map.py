"""The venue map is the file that stands between a canonical symbol and a real order.
These tests assert the refusals, because a wrong answer here spends money."""
from oracle import venue_map


def test_the_spx_memecoin_collision_cannot_be_reached_by_name_match():
    # Every venue lists SPX6900 under a symbol that looks like the index. None of our
    # mappings may resolve to a bare "SPX" / "SPXUSDT".
    for venue in venue_map.venues_for("SPX"):
        got = venue_map.listing("SPX", venue)
        assert got is not None
        assert got.symbol not in {"SPX", "SPXUSDT"}


def test_spx_resolves_to_the_index_on_hyperliquid_and_lighter():
    assert venue_map.listing("SPX", "hyperliquid").symbol == "xyz:SP500"
    assert venue_map.listing("SPX", "lighter").symbol == "US500"


def test_spx_on_aster_is_an_etf_proxy_and_declares_its_scale():
    got = venue_map.listing("SPX", "aster")
    assert got.symbol == "SPYUSDT"
    assert got.is_proxy
    assert got.scale == 10.03


def test_a_one_to_one_listing_is_not_flagged_as_a_proxy():
    assert not venue_map.listing("SPX", "lighter").is_proxy
    assert not venue_map.listing("NVDA", "aster").is_proxy


def test_ndx_and_spx_do_not_share_a_scale_factor():
    # The reason `scale` is per-asset per-venue rather than a constant.
    assert venue_map.listing("NDX", "aster").scale != venue_map.listing("SPX", "aster").scale


def test_an_asset_a_venue_does_not_list_resolves_to_none_not_a_fallback():
    # Lighter carries neither. Falling back to the canonical symbol is exactly how an
    # order lands on the wrong instrument.
    assert venue_map.listing("NFLX", "lighter") is None
    assert venue_map.listing("RIVN", "lighter") is None
    assert venue_map.listing("MNT", "aster") is None


def test_an_unknown_asset_resolves_to_none():
    assert venue_map.listing("DOGECOIN_TO_THE_MOON", "aster") is None
    assert venue_map.venues_for("DOGECOIN_TO_THE_MOON") == []


def test_equities_route_to_hip3_never_to_the_hyperliquid_core_book():
    # The core book has no equities at all; an unnamespaced equity symbol would be wrong.
    for asset in ("NVDA", "TSLA", "GOOGL", "META", "MU", "COIN", "CRCL", "MSTR", "HOOD"):
        assert venue_map.listing(asset, "hyperliquid").symbol.startswith("xyz:")


def test_scale_is_not_mistaken_for_a_venue():
    assert "scale" not in venue_map.venues_for("SPX")


def test_assets_no_venue_lists_are_recorded_rather_than_absent():
    # Recorded so the gap is not rediscovered; both need a real broker.
    assert venue_map.unlisted() == ["GLXY", "ILMN"]
    assert venue_map.venues_for("GLXY") == []


def test_reverse_lookup_recovers_the_canonical_symbol():
    assert venue_map.canonical_for("lighter", "US500") == "SPX"
    assert venue_map.canonical_for("aster", "XAUUSDT") == "GOLD"
    assert venue_map.canonical_for("lighter", "NOT_A_SYMBOL") is None


def test_oil_maps_to_wti_not_brent_on_every_venue():
    # Both are listed on Hyperliquid and Lighter; the corpus means WTI when it says oil.
    assert venue_map.listing("OIL", "hyperliquid").symbol == "xyz:CL"
    assert venue_map.listing("OIL", "lighter").symbol == "WTI"
