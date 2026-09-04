from oracle import altsignal_config


def test_missing_file_is_empty(tmp_path):
    cfg = altsignal_config.load(tmp_path)
    assert cfg.chains == ()
    assert cfg.markets == ()


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
