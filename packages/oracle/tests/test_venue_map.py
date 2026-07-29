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
    # Recorded so the gap is not rediscovered; each needs a real broker. Checked against all
    # seven live venue universes by scripts/probe_venue_coverage.py, not against a memory.
    assert venue_map.unlisted() == ["CHINA", "DXY", "GLXY", "ILMN", "INTL", "SBSW", "VRT"]
    assert venue_map.venues_for("GLXY") == []


def test_reverse_lookup_recovers_the_canonical_symbol():
    assert venue_map.canonical_for("lighter", "US500") == "SPX"
    assert venue_map.canonical_for("aster", "XAUUSDT") == "GOLD"
    assert venue_map.canonical_for("lighter", "NOT_A_SYMBOL") is None


def test_oil_maps_to_wti_not_brent_on_every_venue():
    # Both are listed on Hyperliquid and Lighter; the corpus means WTI when it says oil.
    assert venue_map.listing("OIL", "hyperliquid").symbol == "xyz:CL"
    assert venue_map.listing("OIL", "lighter").symbol == "WTI"


# ── invariants the price probe found broken in the curated file ───────────────────────────


def test_every_etf_proxy_for_an_index_declares_a_scale():
    # RUT carried IWM with no `scale`, so `is_proxy` was False and `guards.check_listing` would
    # have let an order quoted on the index (2953) go out against an instrument at a tenth of
    # it. Any index whose venue instrument is a fund must say so.
    for asset in ("SPX", "NDX", "RUT"):
        proxies = [
            listing
            for venue in venue_map.venues_for(asset)
            if (listing := venue_map.listing(asset, venue)) is not None
            and listing.symbol.split(":")[-1] not in {"SP500", "US500", "XYZ100", "US100"}
        ]
        assert proxies, f"{asset} has no venue listings to check"
        assert all(p.is_proxy for p in proxies), f"{asset} has an undeclared proxy"


def test_one_market_can_be_a_proxy_for_one_asset_and_one_to_one_for_another():
    # Aster's SPYUSDT is the same book under both entries. Scale belongs to the pairing, not
    # to the market: quoted on the index it is 1/10; quoted on the ETF it is itself.
    assert venue_map.listing("SPX", "aster").symbol == venue_map.listing("SPY", "aster").symbol
    assert venue_map.listing("SPX", "aster").is_proxy
    assert not venue_map.listing("SPY", "aster").is_proxy


def test_uranium_names_one_fund_across_its_venues():
    # It used to carry URA on Lighter and URNM on the other two — different funds trading ~25%
    # apart, not a scale, so every level on two of three venues was a quarter wrong.
    symbols = {
        venue_map.listing("URANIUM", venue).symbol.split(":")[-1]
        for venue in venue_map.venues_for("URANIUM")
    }
    assert symbols == {"URA"}


def test_a_ticker_shared_with_a_token_is_mapped_only_where_it_is_the_equity():
    # Aster's BBUSDT marks 0.016 and is a token; BlackBerry is the 8.03 on the other two.
    assert venue_map.listing("BB", "aster") is None
    assert venue_map.listing("BB", "hyperliquid").symbol == "xyz:BB"


def test_no_listing_names_a_hip3_builder_whose_collateral_is_unverified():
    # execution/broker.py assumes USDC backs every perp it trades — true of the core book and
    # `xyz`, unverified elsewhere. A market on another builder is sized against a balance that
    # may not be collateral, so the map must not reach one however good the price match is.
    for asset in venue_map.load():
        listing = venue_map.listing(asset, "hyperliquid")
        if listing is not None and ":" in listing.symbol:
            assert listing.symbol.startswith("xyz:"), f"{asset} maps to an unverified builder"
