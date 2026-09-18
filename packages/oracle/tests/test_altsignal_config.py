from oracle import altsignal_config


def test_missing_file_is_empty(tmp_path):
    cfg = altsignal_config.load(tmp_path)
    assert cfg.chains == ()
    assert cfg.markets == ()
    assert cfg.protocols == ()


def test_loads_chains_and_markets(tmp_path):
    (tmp_path / "altsignal.yaml").write_text(
        """
chains:
  - asset: SOL
    chain: solana
markets:
  - platform: kalshi
    ticker: "KXFED-26DEC-T3.75"
    why: "Fed decision"
  - platform: polymarket
    slug: "us-recession-by-end-of-2026"
    why: "Recession odds"
""",
        encoding="utf-8",
    )
    cfg = altsignal_config.load(tmp_path)
    assert cfg.chains == (altsignal_config.ChainEntry(asset="SOL", chain="solana"),)
    assert cfg.markets == (
        altsignal_config.MarketEntry(platform="kalshi", key="KXFED-26DEC-T3.75", why="Fed decision"),
        altsignal_config.MarketEntry(
            platform="polymarket", key="us-recession-by-end-of-2026", why="Recession odds"
        ),
    )


def test_empty_file_is_empty(tmp_path):
    (tmp_path / "altsignal.yaml").write_text("", encoding="utf-8")
    cfg = altsignal_config.load(tmp_path)
    assert cfg.chains == ()
    assert cfg.markets == ()
    assert cfg.protocols == ()


def test_loads_protocols_with_list_valued_identifiers(tmp_path):
    (tmp_path / "altsignal.yaml").write_text(
        """
protocols:
  - asset: LIT
    llama_fees: lighter
    llama_tvl: lighter
    llama_oi: [lighter-perps, lighter-robinhood-perps]
    coingecko: lighter
    coingecko_derivatives: [lighter, robinhood-chain-lighter-futures]
    venue: lighter
""",
        encoding="utf-8",
    )
    cfg = altsignal_config.load(tmp_path)
    assert cfg.protocols == (
        altsignal_config.ProtocolEntry(
            asset="LIT",
            llama_fees="lighter",
            llama_tvl="lighter",
            llama_oi=("lighter-perps", "lighter-robinhood-perps"),
            coingecko="lighter",
            coingecko_derivatives=("lighter", "robinhood-chain-lighter-futures"),
            venue="lighter",
        ),
    )


def test_a_file_with_no_protocols_block_yields_empty(tmp_path):
    (tmp_path / "altsignal.yaml").write_text(
        "chains:\n  - asset: SOL\n    chain: solana\n", encoding="utf-8"
    )
    cfg = altsignal_config.load(tmp_path)
    assert cfg.protocols == ()


def test_loads_venues_with_a_list_valued_llama_pools(tmp_path):
    (tmp_path / "altsignal.yaml").write_text(
        """
venues:
  - venue: aave-v3
    llama_protocol: aave-v3
    llama_pools: [aa70268e-4b52-42bf-a116-608b370f9501, 6f00d46b-8735-49ae-9ced-2a0fccc56ad0]
    asset: USDC
    chain: Ethereum
""",
        encoding="utf-8",
    )
    cfg = altsignal_config.load(tmp_path)
    assert cfg.venues == (
        altsignal_config.VenueEntry(
            venue="aave-v3",
            llama_protocol="aave-v3",
            llama_pools=(
                "aa70268e-4b52-42bf-a116-608b370f9501",
                "6f00d46b-8735-49ae-9ced-2a0fccc56ad0",
            ),
            asset="USDC",
            chain="Ethereum",
        ),
    )


def test_a_file_with_no_venues_block_yields_empty(tmp_path):
    (tmp_path / "altsignal.yaml").write_text(
        "chains:\n  - asset: SOL\n    chain: solana\n", encoding="utf-8"
    )
    cfg = altsignal_config.load(tmp_path)
    assert cfg.venues == ()
