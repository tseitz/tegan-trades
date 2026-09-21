"""The pure half of the Zerion adapter: what a positions response becomes.

The network is monkeypatched out via `http.get_json`, mirroring `test_wallet.py`. What is
worth testing here is what makes this source genuinely different from `wallet.py`: chain read
off the relationship rather than the implementations list, staked/locked folding into the
liquid row rather than a second one, the Solana filter branch, and pagination.
"""

from __future__ import annotations

import pytest
from oracle import http, wallet, zerion


def _position(
    *,
    symbol="AAA",
    chain="ethereum",
    contract="0xabc",
    value=100.0,
    price=10.0,
    quantity=None,
    position_type="wallet",
    item_id=None,
    other_chain_contract="0xelsewhere",
):
    """One position shaped like Zerion's JSON:API. ``other_chain_contract`` populates a second
    ``implementations`` entry for an unrelated chain — every real response carries dozens —
    so a test that reads the chain off the wrong field fails loudly instead of by luck."""
    if quantity is None:
        quantity = (value / price) if price else 0.0
    return {
        "id": item_id or f"{contract or chain}-{symbol}",
        "attributes": {
            "position_type": position_type,
            "value": value,
            "price": price,
            "quantity": {"float": quantity},
            "fungible_info": {
                "symbol": symbol,
                "implementations": [
                    {"chain_id": "somewhere-else", "address": other_chain_contract},
                    {"chain_id": chain, "address": contract},
                ],
            },
        },
        "relationships": {"chain": {"data": {"id": chain}}},
    }


def _read(*positions):
    return zerion.Read(positions=tuple(positions))


def _rows(*positions, min_value=wallet.MIN_VALUE_USD, prefer=None):
    return zerion.rows_from(_read(*positions), min_value=min_value, prefer=prefer)


# ── the chain comes off the relationship, never off `implementations` ──


def test_the_contract_is_read_off_the_held_chain_not_the_first_implementation():
    rows, _, _ = _rows(
        _position(symbol="AAA", chain="base", contract="0xheld", value=100.0)
    )
    assert rows[0].figi == "0xheld"


def test_the_native_coins_implementation_has_no_address():
    rows, _, _ = _rows(
        _position(symbol="ETH", chain="ethereum", contract=None, value=100.0)
    )
    assert rows[0].figi is None


# ── staked/locked fold into the same ticker's row, per ADR-0008 ──


def test_a_staked_position_folds_into_the_liquid_row_not_a_second_one():
    rows, _, _ = _rows(
        _position(symbol="AAVE", chain="ethereum", contract="0xaave", value=1000.0, price=100.0),
        _position(
            symbol="AAVE",
            chain="ethereum",
            contract="0xaave",
            value=500.0,
            price=100.0,
            quantity=5.0,
            position_type="staked",
        ),
    )
    assert [r.ticker for r in rows] == ["AAVE"]
    assert rows[0].shares == pytest.approx(10.0 + 5.0)
    assert rows[0].staked == pytest.approx(5.0)


def test_a_staked_position_with_no_liquid_counterpart_still_makes_a_row():
    rows, _, _ = _rows(
        _position(
            symbol="ACX",
            chain="ethereum",
            contract="0xacx",
            value=43.0,
            price=1.0,
            quantity=43.0,
            position_type="staked",
        )
    )
    assert [r.ticker for r in rows] == ["ACX"]
    assert rows[0].shares == pytest.approx(43.0)
    assert rows[0].staked == pytest.approx(43.0)


def test_a_reward_position_is_liquid_not_staked():
    """`reward` is not in `STAKED_TYPES` — an unclaimed reward is a plain balance, not
    exposure held for its own yield."""
    rows, _, _ = _rows(
        _position(symbol="ACX", value=30.0, price=1.0, quantity=30.0, position_type="reward")
    )
    assert rows[0].staked is None


# ── a staked position that just restates a plain wallet balance is dropped, not summed ──


def test_a_staked_position_matching_a_wallet_balance_is_dropped_not_double_counted():
    """The live AAVE/STKAAVE case: Zerion's protocol layer reports `staked in Aave V2` for
    the exact same quantity its wallet layer already reports as the literal stkAAVE ERC-20.
    Folding both in would double the position."""
    rows, skipped, _ = _rows(
        _position(
            symbol="AAVE",
            chain="ethereum",
            contract="0xaave",
            value=31.0,
            price=137.9,
            quantity=0.2249488595230007,
            position_type="staked",
        ),
        _position(
            symbol="STKAAVE",
            chain="ethereum",
            contract="0xstkaave",
            value=31.0,
            price=137.9,
            quantity=0.2249488595230007,
            position_type="wallet",
        ),
    )
    assert [r.ticker for r in rows] == ["STKAAVE"]
    assert rows[0].shares == pytest.approx(0.2249488595230007)
    assert rows[0].staked is None
    assert any(s.kind == "duplicate" for s in skipped)


def test_a_staked_position_on_a_different_chain_is_not_treated_as_a_duplicate():
    """The dedup is scoped per chain — a coincidental quantity match across two unrelated
    chains must not delete a real position."""
    rows, _, _ = _rows(
        _position(
            symbol="ACX",
            chain="ethereum",
            contract="0xacx",
            value=30.0,
            price=3.0,
            quantity=10.0,
            position_type="staked",
        ),
        _position(
            symbol="OTHER",
            chain="base",
            contract="0xother",
            value=100.0,
            price=10.0,
            quantity=10.0,
            position_type="wallet",
        ),
        min_value=25.0,
    )
    assert {r.ticker for r in rows} == {"ACX", "OTHER"}


# ── stablecoins are cash, exactly as in `wallet.py`, including a `deposit` position ──


def test_a_stablecoin_deposit_is_cash_not_a_position():
    """ADR-0008 already rejected routing wallet-synced stablecoins into Treasury — a lending
    deposit gets the same treatment as an idle balance, not a new category."""
    rows, _, cash = _rows(
        _position(symbol="USDC", value=250.0, price=1.0, position_type="deposit"),
        _position(symbol="AAA", value=100.0, price=10.0),
    )
    assert [r.ticker for r in rows] == ["AAA"]
    assert cash == pytest.approx(250.0)


# ── an unpriced position is named, not dropped without a trace ──


def test_a_position_with_no_value_is_skipped_not_silently_lost():
    rows, skipped, _ = _rows(_position(symbol="GWART", value=None, price=0))
    assert rows == ()
    assert skipped[0].kind == "unquoted"


# ── the dust floor applies to the combined value, after folding ──


def test_the_dust_floor_applies_after_staking_is_folded_in():
    rows, skipped, _ = _rows(
        _position(
            symbol="ACX", chain="ethereum", contract="0xacx", value=10.0, price=1.0, quantity=10.0
        ),
        _position(
            symbol="ACX",
            chain="ethereum",
            contract="0xacx",
            value=10.0,
            price=1.0,
            quantity=10.0,
            position_type="staked",
        ),
        min_value=15.0,
    )
    assert [r.ticker for r in rows] == ["ACX"]  # 20 combined clears a 15 floor
    assert not any(s.kind == "dust" and s.what == "ACX" for s in skipped)


# ── collisions refuse rather than guess, reusing `wallet._fold` ──


def test_two_contracts_claiming_one_ticker_at_disagreeing_prices_are_refused():
    rows, skipped, _ = _rows(
        _position(symbol="LINK", chain="ethereum", contract="0xreal", value=1500.0, price=15.0),
        _position(
            symbol="LINK",
            chain="base",
            contract="0xfake",
            value=180000.0,
            price=0.00002,
            quantity=9_000_000_000,
        ),
    )
    assert rows == ()
    assert skipped[-1].kind == "collision"


# ── `read` — the Solana filter branch, and pagination ──


def test_read_uses_no_filter_on_evm_and_only_simple_on_solana(monkeypatch):
    seen: list[dict] = []

    def fake_get_json(url, params=None, headers=None, **_kw):
        seen.append(params)
        return {"data": [], "links": {}}

    monkeypatch.setattr(zerion, "api_key", lambda: "key")
    monkeypatch.setattr(http, "get_json", fake_get_json)

    zerion.read("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    zerion.read("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9")

    assert seen[0]["filter[positions]"] == "no_filter"
    assert seen[1]["filter[positions]"] == "only_simple"


def test_read_follows_a_next_link_rather_than_truncating(monkeypatch):
    pages = [
        {"data": [_position(symbol="A")], "links": {"next": "https://api.zerion.io/v1/page2"}},
        {"data": [_position(symbol="B")], "links": {}},
    ]

    def fake_get_json(url, params=None, headers=None, **_kw):
        return pages.pop(0)

    monkeypatch.setattr(zerion, "api_key", lambda: "key")
    monkeypatch.setattr(http, "get_json", fake_get_json)

    found = zerion.read("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    assert len(found.positions) == 2


def test_read_raises_rather_than_looping_forever_on_a_stuck_cursor(monkeypatch):
    def fake_get_json(url, params=None, headers=None, **_kw):
        return {"data": [], "links": {"next": "https://api.zerion.io/v1/forever"}}

    monkeypatch.setattr(zerion, "api_key", lambda: "key")
    monkeypatch.setattr(http, "get_json", fake_get_json)

    with pytest.raises(wallet.WalletError, match="stopped after"):
        zerion.read("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
