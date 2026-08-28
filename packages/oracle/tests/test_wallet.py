"""The pure half of the wallet adapter: what a chain response becomes.

Nothing here touches the network. What is worth testing is the mapping, and specifically the
four traps the live probe found — a native coin the API describes as nothing, zero rows, junk
with no quote, and two tokens claiming one ticker. Each has a test named after the damage it
would do.
"""
from __future__ import annotations

import pytest
from oracle import wallet


def _token(*, network="eth-mainnet", contract="0xabc", symbol="AAA", decimals=18,
           balance=None, price="10", units=None):
    """One row shaped like Alchemy's. ``units`` is the friendly way to say a balance."""
    if balance is None:
        balance = hex(int((units if units is not None else 1) * 10 ** decimals))
    return {
        "network": network,
        "tokenAddress": contract,
        "tokenBalance": balance,
        "tokenMetadata": {"symbol": symbol, "decimals": decimals, "name": symbol},
        "tokenPrices": ([] if price is None else
                        [{"currency": "usd", "value": price, "lastUpdatedAt": "now"}]),
    }


def _read(*tokens, networks=("eth-mainnet",)):
    return wallet.Read(tokens=tuple(tokens), networks=networks)


def _rows(*tokens, min_value=wallet.MIN_VALUE_USD):
    rows, skipped, cash = wallet.rows_from(_read(*tokens), min_value=min_value)
    return rows, skipped, cash


# ── trap 1: the native coin arrives with no metadata at all ──

def test_the_native_coin_survives_having_no_symbol_or_decimals():
    """The live API returns your actual ETH with `tokenAddress: null` and both `symbol` and
    `decimals` null, while every ERC-20 beside it is fully described. Reading the symbol off
    the response would drop the one position you most wanted to see."""
    native = {
        "network": "eth-mainnet",
        "tokenAddress": None,
        "tokenBalance": hex(int(6.65 * 10 ** 18)),
        "tokenMetadata": {"symbol": None, "decimals": None, "name": None, "logo": None},
        "tokenPrices": [{"currency": "usd", "value": "2430.58"}],
    }
    rows, _, _ = _rows(native)
    assert [r.ticker for r in rows] == ["ETH"]
    assert rows[0].shares == pytest.approx(6.65)
    assert rows[0].mark == 2430.58


def test_a_native_coin_on_an_unmapped_chain_is_named_not_guessed():
    native = {"network": "somechain-mainnet", "tokenAddress": None,
              "tokenBalance": hex(10 ** 18), "tokenMetadata": {},
              "tokenPrices": [{"currency": "usd", "value": "1"}]}
    rows, skipped, _ = _rows(native)
    assert rows == ()
    assert "somechain-mainnet native" in skipped[0].what


def test_solana_native_uses_nine_decimals_not_eighteen():
    """A chain's own coin has its own scale. Reading SOL at 18 decimals reports a billionth of
    the real position, which reads as dust and gets dropped by the floor."""
    native = {"network": "solana-mainnet", "tokenAddress": None,
              "tokenBalance": str(3 * 10 ** 9), "tokenMetadata": {},
              "tokenPrices": [{"currency": "usd", "value": "150"}]}
    rows, _, _ = _rows(native)
    assert rows[0].ticker == "SOL"
    assert rows[0].shares == pytest.approx(3.0)


# ── trap 2: a wallet reports every token it ever touched ──

def test_an_emptied_token_is_not_news():
    rows, skipped, _ = _rows(_token(symbol="OLD", balance="0x0"))
    assert rows == ()
    assert skipped == ()        # reported would be worse than dropped; there are hundreds


# ── trap 3: junk has no quote ──

def test_an_unquoted_token_is_dropped_and_named():
    rows, skipped, _ = _rows(_token(symbol="SCAM", price=None, units=1_000_000))
    assert rows == ()
    assert "SCAM" in skipped[0].what
    assert "airdropped" in skipped[0].why


def test_a_position_under_the_floor_says_what_it_was_worth():
    rows, skipped, _ = _rows(_token(symbol="DUST", price="0.001", units=100), min_value=1.0)
    assert rows == ()
    assert "$0.10" in skipped[0].why


# ── trap 4: symbols are not unique, and are actively impersonated ──

def test_the_same_coin_on_two_chains_is_one_position():
    """ETH on mainnet and ETH on Base is one exposure to one chart, exactly as the same fund
    in a Roth and a Traditional IRA is one exposure in `plaid.rows_from`."""
    rows, skipped, _ = _rows(
        _token(network="eth-mainnet", symbol="WETH", price="2400", units=2),
        _token(network="base-mainnet", contract="0xdef", symbol="WETH", price="2401", units=3),
    )
    assert len(rows) == 1
    assert rows[0].shares == pytest.approx(5.0)
    assert skipped == ()


SCAM = ({"contract": "0xreal", "symbol": "LINK", "price": "15", "units": 100},
        {"contract": "0xfake", "symbol": "LINK", "price": "0.00002", "units": 9_000_000_000})


def test_two_tokens_claiming_one_ticker_are_refused_not_guessed_between():
    """Anyone can deploy a token called LINK. Nothing on the chain says which contract is the
    real one, so this drops the ticker rather than sizing a position from a coin flip."""
    rows, skipped, _ = _rows(*(_token(**t) for t in SCAM))
    assert rows == ()
    assert skipped[0].what == "LINK"
    assert "prefer:" in skipped[0].why


def test_the_richest_claimant_is_not_the_real_one():
    """The obvious tie-break, and it loses outright. The scam mints nine billion units at
    $0.00002 — "worth" $180,000 against a real 100 LINK at $1,500. Unit count is worse. This
    test exists because holding-the-most was the first rule written here, and it picked the
    scam."""
    scam_value = 9_000_000_000 * 0.00002
    assert scam_value > 100 * 15
    rows, _, _ = _rows(*(_token(**t) for t in SCAM))
    assert rows == ()


def test_prefer_settles_a_collision_by_contract_and_keeps_the_real_position():
    """A statement about an instrument, which is the only kind of identity claim worth
    trusting here — the same reasoning `cfg/oracle_map.yaml` runs on."""
    rows, skipped, _ = wallet.rows_from(_read(*(_token(**t) for t in SCAM)),
                                        prefer={"LINK": "0xreal"})
    assert [r.ticker for r in rows] == ["LINK"]
    assert rows[0].shares == pytest.approx(100.0)
    assert rows[0].figi == "0xreal"
    assert "0xfake" in skipped[0].what


def test_a_prefer_pointing_at_a_contract_you_do_not_hold_says_so():
    rows, skipped, _ = wallet.rows_from(_read(*(_token(**t) for t in SCAM)),
                                        prefer={"LINK": "0xneither"})
    assert rows == ()
    assert any("you do not hold" in s.why for s in skipped)


# ── stablecoins are money, not a chart ──

def test_a_stablecoin_is_reported_as_cash_not_as_a_position():
    """A dollar has no chart. It would sit in the review forever with no level and no roster
    view — and it is also simply what you could put into a position today."""
    rows, _, cash = _rows(
        _token(symbol="USDC", decimals=6, price="1.0", units=2500),
        _token(symbol="ARB", price="0.5", units=1000),
    )
    assert [r.ticker for r in rows] == ["ARB"]
    assert cash == pytest.approx(2500.0)


def test_a_wallet_holding_no_stablecoin_says_none_not_zero():
    """"We do not know" and "you have nothing to spend" are different facts, and only one of
    them should ever be printed as a number."""
    _, _, cash = _rows(_token(symbol="ARB", price="0.5", units=1000))
    assert cash is None


def test_a_yield_bearing_wrapper_stays_a_position():
    """sUSDe drifts from a dollar on purpose, which makes it a thesis rather than a balance."""
    rows, _, cash = _rows(_token(symbol="SUSDE", price="1.14", units=1000))
    assert [r.ticker for r in rows] == ["SUSDE"]
    assert cash is None


# ── the identity that makes the mark check worth having ──

def test_the_contract_address_and_price_ride_along():
    """Neither is used to fetch anything. They exist so `core.review.mark_disagrees` has a
    second opinion to check our ticker-fetched price against — and on a chain that check is
    sharper than on equities, because ticker collisions are minted daily."""
    rows, _, _ = _rows(_token(contract="0xc02aaa", symbol="WETH", price="2430.58", units=1))
    assert rows[0].figi == "0xc02aaa"
    assert rows[0].mark == 2430.58
    assert rows[0].cost is None          # a chain has no cost basis to report
    assert rows[0].domain == "crypto"


def test_rows_read_back_the_way_the_reader_expects(tmp_path):
    from oracle import portfolios
    rows, _, cash = _rows(
        _token(symbol="USDC", decimals=6, price="1.0", units=250),
        _token(contract="0xc02aaa", symbol="WETH", price="2400", units=1.5),
    )
    portfolios.write_positions(tmp_path / "w.yaml", rows, source=wallet.SOURCE, cash=cash)
    book = portfolios.load("w", root=tmp_path)
    assert [p.holding.ticker for p in book.positions] == ["WETH"]
    assert book.positions[0].domain == "crypto"
    assert book.cash == 250.0


# ── address shape decides which chains to ask about ──

def test_a_hex_address_is_never_asked_about_solana():
    """Asking a Solana network about a hex address is not a narrower query, it is a malformed
    one — and the API answers the whole request with an error rather than the good half."""
    evm = wallet.networks_for("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    assert "solana-mainnet" not in evm
    assert wallet.networks_for("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9") == ("solana-mainnet",)
