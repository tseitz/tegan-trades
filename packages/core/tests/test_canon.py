import json
from types import SimpleNamespace

import pytest
from core.canon import (
    BASKET,
    Registry,
    ResolvedThesis,
    load_registry,
    normalize,
    resolve,
    resolve_asset,
    resolve_person,
)

# ── pure resolve: assets ────────────────────────────────────────────────────

def _reg(**kw):
    return Registry(**kw)


def test_normalize_collapses_case_and_whitespace():
    assert normalize("  Bitcoin  ") == "bitcoin"
    assert normalize("Bitcoin (BTC)") == "bitcoin (btc)"
    assert normalize("btc") == normalize("BTC")


def test_resolve_asset_alias_hit_to_canonical_and_rank():
    reg = _reg(assets={"bitcoin": "BTC"}, tickers={"BTC": {"name": "Bitcoin", "market_cap_rank": 1}})
    assert resolve_asset("Bitcoin", reg) == ("BTC", True, 1)


def test_resolve_asset_canonical_passthrough():
    reg = _reg(assets={"btc": "BTC"}, tickers={"BTC": {"market_cap_rank": 1}})
    assert resolve_asset("BTC", reg) == ("BTC", True, 1)


def test_resolve_asset_basket_sentinel_resolves_without_rank():
    reg = _reg(assets={"altcoins (broad)": BASKET})
    assert resolve_asset("Altcoins (broad)", reg) == (BASKET, True, None)


def test_resolve_asset_bare_known_ticker_auto_accepts():
    reg = _reg(tickers={"SOL": {"market_cap_rank": 5}})
    assert resolve_asset("sol", reg) == ("SOL", True, 5)


def test_resolve_asset_curated_non_crypto_is_trusted_without_rank():
    # A curated stock/index has no CoinGecko rank but is still resolved+trusted (not suspect)
    reg = _reg(assets={"tesla (tsla)": "TSLA"}, tickers={})
    assert resolve_asset("Tesla (TSLA)", reg) == ("TSLA", True, None)


def test_resolve_asset_unmapped_is_unresolved():
    reg = _reg()
    assert resolve_asset("some weird theme", reg) == ("some weird theme", False, None)


# ── pure resolve: people ────────────────────────────────────────────────────

def test_resolve_person_alias_hit():
    reg = _reg(people={"the defi report": "The DeFi Report (Michael Nadeau)",
                       "the defi report (michael nadeau)": "The DeFi Report (Michael Nadeau)"})
    assert resolve_person("The DeFi Report", reg) == ("The DeFi Report (Michael Nadeau)", True)


def test_resolve_person_unresolved_passthrough():
    assert resolve_person("Some Guest", _reg()) == ("Some Guest", False)


def test_resolve_composes_person_and_asset():
    reg = _reg(
        people={"cowen": "Benjamin Cowen"},
        assets={"bitcoin": "BTC"},
        tickers={"BTC": {"market_cap_rank": 1}},
    )
    thesis = SimpleNamespace(asset="Bitcoin", source=SimpleNamespace(person="Cowen"))
    out = resolve(thesis, reg)
    assert isinstance(out, ResolvedThesis)
    assert out.person_canonical == "Benjamin Cowen" and out.person_resolved
    assert out.asset_canonical == "BTC" and out.asset_resolved and out.asset_rank == 1


# ── load_registry (I/O) ─────────────────────────────────────────────────────

def _write(p, text):
    p.write_text(text, encoding="utf-8")


def test_load_registry_reads_all_three_sources(tmp_path):
    _write(tmp_path / "watchlist.yaml", (
        "people:\n"
        "  - name: \"The DeFi Report (Michael Nadeau)\"\n"
        "    aliases: [\"The DeFi Report\"]\n"
        "  - name: \"Technical Roundup (CryptoCred + DonAlt)\"\n"
        "    members: [CryptoCred, DonAlt]\n"
    ))
    _write(tmp_path / "assets.yaml", (
        "BTC: [Bitcoin, \"Bitcoin (BTC)\"]\n"
        "__basket__: [\"Altcoins (broad)\"]\n"
    ))
    _write(tmp_path / "tickers.json", json.dumps({"BTC": {"name": "Bitcoin", "market_cap_rank": 1}}))

    reg = load_registry(tmp_path)
    # person alias + canonical self-map
    assert reg.people["the defi report"] == "The DeFi Report (Michael Nadeau)"
    assert reg.people["the defi report (michael nadeau)"] == "The DeFi Report (Michael Nadeau)"
    assert reg.members["Technical Roundup (CryptoCred + DonAlt)"] == ["CryptoCred", "DonAlt"]
    # asset alias + canonical self-map + basket
    assert reg.assets["bitcoin"] == "BTC"
    assert reg.assets["btc"] == "BTC"
    assert reg.assets["altcoins (broad)"] == BASKET
    assert reg.tickers["BTC"]["market_cap_rank"] == 1


def test_load_registry_tolerates_missing_optional_files(tmp_path):
    # assets.yaml / tickers.json don't exist yet (before first seed) — must not crash
    _write(tmp_path / "watchlist.yaml", "people:\n  - name: TraderMayne\n")
    reg = load_registry(tmp_path)
    assert reg.people["tradermayne"] == "TraderMayne"
    assert reg.assets == {}
    assert reg.tickers == {}


# ── the committed registry ──────────────────────────────────────────────────

@pytest.mark.parametrize("alias,canonical", [
    ("XAG", "SILVER"),   # ISO 4217 code for a troy ounce of silver, not a derivative of it
    ("XAU", "GOLD"),
    ("GC", "GOLD"),      # COMEX gold future; TTrades speaks in futures tickers
    ("CL", "OIL"),       # crude future — verified against all 9 rows, none are Colgate
    ("RTY", "RUT"),
    ("N225", "NKY"),
])
def test_ticker_spellings_fold_into_one_asset(alias, canonical):
    """These each routed to the same instrument under a second key, so the queue offered
    one trade twice — `SILVER LONG` and `XAG LONG` were rows 2 and 6 of one sitting, identical
    in every number. Folding them here rather than in `oracle_map.yaml` is deliberate: the map
    would dedupe *prices* while leaving the corpus split across two keys, which leaves
    `collapse` grouping and the `agreement` count still divided."""
    from oracle.setups_cli import CONFIG_DIR
    registry = load_registry(CONFIG_DIR)
    assert resolve_asset(alias, registry)[0] == canonical


def test_a_currency_is_not_folded_into_its_pair():
    """The other half of that fix, and the reason it is not a blanket 'merge anything sharing a
    symbol' rule. `EUR` carries a EUR/GBP cross and `GBP` carries British Pound futures, so
    they are genuinely different objects from the dollar pairs and must stay split."""
    from oracle.setups_cli import CONFIG_DIR
    registry = load_registry(CONFIG_DIR)
    assert resolve_asset("EUR", registry)[0] != "EURUSD"
    assert resolve_asset("GBP", registry)[0] != "GBPUSD"
