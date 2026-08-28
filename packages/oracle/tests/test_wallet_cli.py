"""``wallet-sync``'s own decisions: what it reads from the file, and when it refuses to write.

The network is monkeypatched out. What is worth testing here is the handling of a chain that
half-answered — the one case where being wrong silently changes a position size.
"""
from __future__ import annotations

import pytest
from oracle import portfolios, wallet, wallet_cli


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "DATA_ROOT", tmp_path)
    return tmp_path


EVM = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


def _file(root, name, body):
    (root / f"{name}.yaml").write_text(body, encoding="utf-8")


def _token(network, contract, symbol, units, price="100"):
    return {"network": network, "tokenAddress": contract,
            "tokenBalance": hex(int(units * 10 ** 18)),
            "tokenMetadata": {"symbol": symbol, "decimals": 18},
            "tokenPrices": [{"currency": "usd", "value": price}]}


def test_a_flaky_chain_is_retried_and_its_partial_rows_are_not_counted_twice(monkeypatch, _root):
    """The probe saw Polygon return `Internal server error` *alongside* good rows. Retrying the
    chain without discarding what the partial read already gave back would append a complete
    re-read on top of a partial one and double the position."""
    _file(_root, "w", f"account: w\ndomain: crypto\nwallets:\n  - address: '{EVM}'\n"
                      "positions:\n  - {ticker: ETH, shares: 1}\n")

    calls: list[tuple] = []

    def fake_read(address, networks):
        calls.append(tuple(networks))
        if len(networks) > 1:
            # The partial: eth answered, matic answered with half its rows and an error.
            return wallet.Read(
                tokens=(_token("eth-mainnet", "0xe", "AAA", 2.0),
                        _token("matic-mainnet", "0xm", "BBB", 3.0)),
                networks=tuple(networks),
                failed=(("matic-mainnet", "Internal server error"),))
        return wallet.Read(tokens=(_token("matic-mainnet", "0xm", "BBB", 5.0),),
                           networks=tuple(networks))

    monkeypatch.setattr(wallet, "read", fake_read)
    rows, _, _, failed, _ = wallet_cli._one("w", wallet_cli._wallets("w"), 1.0)

    assert calls[-1] == ("matic-mainnet",)          # the flaky chain was asked again, alone
    assert failed == ()                             # and it answered, so the read is complete
    held = {r.ticker: r.shares for r in rows}
    assert held["BBB"] == pytest.approx(5.0)        # the re-read, not 3.0 + 5.0
    assert held["AAA"] == pytest.approx(2.0)        # the chain that worked is untouched


def test_a_chain_that_fails_twice_blocks_the_write(monkeypatch, _root):
    """A chain that did not answer looks exactly like a chain you sold out of. Writing on that
    would delete live positions and report it as movement."""
    _file(_root, "w", "account: w\ndomain: crypto\nwallets:\n  - address: 0xabc\n"
                      "positions:\n  - {ticker: ETH, shares: 1}\n")
    monkeypatch.setattr(wallet, "read", lambda a, n: wallet.Read(
        tokens=(), networks=tuple(n), failed=(("matic-mainnet", "still down"),)))

    _, _, _, failed, _ = wallet_cli._one("w", wallet_cli._wallets("w"), 1.0)
    assert failed == (("matic-mainnet", "still down"),)
    assert wallet_cli.sync(["w"]) == 1


def test_a_bare_address_string_is_accepted_and_gets_the_chains_its_shape_implies(_root):
    """`networks:` is only ever a narrowing, so most files will not want to say."""
    _file(_root, "w", f"account: w\nwallets:\n  - '{EVM}'\n"
                      "  - {address: SoLaNaLooKiNgAdDrEsS}\npositions:\n  - {ticker: E, shares: 1}\n")
    evm, sol = wallet_cli._wallets("w")
    assert evm["address"] == EVM
    assert "solana-mainnet" not in evm["networks"]
    assert sol["networks"] == ("solana-mainnet",)


def test_an_unquoted_ethereum_address_is_recovered_from_the_integer_yaml_made_of_it(_root):
    """`0x` plus 40 hex digits is a valid YAML hex literal, so an unquoted address arrives as a
    number with its text gone. Reformatting to 40 places restores it exactly, which is why this
    recovers instead of refusing — and why the leading-zero address is the case to check."""
    zeros = "0x000000000000000000000000000000000000dEaD"
    _file(_root, "w", f"account: w\nwallets:\n  - {EVM}\n  - {zeros}\n"
                      "positions:\n  - {ticker: E, shares: 1}\n")
    got = [w["address"].lower() for w in wallet_cli._wallets("w")]
    assert got == [EVM.lower(), zeros.lower()]


def test_the_file_can_narrow_the_floor_and_pin_a_ticker(_root):
    _file(_root, "w", "account: w\nmin_value: 250\nprefer:\n  link: 0xREAL\n"
                      "wallets:\n  - 0xabc\npositions:\n  - {ticker: E, shares: 1}\n")
    assert wallet_cli._min_value("w") == 250.0
    assert wallet_cli._prefer("w") == {"LINK": "0xREAL"}


def test_only_the_drops_that_cost_you_a_position_are_named(capsys):
    """A real address drops several hundred airdropped tokens per chain. Printing them all
    buries the one line that matters, which fails the same way as printing nothing."""
    wallet_cli._dropped([
        portfolios.Skipped("LINK", "two contracts claim it", kind="collision"),
        *[portfolios.Skipped(f"JUNK{i}", "nobody quotes it", kind="unquoted") for i in range(50)],
    ], everything=False)
    out = capsys.readouterr().out
    assert "LINK" in out
    assert "JUNK0" not in out
    assert "dropped 50 unquoted" in out


def test_a_portfolio_with_no_wallets_block_is_not_a_wallet_account(_root):
    """`plaid-sync` and hand-kept files share this directory. Reporting them as failures every
    night is how a nightly's warnings stop being read."""
    _file(_root, "hand", "account: hand\npositions:\n  - {ticker: VTI, shares: 1}\n")
    assert wallet_cli._wallets("hand") == ()
    assert wallet_cli.sync([]) == 0
