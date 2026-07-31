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
    # Recorded so the gap is not rediscovered. Checked against all seven live venue universes
    # by scripts/probe_venue_coverage.py, not against a memory.
    #
    # This list shrank from seven to one when Alpaca was mapped, and that is the point: five
    # of them were never exotic, just absent from every *derivatives* venue. "Needs a real
    # broker" was the right diagnosis and Alpaca is the broker. What is left is a genuine gap —
    # DXY, whose tracking funds are not 1:1 with it, so it cannot follow DJI and RUT through
    # `tradeable` either. See test_an_asset_no_venue_at_all_carries_stays_empty.
    assert venue_map.unlisted() == ["DXY"]
    assert venue_map.venues_for("DXY") == []


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
    #
    # RUT has since left this list, and NOT by being fixed the way it was fixed the first time.
    # It is priced on IWM for execution (oracle_map `tradeable`), so there is no longer an
    # index price to be a ratio away from — the row is genuinely 1:1 and a `scale` on it would
    # be the lie. SPX and NDX stay because they are still traded at index scale elsewhere.
    for asset in ("SPX", "NDX"):
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


# ── alpaca: the venue mapped by instrument rather than by mark ──────────────────────────────
#
# Alpaca's symbol for an equity is the same ticker our own close is fetched under, so the
# price comparison every other venue is confirmed by would be circular here. The evidence is
# the instrument instead, and these tests hold the boundary that evidence drew.

def test_no_crypto_asset_carries_an_alpaca_listing():
    """The whole hazard in one test. Resolved as bare tickers, a large share of the crypto
    section are real, liquid, entirely wrong US equities — LINK is Interlink Electronics,
    NEAR a bond ETF, DASH is DoorDash, BCH is Banco de Chile, TAO a China real-estate ETF.
    A name-matched alpaca column would have placed real orders on every one of them.
    """
    for asset in ("LINK", "NEAR", "DASH", "BCH", "TAO", "SUI", "LTC", "APT", "AR", "ARB",
                  "BTC", "ETH", "COMP", "AERO", "ALT", "SEI", "XPL", "PUMP", "TRX"):
        assert venue_map.listing(asset, "alpaca") is None, f"{asset} must not map to Alpaca"


def test_an_index_still_traded_at_its_own_scale_has_no_alpaca_listing():
    """Alpaca trades neither indices nor futures. SPX and NDX therefore resolve to nothing
    rather than to a scaled proxy — and they must KEEP resolving to nothing, because both are
    traded at index scale on Hyperliquid and Lighter, so their prices have to stay the index's.
    Giving either one an alpaca row is only safe after it stops being priced on the index."""
    assert venue_map.listing("SPX", "alpaca") is None
    assert venue_map.listing("NDX", "alpaca") is None
    assert venue_map.listing("SPY", "alpaca").symbol == "SPY"
    assert venue_map.listing("QQQ", "alpaca").symbol == "QQQ"
    assert venue_map.listing("IWM", "alpaca").symbol == "IWM"


def test_commodities_and_fx_have_no_alpaca_listing():
    """USO and UNG carry roll decay that compounds over CARRY_HOLD_DAYS, so a flat three weeks
    in crude still loses money in the proxy. That is a different trade, not a scaled one, and
    `scale` cannot express it — so the row is absent rather than approximate."""
    for asset in ("GOLD", "SILVER", "OIL", "NATGAS", "COPPER", "PLATINUM", "PALLADIUM",
                  "EUR", "GBP", "USDCAD", "USDJPY"):
        assert venue_map.listing(asset, "alpaca") is None, f"{asset} must not map to Alpaca"


def test_no_alpaca_listing_is_a_proxy():
    """Every alpaca row is the instrument itself, so none carries a scale. `execution.guards`
    refuses a scaled listing outright, so a proxy here would be a row that can never trade."""
    for asset in venue_map.load():
        listing = venue_map.listing(asset, "alpaca")
        if listing is not None:
            assert not listing.is_proxy, f"{asset} maps to a scaled Alpaca listing"


def test_alpaca_reaches_the_assets_no_derivatives_venue_carried():
    """This is what "these need a real broker" meant. CHINA trades as FXI — the fund our own
    close is already routed to, so 1:1 rather than a proxy."""
    assert venue_map.listing("CHINA", "alpaca").symbol == "FXI"
    for asset in ("GLXY", "ILMN", "INTL", "SBSW", "VRT"):
        assert venue_map.listing(asset, "alpaca").symbol == asset


def test_an_asset_no_venue_at_all_carries_stays_empty():
    """DXY is the remaining genuine gap. It used to be describable as "an index, and nothing
    here trades an index" — no longer true, since DJI and RUT now reach Alpaca as their funds.
    What keeps DXY empty is narrower: no venue lists a dollar-index market, and the ETFs that
    track it (UUP, USDU) are not 1:1 with it, so `tradeable` cannot rescue it the way it
    rescued the Dow. Absence is a real answer and must not be filled in by pattern just
    because the sections around it now have an alpaca column.

    This test used to name VRT, and was wrong. Vertiv is NYSE-listed and sits in Alpaca's own
    universe fetch; the empty row cost a real approval, refused at placement after the
    judgement had been spent. An asset a venue does not list is omitted — but "listed nowhere"
    is a claim, and a test asserting one is only as good as the check behind it."""
    assert venue_map.venues_for("DXY") == []


# ── the rule that lets an index reach Alpaca at all ──────────────────────────

def _curated():
    from oracle.route import load_curated
    return load_curated(venue_map.CFG_PATH.parent)


def test_an_asset_traded_through_a_fund_is_priced_on_that_fund():
    """The invariant that makes DJI's and RUT's alpaca rows safe, and it spans two files:
    the row is 1:1 *because* oracle_map moved execution pricing onto the same instrument.
    Either half alone is a live mispricing — a 1:1 row with index pricing sends 51,885 to a
    $527 fund; index pricing with no row just refuses. Only the pair is correct."""
    for asset, fund in (("DJI", "DIA"), ("RUT", "IWM")):
        listing = venue_map.listing(asset, "alpaca")
        assert listing is not None and listing.symbol == fund
        assert not listing.is_proxy, f"{asset} is priced on {fund}, so nothing is scaled"
        assert _curated()[asset].get("tradeable") == fund, (
            f"{asset} has a 1:1 {fund} row but is not priced on {fund} — "
            "the zone would still be quoted on the index"
        )


def test_an_index_traded_at_its_own_scale_anywhere_keeps_index_pricing():
    """The other direction, and the reason this is a rule rather than a preference. SPX trades
    1:1 as `xyz:SP500` and `US500`. Moving its pricing to SPY to unlock Alpaca would misprice
    the two venues where it is already correct — 425 theses, the corpus's largest asset."""
    for asset, one_to_one in (("SPX", {"xyz:SP500", "US500"}), ("NDX", {"xyz:XYZ100", "US100"})):
        symbols = {
            listing.symbol
            for venue in venue_map.venues_for(asset)
            if (listing := venue_map.listing(asset, venue)) is not None
        }
        assert symbols & one_to_one, f"{asset} no longer trades at index scale — revisit"
        assert _curated()[asset].get("tradeable") is None, (
            f"{asset} is traded at index scale somewhere, so it must stay priced on the index"
        )
