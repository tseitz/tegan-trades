"""``coinbase_account``'s promises: it can only read, it refuses a key that could do more, and
a book it writes adds up to what Coinbase says the account holds. The network is never
touched."""

from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from oracle import coinbase_account as cb

NAME = "organizations/org-1/apiKeys/key-1"
UUID = "0f8e3c2a-1b4d-4e6f-8a9b-0c1d2e3f4a5b"
PERMS = {
    "can_view": True,
    "can_trade": False,
    "can_transfer": False,
    "portfolio_uuid": UUID,
    "portfolio_type": "DEFAULT",
}


def _pem(key, fmt=serialization.PrivateFormat.TraditionalOpenSSL) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, fmt, serialization.NoEncryption()
    ).decode()


@pytest.fixture
def ec_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def env(monkeypatch, ec_key):
    monkeypatch.setattr(cb, "load_env", lambda: None)
    monkeypatch.setenv("COINBASE_API_KEY_NAME", NAME)
    monkeypatch.setenv("COINBASE_API_PRIVATE_KEY", _pem(ec_key).replace("\n", "\\n"))
    return ec_key


def _spot(
    asset,
    quantity,
    value,
    *,
    kind=cb.LIQUID,
    basis: str | None = "100",
    platform=cb.CONSUMER,
):
    entry = {
        "asset": asset,
        "account_type": kind,
        "platform": platform,
        "total_balance_crypto": quantity,
        "total_balance_fiat": value,
    }
    if basis is not None:
        entry["cost_basis"] = {"value": basis, "currency": "USD"}
    return entry


def _read(*spot, total=None, **lists):
    total = sum(e["total_balance_fiat"] for e in spot) if total is None else total
    return cb.Read(
        breakdown={
            "portfolio_balances": {
                "total_balance": {"value": str(total), "currency": "USD"}
            },
            "spot_positions": list(spot),
            **lists,
        }
    )


# ─── it can only read ───────────────────────────────────────────────────────────────────


def test_the_only_private_reads_are_permissions_and_the_portfolio_breakdown():
    assert {"/api/v3/brokerage/key_permissions"} == cb.PRIVATE_PATHS
    assert cb.allowed(f"/api/v3/brokerage/portfolios/{UUID}")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v3/brokerage/orders",
        "/api/v3/brokerage/accounts",
        "/api/v3/brokerage/portfolios/not-a-uuid",
        f"/api/v3/brokerage/portfolios/{UUID}/move_funds",
        f"/api/v3/brokerage/portfolios/{UUID}?x=1",
        f"/api/v3/brokerage/portfolios/{UUID}\n",
    ],
)
def test_any_other_path_is_refused_before_a_token_is_signed(monkeypatch, path):
    def no_credentials():
        raise AssertionError("signed anyway")

    monkeypatch.setattr(cb, "credentials", no_credentials)
    with pytest.raises(cb.CoinbaseError, match="not a read"):
        cb.private(path)


def test_the_token_carries_exactly_the_claims_coinbase_checks(ec_key):
    path = f"/api/v3/brokerage/portfolios/{UUID}"
    token = cb.token(path, NAME, ec_key, now=1_000_000)
    header = jwt.get_unverified_header(token)
    claims = jwt.decode(
        token,
        ec_key.public_key(),
        algorithms=["ES256"],
        options={"verify_nbf": False, "verify_exp": False},
    )
    assert header["alg"] == "ES256" and header["kid"] == NAME and header["nonce"]
    assert claims == {
        "sub": NAME,
        "iss": "cdp",
        "nbf": 1_000_000,
        "exp": 1_000_000 + cb.TOKEN_SECONDS,
        "uri": f"GET api.coinbase.com{path}",
    }


def test_a_non_200_answer_raises_rather_than_reading_as_empty(env, monkeypatch):
    class Redirect:
        status_code = 302
        text = ""

    sent = {}

    def get(url, **kwargs):
        sent.update(kwargs)
        return Redirect()

    monkeypatch.setattr(cb.requests, "get", get)
    with pytest.raises(cb.CoinbaseError, match="HTTP 302"):
        cb.private("/api/v3/brokerage/key_permissions")
    assert sent["allow_redirects"] is False


# ─── the key ────────────────────────────────────────────────────────────────────────────


def test_an_escaped_pem_from_env_loads(env):
    name, key = cb.credentials()
    assert name == NAME and isinstance(key, ec.EllipticCurvePrivateKey)


def test_a_pkcs8_ec_key_loads_too(env, monkeypatch, ec_key):
    monkeypatch.setenv(
        "COINBASE_API_PRIVATE_KEY", _pem(ec_key, serialization.PrivateFormat.PKCS8)
    )
    assert isinstance(cb.credentials()[1], ec.EllipticCurvePrivateKey)


@pytest.mark.parametrize(
    "key",
    [ed25519.Ed25519PrivateKey.generate(), ec.generate_private_key(ec.SECP384R1())],
    ids=["ed25519", "p384"],
)
def test_a_key_es256_cannot_sign_with_raises(env, monkeypatch, key):
    monkeypatch.setenv(
        "COINBASE_API_PRIVATE_KEY", _pem(key, serialization.PrivateFormat.PKCS8)
    )
    with pytest.raises(cb.CoinbaseError, match="ECDSA P-256"):
        cb.credentials()


def test_missing_keys_are_their_own_error(env, monkeypatch):
    monkeypatch.delenv("COINBASE_API_PRIVATE_KEY")
    with pytest.raises(cb.MissingCredentials):
        cb.credentials()


# ─── the permission gate fails closed ───────────────────────────────────────────────────


def test_a_view_only_key_passes_and_names_its_portfolio():
    assert cb.check_permissions(PERMS) == UUID


@pytest.mark.parametrize(
    "change",
    [
        {"can_trade": True},
        {"can_transfer": True},
        {"can_trade": None},
        {"can_view": False},
        {"can_withdraw_everything": True},
        {"portfolio_uuid": "../orders"},
    ],
)
def test_anything_but_view_only_refuses(change):
    with pytest.raises(cb.CoinbaseError):
        cb.check_permissions({**PERMS, **change})


@pytest.mark.parametrize("field", ["can_trade", "can_transfer"])
def test_a_missing_permission_field_refuses(field):
    perms = dict(PERMS)
    del perms[field]
    with pytest.raises(cb.CoinbaseError, match=field):
        cb.check_permissions(perms)


# ─── rows ───────────────────────────────────────────────────────────────────────────────


def test_staked_and_liquid_entries_fold_into_one_row():
    (row,), _, _ = cb.rows_from(
        _read(
            _spot("SOL", 2.0, 300.0, basis="200"),
            _spot("SOL", 1.0, 150.0, kind=cb.STAKED, basis="100"),
        )
    )
    assert (row.ticker, row.shares, row.staked) == ("SOL", 3.0, 1.0)
    assert row.mark == pytest.approx(150.0)
    assert row.cost == pytest.approx(100.0)


def test_one_entry_without_a_basis_leaves_the_ticker_without_a_cost():
    (row,), _, _ = cb.rows_from(
        _read(
            _spot("SOL", 2.0, 300.0),
            _spot("SOL", 1.0, 150.0, kind=cb.STAKED, basis=None),
        )
    )
    assert row.cost is None


def test_dollars_and_stablecoins_become_cash_not_rows():
    rows, _, cash = cb.rows_from(
        _read(
            _spot("USD", 40.0, 40.0, kind=cb.FIAT),
            _spot("USDC", 60.0, 60.0),
            _spot("USDS", 5.0, 5.0),
            _spot("BTC", 0.01, 800.0),
        )
    )
    assert [r.ticker for r in rows] == ["BTC"]
    assert cash == pytest.approx(105.0)


def test_coinbases_old_celo_code_is_renamed():
    (row,), _, _ = cb.rows_from(_read(_spot("CGLD", 100.0, 50.0)))
    assert row.ticker == "CELO" and row.figi == "CGLD"


def test_dust_under_the_floor_is_skipped_but_still_reconciled():
    rows, skipped, _ = cb.rows_from(
        _read(_spot("BTC", 0.01, 800.0), _spot("AMP", 10.0, 3.0)), min_value=25
    )
    assert [r.ticker for r in rows] == ["BTC"]
    assert [(s.what, s.kind) for s in skipped] == [("AMP", "dust")]


def test_zero_entries_are_ignored_even_with_an_unknown_type():
    rows, _, _ = cb.rows_from(
        _read(
            _spot("BTC", 0.01, 800.0), _spot("XYZ", 0.0, 0.0, kind="ACCOUNT_TYPE_NEW")
        )
    )
    assert [r.ticker for r in rows] == ["BTC"]


@pytest.mark.parametrize(
    "entry",
    [
        _spot("ETH", 1.0, 2000.0, kind="ACCOUNT_TYPE_LENT_FUNDS"),
        _spot("ETH", 1.0, 2000.0, platform="PLATFORM_INTX"),
        _spot("EUR", 100.0, 110.0, kind=cb.FIAT),
        _spot("EUR", 100.0, 110.0),
    ],
    ids=["lent", "intx", "eur-fiat", "eur-as-crypto"],
)
def test_a_holding_with_no_row_shape_raises(entry):
    with pytest.raises(cb.CoinbaseError):
        cb.rows_from(_read(_spot("BTC", 0.01, 800.0), entry))


@pytest.mark.parametrize("key", cb.UNMAPPED_LISTS)
def test_equities_futures_and_predictions_raise_rather_than_vanish(key):
    with pytest.raises(cb.CoinbaseError, match=key):
        cb.rows_from(_read(_spot("BTC", 0.01, 800.0), **{key: [{"anything": 1}]}))


def test_rows_that_do_not_add_up_to_coinbases_total_raise():
    with pytest.raises(cb.CoinbaseError, match="not being read"):
        cb.rows_from(_read(_spot("BTC", 0.01, 800.0), total=1200.0))


def test_a_rounding_gap_inside_the_tolerance_passes():
    rows, _, _ = cb.rows_from(_read(_spot("BTC", 0.01, 800.0), total=800.5))
    assert [r.ticker for r in rows] == ["BTC"]


@pytest.mark.parametrize("bad", ["NaN", "inf"])
def test_a_non_finite_total_refuses_rather_than_reconciling(bad):
    with pytest.raises(cb.CoinbaseError, match="unreadable"):
        cb.rows_from(_read(_spot("BTC", 0.01, 800.0), total=bad))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_entry_value_refuses(bad):
    with pytest.raises(cb.CoinbaseError, match="unreadable"):
        cb.rows_from(_read(_spot("BTC", 0.01, bad), total=800.0))
