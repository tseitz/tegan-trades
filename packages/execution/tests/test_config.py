"""Settings from the committed file, credentials from the environment — and never the reverse."""
from __future__ import annotations

import pytest
from execution import config
from execution.broker import MAINNET, TESTNET

# ── defaults ────────────────────────────────────────────────────────────────────────────────

def test_missing_file_yields_the_safe_defaults(tmp_path):
    """Absent config must mean testnet at 1%, not an error and not mainnet."""
    loaded = config.load(tmp_path / "nope.yaml")
    assert loaded.network == TESTNET
    assert loaded.risk_pct == 0.01


def test_default_notional_cap_is_inert_on_observed_zones(tmp_path):
    """3.0 is a measured choice — see ``Config``. Pinned so a future edit is deliberate."""
    assert config.load(tmp_path / "absent.yaml").max_notional_frac == 3.0


# ── reading ─────────────────────────────────────────────────────────────────────────────────

def test_reads_values(tmp_path):
    path = tmp_path / "execution.yaml"
    path.write_text("network: mainnet\nrisk_pct: 0.005\nmax_notional_frac: 2\n")
    loaded = config.load(path)
    assert loaded.network == MAINNET
    assert loaded.risk_pct == 0.005
    assert loaded.max_notional_frac == 2


def test_unrecognised_key_is_an_error(tmp_path):
    """A misspelled setting would otherwise be silently ignored, leaving the account trading
    at a risk level nobody chose."""
    path = tmp_path / "execution.yaml"
    path.write_text("risk_pctt: 0.05\n")
    with pytest.raises(ValueError, match="unrecognised key"):
        config.load(path)


@pytest.mark.parametrize("body, match", [
    ("network: paper\n", "network"),
    ("risk_pct: 1.5\n", "risk_pct"),
    ("risk_pct: 0\n", "risk_pct"),
    ("max_notional_frac: -1\n", "max_notional_frac"),
])
def test_invalid_values_are_refused(tmp_path, body, match):
    path = tmp_path / "execution.yaml"
    path.write_text(body)
    with pytest.raises(ValueError, match=match):
        config.load(path)


# ── credentials ─────────────────────────────────────────────────────────────────────────────

def test_reads_credentials_from_the_environment():
    creds = config.credentials({
        config.ADDRESS_VAR: "0xabc",
        config.SECRET_VAR: "0xdef",
    })
    assert creds.account_address == "0xabc"
    assert creds.secret_key == "0xdef"


@pytest.mark.parametrize("env", [
    {},
    {config.ADDRESS_VAR: "0xabc"},
    {config.SECRET_VAR: "0xdef"},
    {config.ADDRESS_VAR: "  ", config.SECRET_VAR: "0xdef"},
])
def test_missing_credentials_raise_with_both_variable_names(env):
    with pytest.raises(config.MissingCredentials, match="HYPERLIQUID_"):
        config.credentials(env)


def test_credentials_fall_back_to_the_repo_dotenv(tmp_path, monkeypatch):
    """`uv run` and the nightly launchd job inherit no shell exports, so a .env that has to be
    sourced by hand is a .env that is not there when it matters."""
    (tmp_path / ".env").write_text(
        f"{config.ADDRESS_VAR}=0xfromfile\n{config.SECRET_VAR}=0xkeyfromfile\n"
    )
    monkeypatch.setattr("core.env.REPO_ROOT", tmp_path)
    monkeypatch.delenv(config.ADDRESS_VAR, raising=False)
    monkeypatch.delenv(config.SECRET_VAR, raising=False)

    creds = config.credentials()      # no env passed — the real lookup path

    assert creds.account_address == "0xfromfile"
    assert creds.secret_key == "0xkeyfromfile"


def test_an_injected_environment_ignores_the_dotenv(tmp_path, monkeypatch):
    """Otherwise a test's stated environment would be quietly topped up from an untracked file
    on the developer's machine, and `missing credentials` would pass or fail by accident."""
    (tmp_path / ".env").write_text(f"{config.SECRET_VAR}=0xkeyfromfile\n")
    monkeypatch.setattr("core.env.REPO_ROOT", tmp_path)

    with pytest.raises(config.MissingCredentials, match=config.SECRET_VAR):
        config.credentials({config.ADDRESS_VAR: "0xabc"})


def test_credentials_are_never_read_from_the_config_file(tmp_path):
    """cfg/ is committed. A key placed there would survive in history after one commit, so
    the loader must not have a field that could accept one."""
    path = tmp_path / "execution.yaml"
    path.write_text("secret_key: 0xdeadbeef\n")
    with pytest.raises(ValueError, match="unrecognised key"):
        config.load(path)


# ── the mainnet barrier ─────────────────────────────────────────────────────────────────────

def test_mainnet_requires_typed_confirmation():
    assert config.requires_typed_confirmation(MAINNET) is True
    assert config.requires_typed_confirmation(TESTNET) is False


# ── liquidity enforcement follows the network ───────────────────────────────────────────────

def test_liquidity_is_enforced_on_mainnet():
    assert config.Config(network=MAINNET).liquidity_enforced is True


def test_liquidity_is_not_enforced_on_testnet():
    """Testnet books are mock — every HIP-3 market there has no book at all, so enforcing
    would refuse everything while protecting nothing. It stays measured and reported."""
    assert config.Config(network=TESTNET).liquidity_enforced is False


@pytest.mark.parametrize("network", [MAINNET, TESTNET])
def test_explicit_setting_overrides_the_network_default(network):
    assert config.Config(network=network, enforce_liquidity=True).liquidity_enforced is True
    assert config.Config(network=network, enforce_liquidity=False).liquidity_enforced is False


def test_liquidity_floors_are_configurable(tmp_path):
    path = tmp_path / "execution.yaml"
    path.write_text("min_day_volume: 5000000\nmin_open_interest: 2000000\n")
    loaded = config.load(path)
    assert loaded.min_day_volume == 5_000_000
    assert loaded.min_open_interest == 2_000_000
