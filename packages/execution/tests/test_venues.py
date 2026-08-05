"""Two venues, two spellings of "real money", and the places that must know both.

The failure this file exists to prevent is a check written as ``network == "mainnet"``. That
is correct for Hyperliquid and silently wrong for Alpaca, where the irreversible network is
called ``live`` — and being wrong means a real brokerage order treated as a rehearsal.
"""
from __future__ import annotations

import pytest
from execution import config as config_module
from execution import venues
from execution.alpaca_broker import LIVE, PAPER, AlpacaBroker, AlpacaCredentials
from execution.broker import MAINNET, TESTNET
from execution.config import Config
from execution.session import open_broker

# ── the table ───────────────────────────────────────────────────────────────────────────────

def test_every_venue_offers_a_rehearsal_and_a_real_network():
    for venue, networks in venues.NETWORKS.items():
        assert len(networks) == 2, venue


def test_the_default_network_is_always_the_rehearsal():
    """Reaching real money is never something that happens by omission."""
    for venue in venues.NETWORKS:
        assert not venues.is_real_money(venues.default_network(venue))


def test_both_real_money_networks_are_recognised_as_such():
    assert venues.is_real_money(MAINNET)
    assert venues.is_real_money(LIVE)


@pytest.mark.parametrize("network", [TESTNET, PAPER, "", "nonsense"])
def test_nothing_else_is_real_money(network):
    assert not venues.is_real_money(network)


def test_an_unknown_venue_has_no_default_network():
    """Guessing here would resolve an unrecognised venue to somewhere money can move."""
    with pytest.raises(ValueError, match="unknown venue"):
        venues.default_network("ftx")


# ── the typed confirmation covers both ──────────────────────────────────────────────────────

@pytest.mark.parametrize("network", [MAINNET, LIVE])
def test_real_money_requires_a_typed_confirmation(network):
    assert config_module.requires_typed_confirmation(network)


@pytest.mark.parametrize("network", [TESTNET, PAPER])
def test_a_rehearsal_does_not(network):
    assert not config_module.requires_typed_confirmation(network)


# ── config validation is venue-aware ────────────────────────────────────────────────────────

def test_a_venue_named_without_a_network_gets_its_own_rehearsal(tmp_path):
    """Not the other venue's. Inheriting ``testnet`` here would fail with a message about
    networks rather than about the setting that was actually missing."""
    path = tmp_path / "execution.yaml"
    path.write_text("venue: alpaca\n")
    assert config_module.load(path).network == PAPER


def test_a_network_from_the_wrong_venue_is_refused(tmp_path):
    path = tmp_path / "execution.yaml"
    path.write_text("venue: alpaca\nnetwork: mainnet\n")
    with pytest.raises(ValueError, match="network for venue 'alpaca'"):
        config_module.load(path)


def test_an_unknown_venue_is_refused(tmp_path):
    path = tmp_path / "execution.yaml"
    path.write_text("venue: ftx\n")
    with pytest.raises(ValueError, match="venue must be one of"):
        config_module.load(path)


def test_the_hyperliquid_default_is_unchanged(tmp_path):
    """The existing behaviour is load-bearing — this package's whole rehearsal workflow
    assumes an absent config means Hyperliquid testnet."""
    loaded = config_module.load(tmp_path / "absent.yaml")
    assert (loaded.venue, loaded.network) == (venues.HYPERLIQUID, TESTNET)


# ── the liquidity gate does not transfer to equities ────────────────────────────────────────

def test_the_liquidity_gate_is_off_for_equities():
    """An equity has no open interest and the order-entry API publishes no book, so
    ``AlpacaBroker.liquidity`` reports "not measured" — which is a refusal. Enforcing the
    gate here would refuse every equity, every time."""
    assert not Config(venue=venues.ALPACA, network=PAPER).liquidity_enforced


def test_it_stays_on_for_perps_on_mainnet():
    assert Config(venue=venues.HYPERLIQUID, network=MAINNET).liquidity_enforced


def test_an_explicit_setting_still_wins_on_either_venue():
    """The override exists so the gate can be exercised deliberately; it must not be
    unreachable on the venue where the default happens to be off."""
    assert Config(venue=venues.ALPACA, network=PAPER, enforce_liquidity=True).liquidity_enforced


# ── credentials ─────────────────────────────────────────────────────────────────────────────

def test_alpaca_credentials_come_from_the_environment():
    creds = config_module.alpaca_credentials(
        {"ALPACA_API_KEY_ID": "PK1", "ALPACA_API_SECRET_KEY": "s3c"}
    )
    assert (creds.key_id, creds.secret_key) == ("PK1", "s3c")


@pytest.mark.parametrize("env", [
    {},
    {"ALPACA_API_KEY_ID": "PK1"},
    {"ALPACA_API_SECRET_KEY": "s3c"},
    {"ALPACA_API_KEY_ID": "  ", "ALPACA_API_SECRET_KEY": "s3c"},
])
def test_missing_alpaca_credentials_name_what_is_missing(env):
    with pytest.raises(config_module.MissingCredentials, match="ALPACA_API"):
        config_module.alpaca_credentials(env)


def test_credentials_for_dispatches_on_venue():
    creds = config_module.credentials_for(
        venues.ALPACA, {"ALPACA_API_KEY_ID": "PK1", "ALPACA_API_SECRET_KEY": "s3c"}
    )
    assert isinstance(creds, AlpacaCredentials)


def test_credentials_for_refuses_an_unknown_venue():
    with pytest.raises(ValueError, match="unknown venue"):
        config_module.credentials_for("ftx", {})


# ── the broker a config selects ─────────────────────────────────────────────────────────────

def test_an_alpaca_config_opens_an_alpaca_broker():
    broker = open_broker(
        Config(venue=venues.ALPACA, network=PAPER),
        AlpacaCredentials(key_id="PK1", secret_key="s3c"),
    )
    assert isinstance(broker, AlpacaBroker)
    assert broker.network == PAPER


def test_an_unknown_venue_opens_nothing():
    with pytest.raises(ValueError, match="unknown venue"):
        open_broker(Config(venue="ftx"), None)


def test_all_networks_spans_every_venue():
    """The CLI's ``--network`` choices come from here. Built from the table rather than
    written out, because a venue added without its networks appearing in the flag is a venue
    only reachable by editing cfg/execution.yaml by hand."""
    assert {TESTNET, MAINNET, PAPER, LIVE} == venues.ALL_NETWORKS


def test_all_networks_is_the_union_not_the_rehearsals():
    """It has to include the real-money ones — otherwise --network cannot express them and
    the typed confirmation guards a path nobody can reach."""
    assert venues.REAL_MONEY <= venues.ALL_NETWORKS
