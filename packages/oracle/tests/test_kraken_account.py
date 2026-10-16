"""``kraken_account``'s two promises: it can only read, and every Kraken spelling of a coin
lands in one row. The network is never touched."""

from __future__ import annotations

import pytest
from oracle import kraken_account as ka

ASSETS = {
    "XXBT": {"altname": "XBT"},
    "XETH": {"altname": "ETH"},
    "LDO": {"altname": "LDO"},
    "ZUSD": {"altname": "USD"},
    "ZEUR": {"altname": "EUR"},
    "USDC": {"altname": "USDC"},
    "KFEE": {"altname": "FEE"},
    "WAVES": {"altname": "WAVES"},
}
PAIRS = {
    "XXBTZUSD": {"base": "XXBT", "quote": "ZUSD", "wsname": "XBT/USD"},
    "XXBTZUSD.d": {"base": "XXBT", "quote": "ZUSD", "wsname": "XBT/USD"},
    "XETHZUSD": {"base": "XETH", "quote": "ZUSD", "wsname": "ETH/USD"},
    "LDOUSD": {"base": "LDO", "quote": "ZUSD", "wsname": "LDO/USD"},
    "USDCUSD": {"base": "USDC", "quote": "ZUSD", "wsname": "USDC/USD"},
    "ZEURZUSD": {"base": "ZEUR", "quote": "ZUSD", "wsname": "EUR/USD"},
}
PRICES = {"XXBTZUSD": 80000.0, "XETHZUSD": 2400.0, "LDOUSD": 0.5, "USDCUSD": 1.0}


def _read(**balances):
    return ka.Read(
        balances={
            code.replace("_", "."): {"balance": str(q), "hold_trade": "0"}
            for code, q in balances.items()
        },
        assets=ASSETS,
        pairs=PAIRS,
        prices=PRICES,
    )


def test_signing_reproduces_krakens_published_example():
    secret = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="  # gitleaks:allow — Kraken docs example
    postdata = "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25"
    assert ka.sign("/0/private/AddOrder", "1616492376594", postdata, secret) == (
        "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
    )


def test_the_only_private_endpoint_is_the_balance_read():
    assert frozenset({"/0/private/BalanceEx"}) == ka.PRIVATE_PATHS


def test_any_other_private_endpoint_is_refused_before_a_request_is_built(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("a request was attempted")

    monkeypatch.setattr(ka.requests, "post", no_network)
    monkeypatch.setattr(ka, "credentials", no_network)
    with pytest.raises(ka.KrakenError, match="not a read"):
        ka.private("/0/private/AddOrder")


def test_an_error_in_a_200_body_raises_rather_than_reading_as_empty():
    with pytest.raises(ka.KrakenError, match="Rate limit"):
        ka.public(
            "/0/public/Ticker",
            get_json=lambda *a, **k: {
                "error": ["EAPI:Rate limit exceeded"],
                "result": {},
            },
        )


def test_missing_keys_are_their_own_error(monkeypatch):
    monkeypatch.setattr(ka, "load_env", lambda: {})
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    with pytest.raises(ka.MissingCredentials):
        ka.credentials()


def test_earn_variants_fold_into_one_row_spelled_the_way_the_repo_prices_it():
    rows, _, _ = ka.rows_from(_read(XXBT=0.01, XBT_M=0.02, XBT_HOLD=0.005))
    [btc] = rows
    assert (btc.ticker, btc.figi, btc.mark) == ("BTC", "XXBT", 80000.0)
    assert btc.shares == pytest.approx(0.035)
    assert btc.staked == pytest.approx(0.02)


def test_dollars_and_stablecoins_become_cash_not_rows():
    rows, _, cash = ka.rows_from(_read(ZUSD=2000, USDC=100, XETH=1))
    assert [r.ticker for r in rows] == ["ETH"]
    assert cash == pytest.approx(2100)


def test_a_non_usd_balance_raises():
    with pytest.raises(ka.KrakenError, match="EUR"):
        ka.rows_from(_read(ZEUR=50))


def test_an_unknown_suffix_raises_rather_than_being_guessed_at():
    with pytest.raises(ka.KrakenError, match="unknown balance suffix"):
        ka.rows_from(_read(XETH_INK=1))


def test_zero_balances_are_ignored_even_with_an_unknown_suffix():
    rows, skipped, cash = ka.rows_from(_read(XETH_INK=0, XETH=1))
    assert [r.ticker for r in rows] == ["ETH"] and not skipped and cash == 0


def test_fee_credits_are_named_not_held():
    rows, skipped, _ = ka.rows_from(_read(KFEE=500, XETH=1))
    assert [r.ticker for r in rows] == ["ETH"]
    assert [(s.what, s.kind) for s in skipped] == [("KFEE", "fee")]


def test_dust_under_the_floor_is_skipped_and_counted():
    rows, skipped, _ = ka.rows_from(_read(LDO=10, XETH=1), min_value=25)
    assert [r.ticker for r in rows] == ["ETH"]
    assert [(s.what, s.kind) for s in skipped] == [("LDO", "dust")]


def test_a_holding_with_no_usd_pair_is_kept_unpriced():
    rows, skipped, _ = ka.rows_from(_read(WAVES=40))
    assert [(r.ticker, r.mark) for r in rows] == [("WAVES", None)] and not skipped
