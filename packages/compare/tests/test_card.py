from datetime import UTC, datetime

import pytest
from compare import card
from core.altsignal import AltSignalReading
from oracle.altsignal_config import AltSignalConfig, ProtocolEntry

AT = datetime(2026, 9, 16, 6, 15, tzinfo=UTC)
STALE = datetime(2026, 9, 1, 6, 15, tzinfo=UTC)

HYPE = ProtocolEntry(
    asset="HYPE", llama_fees="hyperliquid", llama_tvl="hyperliquid",
    llama_oi=("hyperliquid-perps",), coingecko="hyperliquid",
    coingecko_derivatives=("hyperliquid",), venue="hyperliquid",
)
LIT = ProtocolEntry(
    asset="LIT", llama_fees="lighter", llama_tvl="lighter",
    llama_oi=("lighter-perps", "lighter-robinhood-perps"), coingecko="lighter",
    coingecko_derivatives=("lighter", "robinhood-chain-lighter-futures"), venue="lighter",
)
FOO = ProtocolEntry(
    asset="FOO", llama_fees="foo", llama_tvl="foo", llama_oi=("foo-perps",),
    coingecko="foo", coingecko_derivatives=("foo",), venue="foo",
)

CFG = AltSignalConfig(chains=(), markets=(), protocols=(HYPE, LIT, FOO))


def _reading(source, kind, key, value, observed_at=AT):
    return AltSignalReading(source=source, kind=kind, key=key, value=value, observed_at=observed_at)


def _full_store():
    """Every reading both `fetch_protocols` and `coingecko.fetch` would write for HYPE and LIT,
    with round numbers chosen so ratio/derived-row math is exact and easy to assert."""
    rows = [
        # HYPE
        _reading("defillama", "protocol_fees_30d", "hyperliquid", 80_000_000),
        _reading("defillama", "protocol_revenue_30d", "hyperliquid", 60_000_000),
        _reading("defillama", "protocol_tvl", "hyperliquid", 6_000_000_000),
        _reading("defillama", "protocol_category", "hyperliquid", "Derivatives"),
        _reading("defillama", "unlock_schedule", "hyperliquid", {
            "unlocked_today": 388_000_000, "unlocked_30d": 400_000, "scheduled_90d": 25_000,
            "tbd_amount": 611_000_000, "documented_end": "2026-09-15T00:00:00+00:00",
        }),
        _reading("coingecko", "market_cap", "hyperliquid", 20_000_000_000),
        _reading("coingecko", "fdv", "hyperliquid", 80_000_000_000),
        _reading("coingecko", "circulating_supply", "hyperliquid", 200_000_000),
        _reading("coingecko", "total_supply", "hyperliquid", 1_000_000_000),
        _reading("coingecko", "open_interest", "hyperliquid", 10_000_000_000),
        _reading("coingecko", "volume_24h", "hyperliquid", 4_000_000_000),
        # LIT -- two OI/volume rows on the coingecko-derivatives side, one TVL/fees identity.
        _reading("defillama", "protocol_fees_30d", "lighter", 6_000_000),
        _reading("defillama", "protocol_revenue_30d", "lighter", 4_000_000),
        _reading("defillama", "protocol_tvl", "lighter", 700_000_000),
        _reading("defillama", "protocol_category", "lighter", "Derivatives"),
        _reading("defillama", "unlock_schedule", "lighter", {
            "unlocked_today": 250_000_000, "unlocked_30d": 0, "scheduled_90d": 0,
            "tbd_amount": 250_000_000, "documented_end": "2029-12-29T00:00:00+00:00",
        }),
        _reading("coingecko", "market_cap", "lighter", 1_000_000_000),
        _reading("coingecko", "fdv", "lighter", 4_000_000_000),
        _reading("coingecko", "circulating_supply", "lighter", 250_000_000),
        _reading("coingecko", "total_supply", "lighter", 1_000_000_000),
        _reading("coingecko", "open_interest", "lighter", 900_000_000),
        _reading("coingecko", "open_interest", "robinhood-chain-lighter-futures", 100_000_000),
        _reading("coingecko", "volume_24h", "lighter", 900_000_000),
        _reading("coingecko", "volume_24h", "robinhood-chain-lighter-futures", 100_000_000),
    ]

    def store_read(*, source, kind=None, key=None, **_kw):
        return [
            r for r in rows
            if r.source == source and (kind is None or r.kind == kind) and (key is None or r.key == key)
        ]

    return store_read


def _row(result, label):
    return next(r for r in result.metrics if r.label == label)


def test_tier_order_matches_the_54_prototype():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    labels = [row.label for row in result.metrics]
    assert labels == [
        "Revenue, 30d", "Open interest", "Market cap", "Unlock schedule",
        "Fees, 30d", "Volume, 24h", "TVL", "Turnover (vol/OI)", "Float share", "FDV",
        "Active users", "Liquidations", "Book depth",
    ]
    tiers = [row.tier for row in result.metrics]
    assert tiers == [1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3]


def test_present_cell_carries_value_and_observed_at():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    cell = _row(result, "Revenue, 30d").left
    assert cell.state == card.PRESENT
    assert cell.value == 60_000_000
    assert cell.observed_at == AT


def test_not_fetched_when_the_store_has_nothing_for_a_configured_metric():
    empty_store = lambda **kw: []  # noqa: E731
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=empty_store)
    assert _row(result, "Revenue, 30d").left.state == card.NOT_FETCHED
    assert _row(result, "Revenue, 30d").right.state == card.NOT_FETCHED


def test_tier_3_rows_are_always_no_free_source_regardless_of_the_store():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    for label in ("Active users", "Liquidations", "Book depth"):
        row = _row(result, label)
        assert row.left.state == card.NO_FREE_SOURCE
        assert row.right.state == card.NO_FREE_SOURCE


def test_tvl_row_is_present_when_categories_agree():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    row = _row(result, "TVL")
    assert row.left.state == card.PRESENT
    assert row.right.state == card.PRESENT


def test_tvl_row_refuses_to_compare_when_categories_differ():
    store_read = _full_store()

    def mismatched_categories(*, source, kind=None, key=None, **kw):
        rows = store_read(source=source, kind=kind, key=key, **kw)
        if kind == "protocol_category" and key == "lighter":
            return [_reading("defillama", "protocol_category", "lighter", "Lending")]
        return rows

    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=mismatched_categories)
    row = _row(result, "TVL")
    assert row.left.state == card.NOT_COMPARABLE
    assert row.right.state == card.NOT_COMPARABLE
    assert row.format(700_000_000) == "$700.00M"  # format still callable, only state changes


def test_turnover_is_derived_from_volume_and_open_interest():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    row = _row(result, "Turnover (vol/OI)")
    assert row.left.value == pytest.approx(4_000_000_000 / 10_000_000_000)
    assert row.right.value == pytest.approx(1_000_000_000 / 1_000_000_000)  # 900M+100M / 900M+100M


def test_float_share_is_derived_from_circulating_and_total_supply():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    row = _row(result, "Float share")
    assert row.left.value == pytest.approx(0.2)
    assert row.right.value == pytest.approx(0.25)


def test_open_interest_sums_every_configured_row_and_never_presums_at_the_source():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    row = _row(result, "Open interest")
    assert row.left.value == 10_000_000_000
    assert row.right.value == 1_000_000_000  # 900M + 100M, both rows seen
    assert "LIT: 2 rows summed" in row.note
    assert "HYPE" not in row.note


def test_unlock_schedule_formats_as_a_sentence_not_a_number():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    row = _row(result, "Unlock schedule")
    text = row.format(row.left.value)
    assert "unlocked/30d" in text
    assert "2026-09-15" in text


def test_mcap_oi_ratio_and_its_caption_are_computed_not_hardcoded():
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    ratio = next(r for r in result.ratios if r.label == "mcap / OI")
    assert ratio.left == pytest.approx(20_000_000_000 / 10_000_000_000)  # 2.0
    assert ratio.right == pytest.approx(1_000_000_000 / 1_000_000_000)   # 1.0
    assert "HYPE" in ratio.caption
    assert "MORE expensive" in ratio.caption


def test_real_risk_caption_reverses_direction_with_the_data():
    hype_pricier = card._real_risk_caption(HYPE, LIT, 2.0, 1.0)
    lit_pricier = card._real_risk_caption(HYPE, LIT, 1.0, 2.0)
    assert "HYPE" in hype_pricier
    assert "LIT" in lit_pricier
    assert hype_pricier != lit_pricier


def test_cheaper_caption_names_the_lower_multiple_side():
    assert "LIT" in card._cheaper_caption(HYPE, LIT, 24.3, 21.6)
    assert "HYPE" in card._cheaper_caption(HYPE, LIT, 21.6, 24.3)


def test_captions_are_empty_when_either_side_has_no_ratio():
    assert card._real_risk_caption(HYPE, LIT, None, 1.0) == ""
    assert card._cheaper_caption(HYPE, LIT, 1.0, None) == ""


def test_compare_for_is_not_hardwired_to_one_pair():
    hype_lit = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=_full_store())
    lit_foo = card.compare_for("LIT", "FOO", altsignal_cfg=CFG, store_read=lambda **kw: [])
    assert hype_lit.left.asset == "HYPE" and hype_lit.right.asset == "LIT"
    assert lit_foo.left.asset == "LIT" and lit_foo.right.asset == "FOO"


def test_unknown_asset_raises_a_named_error():
    with pytest.raises(card.UnknownAssetError):
        card.compare_for("HYPE", "NOPE", altsignal_cfg=CFG, store_read=lambda **kw: [])


def test_freshest_and_oldest_span_every_reading_gathered_for_a_side():
    rows = [
        _reading("defillama", "protocol_fees_30d", "hyperliquid", 1, observed_at=STALE),
        _reading("coingecko", "market_cap", "hyperliquid", 2, observed_at=AT),
    ]
    store_read = lambda **kw: [  # noqa: E731
        r for r in rows
        if r.source == kw.get("source") and (kw.get("key") is None or r.key == kw.get("key"))
        and (kw.get("kind") is None or r.kind == kw.get("kind"))
    ]
    result = card.compare_for("HYPE", "LIT", altsignal_cfg=CFG, store_read=store_read)
    left_freshest, right_freshest = result.freshest
    left_oldest, right_oldest = result.oldest
    assert left_freshest == AT
    assert left_oldest == STALE
    assert right_freshest is None and right_oldest is None
