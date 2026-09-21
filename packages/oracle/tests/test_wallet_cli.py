"""``wallet-sync``'s own decisions: what it reads from the file, and when it refuses to write.

The network is monkeypatched out. What is worth testing here is the handling of a chain that
half-answered — the one case where being wrong silently changes a position size.
"""

from __future__ import annotations

import pytest
from oracle import portfolios, stake_solana, wallet, wallet_cli, zerion


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "DATA_ROOT", tmp_path)
    return tmp_path


EVM = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


def _file(root, name, body):
    (root / f"{name}.yaml").write_text(body, encoding="utf-8")


def _token(network, contract, symbol, units, price="100"):
    return {
        "network": network,
        "tokenAddress": contract,
        "tokenBalance": hex(int(units * 10**18)),
        "tokenMetadata": {"symbol": symbol, "decimals": 18},
        "tokenPrices": [{"currency": "usd", "value": price}],
    }


def test_a_flaky_chain_is_retried_and_its_partial_rows_are_not_counted_twice(
    monkeypatch, _root
):
    """The probe saw Polygon return `Internal server error` *alongside* good rows. Retrying the
    chain without discarding what the partial read already gave back would append a complete
    re-read on top of a partial one and double the position."""
    _file(
        _root,
        "w",
        f"account: w\ndomain: crypto\nwallets:\n  - address: '{EVM}'\n"
        "positions:\n  - {ticker: ETH, shares: 1}\n",
    )

    calls: list[tuple] = []

    def fake_read(address, networks):
        calls.append(tuple(networks))
        if len(networks) > 1:
            # The partial: eth answered, matic answered with half its rows and an error.
            return wallet.Read(
                tokens=(
                    _token("eth-mainnet", "0xe", "AAA", 2.0),
                    _token("matic-mainnet", "0xm", "BBB", 3.0),
                ),
                networks=tuple(networks),
                failed=(("matic-mainnet", "Internal server error"),),
            )
        return wallet.Read(
            tokens=(_token("matic-mainnet", "0xm", "BBB", 5.0),),
            networks=tuple(networks),
        )

    monkeypatch.setattr(wallet, "read", fake_read)
    rows, _, _, failed, _ = wallet_cli._one("w", wallet_cli._wallets("w"), 1.0, "alchemy")

    assert calls[-1] == ("matic-mainnet",)  # the flaky chain was asked again, alone
    assert failed == ()  # and it answered, so the read is complete
    held = {r.ticker: r.shares for r in rows}
    assert held["BBB"] == pytest.approx(5.0)  # the re-read, not 3.0 + 5.0
    assert held["AAA"] == pytest.approx(2.0)  # the chain that worked is untouched


def test_a_chain_that_fails_twice_blocks_the_write(monkeypatch, _root):
    """A chain that did not answer looks exactly like a chain you sold out of. Writing on that
    would delete live positions and report it as movement."""
    _file(
        _root,
        "w",
        "account: w\ndomain: crypto\nwallets:\n  - address: 0xabc\n"
        "positions:\n  - {ticker: ETH, shares: 1}\n",
    )
    monkeypatch.setattr(
        wallet,
        "read",
        lambda a, n: wallet.Read(
            tokens=(), networks=tuple(n), failed=(("matic-mainnet", "still down"),)
        ),
    )

    _, _, _, failed, _ = wallet_cli._one("w", wallet_cli._wallets("w"), 1.0, "alchemy")
    assert failed == (("matic-mainnet", "still down"),)
    assert wallet_cli.sync(["--source", "alchemy", "w"]) == 1


def test_a_bare_address_string_is_accepted_and_gets_the_chains_its_shape_implies(_root):
    """`networks:` is only ever a narrowing, so most files will not want to say."""
    _file(
        _root,
        "w",
        f"account: w\nwallets:\n  - '{EVM}'\n"
        "  - {address: SoLaNaLooKiNgAdDrEsS}\npositions:\n  - {ticker: E, shares: 1}\n",
    )
    evm, sol = wallet_cli._wallets("w")
    assert evm["address"] == EVM
    assert "solana-mainnet" not in evm["networks"]
    assert sol["networks"] == ("solana-mainnet",)


def test_an_unquoted_ethereum_address_is_recovered_from_the_integer_yaml_made_of_it(
    _root,
):
    """`0x` plus 40 hex digits is a valid YAML hex literal, so an unquoted address arrives as a
    number with its text gone. Reformatting to 40 places restores it exactly, which is why this
    recovers instead of refusing — and why the leading-zero address is the case to check."""
    zeros = "0x000000000000000000000000000000000000dEaD"
    _file(
        _root,
        "w",
        f"account: w\nwallets:\n  - {EVM}\n  - {zeros}\n"
        "positions:\n  - {ticker: E, shares: 1}\n",
    )
    got = [w["address"].lower() for w in wallet_cli._wallets("w")]
    assert got == [EVM.lower(), zeros.lower()]


def test_the_file_can_narrow_the_floor_and_pin_a_ticker(_root):
    _file(
        _root,
        "w",
        "account: w\nmin_value: 250\nprefer:\n  link: 0xREAL\n"
        "wallets:\n  - 0xabc\npositions:\n  - {ticker: E, shares: 1}\n",
    )
    assert wallet_cli._min_value("w") == 250.0
    assert wallet_cli._prefer("w") == {"LINK": "0xREAL"}


def test_only_the_drops_that_cost_you_a_position_are_named(capsys):
    """A real address drops several hundred airdropped tokens per chain. Printing them all
    buries the one line that matters, which fails the same way as printing nothing."""
    wallet_cli._dropped(
        [
            portfolios.Skipped("LINK", "two contracts claim it", kind="collision"),
            *[
                portfolios.Skipped(f"JUNK{i}", "nobody quotes it", kind="unquoted")
                for i in range(50)
            ],
        ],
        everything=False,
    )
    out = capsys.readouterr().out
    assert "LINK" in out
    assert "JUNK0" not in out
    assert "dropped 50 unquoted" in out


SOL_A = "DEs2iLbuF34RpeLaSXyt2s5EGq5Htdaw4und4CXUQNEM"
SOL_B = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"


def _sol_native(units, price="98.7"):
    return {
        "network": "solana-mainnet",
        "tokenAddress": None,
        "tokenBalance": str(int(units * 10**9)),
        "tokenMetadata": {},
        "tokenPrices": [{"currency": "usd", "value": price}],
    }


def test_stake_totals_accumulate_across_two_addresses_into_one_row(monkeypatch, _root):
    """`rows_from` is called once over every wallet's tokens merged, so two Solana addresses
    staking the same ticker must arrive as one combined total, not overwrite each other."""
    _file(
        _root,
        "w",
        f"account: w\ndomain: crypto\nwallets:\n"
        f"  - address: '{SOL_A}'\n  - address: '{SOL_B}'\n"
        "positions:\n  - {ticker: SOL, shares: 1}\n",
    )
    monkeypatch.setattr(
        wallet,
        "read",
        lambda address, networks: wallet.Read(
            tokens=(_sol_native(0.1 if address == SOL_A else 0.2),),
            networks=tuple(networks),
        ),
    )
    monkeypatch.setattr(
        stake_solana,
        "read",
        lambda address: stake_solana.StakeRead(
            accounts=({"account": {"lamports": 1_000_000_000}},)
            if address == SOL_A
            else ({"account": {"lamports": 2_000_000_000}},)
        ),
    )

    rows, _, _, failed, _ = wallet_cli._one("w", wallet_cli._wallets("w"), 0.0, "alchemy")
    assert failed == ()
    assert [r.ticker for r in rows] == ["SOL"]
    assert rows[0].staked == pytest.approx(3.0)
    assert rows[0].shares == pytest.approx(0.1 + 0.2 + 3.0)


def test_a_raising_stake_read_aborts_the_account_and_writes_nothing(monkeypatch, _root):
    """`stake_solana.read` raises the same `wallet.WalletError` a token read does — one failure
    mechanism, caught by the same `except` a chain outage already goes through."""
    _file(
        _root,
        "w",
        f"account: w\ndomain: crypto\nwallets:\n  - address: '{SOL_A}'\n"
        "positions:\n  - {ticker: SOL, shares: 1}\n",
    )
    monkeypatch.setattr(
        wallet,
        "read",
        lambda address, networks: wallet.Read(
            tokens=(_sol_native(0.1),), networks=tuple(networks)
        ),
    )

    def _boom(address):
        raise wallet.WalletError("rate limited")

    monkeypatch.setattr(stake_solana, "read", _boom)

    rows, _, _, _, _ = wallet_cli._one("w", wallet_cli._wallets("w"), 0.0, "alchemy")
    assert rows is None
    assert wallet_cli.sync(["--source", "alchemy", "w"]) == 1


def test_an_unpriced_stake_blocks_the_write(monkeypatch, _root):
    """No native SOL row means no quote to value the staked units with — a failed read, not a
    position that silently writes shrunk."""
    _file(
        _root,
        "w",
        f"account: w\ndomain: crypto\nwallets:\n  - address: '{SOL_A}'\n"
        "positions:\n  - {ticker: SOL, shares: 1}\n",
    )
    monkeypatch.setattr(
        wallet,
        "read",
        lambda address, networks: wallet.Read(tokens=(), networks=tuple(networks)),
    )
    monkeypatch.setattr(
        stake_solana,
        "read",
        lambda address: stake_solana.StakeRead(
            accounts=({"account": {"lamports": 1_000_000_000}},)
        ),
    )

    _, _, _, failed, _ = wallet_cli._one("w", wallet_cli._wallets("w"), 0.0, "alchemy")
    assert any("staked" in why for _, why in failed)
    assert wallet_cli.sync(["--source", "alchemy", "w"]) == 1


# ── source selection: which reader owns an account's sync ──


def test_source_defaults_to_zerion(_root):
    _file(
        _root,
        "w",
        f"account: w\nwallets:\n  - '{EVM}'\npositions:\n  - {{ticker: E, shares: 1}}\n",
    )
    assert wallet_cli._source("w") == "zerion"


def test_source_reads_the_alchemy_override_from_the_file(_root):
    _file(
        _root,
        "w",
        f"account: w\nsource: alchemy\nwallets:\n  - '{EVM}'\n"
        "positions:\n  - {ticker: E, shares: 1}\n",
    )
    assert wallet_cli._source("w") == "alchemy"


def test_an_unrecognized_source_refuses_the_account_rather_than_guessing(_root):
    """The same reasoning as every other refusal in this module: a typo'd `source: alchmey`
    silently syncing from Zerion instead would look identical to a correctly configured file."""
    _file(
        _root,
        "w",
        f"account: w\nsource: alchmey\nwallets:\n  - '{EVM}'\n"
        "positions:\n  - {ticker: E, shares: 1}\n",
    )
    assert wallet_cli._source("w") is None
    assert wallet_cli.sync(["w"]) == 1


def test_one_dispatches_to_zerion_by_default(monkeypatch, _root):
    _file(
        _root,
        "w",
        f"account: w\ndomain: crypto\nwallets:\n  - address: '{EVM}'\n"
        "positions:\n  - {ticker: E, shares: 1}\n",
    )
    monkeypatch.setattr(
        zerion, "read", lambda address: zerion.Read(positions=(), chains=("ethereum",))
    )
    rows, _, _, failed, counted = wallet_cli._one(
        "w", wallet_cli._wallets("w"), 25.0, "zerion"
    )
    assert rows == ()
    assert failed == ()
    assert counted == ("ethereum",)


def _zerion_native(symbol, chain, value, price, quantity, *, position_type="wallet"):
    return {
        "id": f"{chain}-{symbol}",
        "attributes": {
            "position_type": position_type,
            "value": value,
            "price": price,
            "quantity": {"float": quantity},
            "fungible_info": {"symbol": symbol, "implementations": []},
        },
        "relationships": {"chain": {"data": {"id": chain}}},
    }


def test_zerion_source_never_calls_stake_solana(monkeypatch, _root):
    """The double-count `wallet.py` + `stake_solana` would create if both ran for one address:
    Zerion already returns a delegated stake account as `locked` on the same call."""
    _file(
        _root,
        "w",
        f"account: w\ndomain: crypto\nwallets:\n  - address: '{SOL_A}'\n"
        "positions:\n  - {ticker: SOL, shares: 1}\n",
    )

    def boom(address):
        raise AssertionError("stake_solana.read must not run under source=zerion")

    monkeypatch.setattr(stake_solana, "read", boom)
    monkeypatch.setattr(
        zerion,
        "read",
        lambda address: zerion.Read(
            positions=(
                _zerion_native("SOL", "solana", 50.0, 100.0, 0.5),
                _zerion_native(
                    "SOL", "solana", 50.0, 100.0, 0.5, position_type="locked"
                ),
            ),
            chains=("solana",),
        ),
    )

    rows, _, _, _, _ = wallet_cli._one("w", wallet_cli._wallets("w"), 0.0, "zerion")
    assert [r.ticker for r in rows] == ["SOL"]
    assert rows[0].staked == pytest.approx(0.5)


def test_the_file_banner_names_the_source_that_actually_wrote_it(monkeypatch, _root):
    """`write_positions` is called from `sync`, not `_one` — a stale hardcoded `wallet.SOURCE`
    there would stamp a Zerion-synced file `# Synced from Alchemy`."""
    _file(
        _root,
        "w",
        f"account: w\ndomain: crypto\nwallets:\n  - address: '{EVM}'\n"
        "positions:\n  - {ticker: E, shares: 1}\n",
    )
    monkeypatch.setattr(
        zerion,
        "read",
        lambda address: zerion.Read(
            positions=(_zerion_native("AAA", "ethereum", 100.0, 10.0, 10.0),)
        ),
    )
    assert wallet_cli.sync(["w"]) == 0
    text = (_root / "w.yaml").read_text(encoding="utf-8")
    assert "# Synced from Zerion" in text


def test_a_portfolio_with_no_wallets_block_is_not_a_wallet_account(_root):
    """`plaid-sync` and hand-kept files share this directory. Reporting them as failures every
    night is how a nightly's warnings stop being read."""
    _file(_root, "hand", "account: hand\npositions:\n  - {ticker: VTI, shares: 1}\n")
    assert wallet_cli._wallets("hand") == ()
    assert wallet_cli.sync([]) == 0
