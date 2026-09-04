from datetime import UTC, datetime

from core.altsignal import AltSignalReading
from oracle.altsignal_config import AltSignalConfig, ChainEntry, MarketEntry
from review import altsignal

AT = datetime(2026, 9, 3, 6, 15, tzinfo=UTC)

CFG = AltSignalConfig(
    chains=(ChainEntry(asset="SOL", chain="solana"),),
    markets=(
        MarketEntry(platform="kalshi", key="KXFED-26DEC-T3.75", why="Fed decision"),
        MarketEntry(platform="polymarket", key="what-price-will-bitcoin-hit-before-2027", why="BTC target"),
    ),
)


class _Reading:
    """Stands in for core.review.Reading — chain_lines only reads identity, not its fields."""


def _defillama(kind, value, key="solana"):
    return AltSignalReading(source="defillama", kind=kind, key=key, value=value, observed_at=AT)


def test_chain_lines_matches_a_holding_to_its_configured_chain():
    stored = [_defillama("chain_tvl", 5_927_196_263.0), _defillama("stablecoin_supply", 16_537_787_080.0)]
    reading = _Reading()
    (line,) = altsignal.chain_lines(
        [reading], ["SOL"], altsignal_cfg=CFG, store_read=lambda **kw: stored
    )
    assert line.reading is reading
    assert any("TVL" in text for text in line.lines)
    assert any("stablecoin" in text.lower() for text in line.lines)


def test_chain_lines_skips_a_holding_with_no_configured_chain():
    lines = altsignal.chain_lines(
        [_Reading()], ["DOGE"], altsignal_cfg=CFG, store_read=lambda **kw: []
    )
    assert lines == ()


def test_chain_lines_skips_a_configured_chain_with_no_stored_data_yet():
    lines = altsignal.chain_lines(
        [_Reading()], ["SOL"], altsignal_cfg=CFG, store_read=lambda **kw: []
    )
    assert lines == ()


def test_macro_block_matches_kalshi_by_exact_key():
    stored = {
        "kalshi": [AltSignalReading(source="kalshi", kind="market", key="KXFED-26DEC-T3.75",
                                     value=0.72, observed_at=AT)],
        "polymarket": [],
    }
    rows = altsignal.macro_block(altsignal_cfg=CFG, store_read=lambda *, source, **kw: stored[source])
    fed_row = next(r for r in rows if r.why == "Fed decision")
    assert fed_row.top == (("KXFED-26DEC-T3.75", 0.72),)
    assert fed_row.others == 0


def test_macro_block_groups_polymarket_by_the_namespaced_event_prefix():
    stored = {
        "kalshi": [],
        "polymarket": [
            AltSignalReading(source="polymarket", kind="market",
                              key="what-price-will-bitcoin-hit-before-2027:btc-hit-150k",
                              value=0.31, observed_at=AT),
            AltSignalReading(source="polymarket", kind="market",
                              key="what-price-will-bitcoin-hit-before-2027:btc-hit-200k",
                              value=0.08, observed_at=AT),
            # A different event's reading must never leak into this row.
            AltSignalReading(source="polymarket", kind="market",
                              key="us-recession-by-end-of-2026:us-recession-by-end-of-2026",
                              value=0.07, observed_at=AT),
        ],
    }
    rows = altsignal.macro_block(altsignal_cfg=CFG, store_read=lambda *, source, **kw: stored[source])
    btc_row = next(r for r in rows if r.why == "BTC target")
    assert dict(btc_row.top) == {
        "what-price-will-bitcoin-hit-before-2027:btc-hit-150k": 0.31,
        "what-price-will-bitcoin-hit-before-2027:btc-hit-200k": 0.08,
    }


def test_macro_block_caps_and_counts_the_rest():
    many = [
        AltSignalReading(source="polymarket", kind="market",
                          key=f"what-price-will-bitcoin-hit-before-2027:strike-{i}",
                          value=i / 10, observed_at=AT)
        for i in range(5)
    ]
    rows = altsignal.macro_block(
        altsignal_cfg=CFG, limit=3,
        store_read=lambda *, source, **kw: many if source == "polymarket" else [],
    )
    btc_row = next(r for r in rows if r.why == "BTC target")
    assert len(btc_row.top) == 3
    assert btc_row.others == 2


def test_macro_block_ranks_by_distance_from_fifty_percent_not_by_raw_value():
    # A "will it dip to $X" strike prices near-certain (0.95+) for an obviously-true low bar —
    # the least informative reading a probability can give. The genuinely contested strike
    # (near 0.5) is the one worth surfacing first.
    near_certain = AltSignalReading(source="polymarket", kind="market",
                                     key="what-price-will-bitcoin-hit-before-2027:dip-to-60k",
                                     value=0.97, observed_at=AT)
    contested = AltSignalReading(source="polymarket", kind="market",
                                  key="what-price-will-bitcoin-hit-before-2027:hit-150k",
                                  value=0.52, observed_at=AT)
    rows = altsignal.macro_block(
        altsignal_cfg=CFG, limit=1,
        store_read=lambda *, source, **kw: [near_certain, contested] if source == "polymarket" else [],
    )
    btc_row = next(r for r in rows if r.why == "BTC target")
    assert btc_row.top[0][0].endswith("hit-150k")


def test_macro_block_skips_a_market_with_no_stored_data_yet():
    rows = altsignal.macro_block(altsignal_cfg=CFG, store_read=lambda *, source, **kw: [])
    assert rows == ()


def test_missing_config_yields_nothing_for_either():
    empty = AltSignalConfig(chains=(), markets=())
    assert altsignal.chain_lines([_Reading()], ["SOL"], altsignal_cfg=empty, store_read=lambda **kw: []) == ()
    assert altsignal.macro_block(altsignal_cfg=empty, store_read=lambda **kw: []) == ()
