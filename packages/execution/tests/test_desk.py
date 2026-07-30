"""Several venues at once, and the ways a venue drops out without taking the run with it."""
from __future__ import annotations

import pytest

from execution import desk as desk_module
from execution import venues
from execution.account import Account
from execution.config import Config, MissingCredentials
from execution.desk import (
    REASON_NO_ADAPTER,
    REASON_NO_CREDENTIALS,
    REASON_NOT_CONFIRMED,
    REASON_UNREACHABLE,
    Desk,
    Unroutable,
    config_for,
    network_for,
)


# ── fakes ───────────────────────────────────────────────────────────────────────────────────

class FakeSession:
    """Enough of ``Session`` for the desk to hold: a config, a network, an account read."""

    def __init__(self, config, *, dexs=(), orders_path=None, account=None):
        self.config = config
        self.dexs = dexs
        self.orders_path = orders_path
        self.markets = {"ETH": object()}
        self._account = account
        self.account_reads = 0
        self._committed = 0.0

    @property
    def network(self) -> str:
        return self.config.network

    def read_account(self):
        self.account_reads += 1
        return self._account


class factory:
    """A session factory with per-venue outcomes. ``fails`` maps venue -> exception."""

    def __init__(self, accounts=None, fails=None):
        self.accounts = accounts or {}
        self.fails = fails or {}
        self.opened: list[str] = []

    def __call__(self, *, config, dexs=(), orders_path=None):
        self.opened.append(config.venue)
        if config.venue in self.fails:
            raise self.fails[config.venue]
        return FakeSession(config, dexs=dexs, orders_path=orders_path,
                           account=self.accounts.get(config.venue))


def session(desk: Desk, venue: str):
    """The session, asserted present — so a test that meant to find one fails saying so."""
    found = desk.session_for(venue)
    assert found is not None, f"expected {venue} to be routable"
    return found


def refusal(desk: Desk, venue: str) -> Unroutable:
    found = desk.refusal_for(venue)
    assert found is not None, f"expected {venue} to be refused with a reason"
    return found


# ── network_for: the tier is the fact, the spelling is per venue ─────────────────────────────

def test_the_configured_venue_keeps_the_network_it_was_given():
    config = Config(venue=venues.HYPERLIQUID, network="mainnet")
    assert network_for(config, venues.HYPERLIQUID) == "mainnet"


def test_a_rehearsal_run_reaches_the_other_venue_s_rehearsal():
    config = Config(venue=venues.HYPERLIQUID, network="testnet")
    assert network_for(config, venues.ALPACA) == "paper"


def test_a_real_money_run_reaches_the_other_venue_s_real_money():
    """The load-bearing translation, and the one a string comparison gets wrong in the
    dangerous direction. ``mainnet`` is Hyperliquid's spelling of real money; Alpaca's is
    ``live``. A desk that carried the *word* across would open Alpaca paper for a run the user
    typed a real-money confirmation for — reporting rehearsal fills as though they were real.
    """
    config = Config(venue=venues.HYPERLIQUID, network="mainnet")
    assert network_for(config, venues.ALPACA) == "live"


def test_the_translation_runs_the_other_way_too():
    config = Config(venue=venues.ALPACA, network="live")
    assert network_for(config, venues.HYPERLIQUID) == "mainnet"


def test_an_alpaca_paper_run_does_not_reach_hyperliquid_mainnet():
    config = Config(venue=venues.ALPACA, network="paper")
    assert network_for(config, venues.HYPERLIQUID) == "testnet"


def test_an_unknown_venue_raises_rather_than_guessing_a_network():
    with pytest.raises(ValueError, match="kraken"):
        network_for(Config(), "kraken")


# ── config_for: one config per venue, so the per-venue branches keep working ─────────────────

def test_each_venue_gets_a_config_naming_itself():
    config = Config(venue=venues.HYPERLIQUID, network="testnet")
    other = config_for(config, venues.ALPACA)
    assert (other.venue, other.network) == (venues.ALPACA, "paper")


def test_a_derived_config_preserves_every_risk_setting():
    """``replace`` and not a field-by-field rebuild, for the reason ``execute.open_session``
    records: a rebuild that listed four of the settings let a flag about *where* to trade
    quietly reset *how much* to risk.
    """
    config = Config(venue=venues.HYPERLIQUID, network="testnet", risk_pct=0.004,
                    max_position_frac=0.05, min_budget_fill=0.9, max_participation=0.02)
    other = config_for(config, venues.ALPACA)
    assert other.risk_pct == 0.004
    assert other.max_position_frac == 0.05
    assert other.min_budget_fill == 0.9
    assert other.max_participation == 0.02


def test_a_derived_config_validates():
    # The venue/network pair is the thing being derived, so it is the thing worth checking.
    config_for(Config(venue=venues.ALPACA, network="live"), venues.HYPERLIQUID).validate()


def test_the_alpaca_config_still_turns_the_liquidity_gate_off_on_real_money():
    # ``liquidity_enforced`` branches on venue, which is exactly why a per-venue config is the
    # right carrier: an equity has no open interest, so enforcing would refuse every one.
    config = Config(venue=venues.HYPERLIQUID, network="mainnet")
    assert config.liquidity_enforced is True
    assert config_for(config, venues.ALPACA).liquidity_enforced is False


# ── opening: a venue that cannot be reached drops out alone ──────────────────────────────────

def test_a_session_is_opened_for_each_wanted_venue():
    open_session = factory()
    d = Desk.open(config=Config(), wanted=(venues.HYPERLIQUID, venues.ALPACA),
                  session_factory=open_session)
    assert sorted(d.routable) == [venues.ALPACA, venues.HYPERLIQUID]
    assert sorted(open_session.opened) == [venues.ALPACA, venues.HYPERLIQUID]


def test_each_session_gets_its_own_network():
    d = Desk.open(config=Config(venue=venues.HYPERLIQUID, network="testnet"),
                  wanted=(venues.HYPERLIQUID, venues.ALPACA), session_factory=factory())
    assert session(d, venues.HYPERLIQUID).network == "testnet"
    assert session(d, venues.ALPACA).network == "paper"


def test_a_venue_with_no_credentials_is_unroutable_rather_than_fatal():
    d = Desk.open(config=Config(), wanted=(venues.ALPACA,),
                  session_factory=factory(fails={venues.ALPACA: MissingCredentials("no key")}))
    assert d.routable == ()
    assert refusal(d, venues.ALPACA).reason == REASON_NO_CREDENTIALS


def test_a_missing_key_on_one_venue_does_not_block_the_other():
    """The requirement this class exists for. A single-broker session raised on the first
    missing credential, so one unconfigured venue took the whole run with it — and the run had
    a perfectly good key for the venue the trade was routed to.
    """
    d = Desk.open(config=Config(), wanted=(venues.HYPERLIQUID, venues.ALPACA),
                  session_factory=factory(fails={venues.ALPACA: MissingCredentials("no key")}))
    assert d.routable == (venues.HYPERLIQUID,)
    assert d.session_for(venues.HYPERLIQUID) is not None
    assert d.session_for(venues.ALPACA) is None


def test_an_unreachable_venue_degrades_and_keeps_what_the_venue_said():
    d = Desk.open(config=Config(), wanted=(venues.HYPERLIQUID,),
                  session_factory=factory(fails={venues.HYPERLIQUID: TimeoutError("api down")}))
    dropped = refusal(d, venues.HYPERLIQUID)
    assert dropped.reason == REASON_UNREACHABLE
    assert "TimeoutError" in dropped.detail and "api down" in dropped.detail


def test_the_missing_credentials_message_survives_too():
    # The message names the two environment variables and how to load them; losing it would
    # leave "no_credentials" with nothing actionable attached.
    d = Desk.open(config=Config(), wanted=(venues.ALPACA,),
                  session_factory=factory(
                      fails={venues.ALPACA: MissingCredentials("ALPACA_API_KEY_ID not set")}))
    assert "ALPACA_API_KEY_ID" in refusal(d, venues.ALPACA).detail


def test_a_venue_this_package_cannot_place_on_is_unroutable_not_an_exception():
    """Kraken is priced by ``core.routing`` and has no ``Broker``. If it ever reaches here the
    answer is "not routable", the same as a missing key — a venue with no adapter is a missing
    fact about this repo, not a bug in the caller.
    """
    open_session = factory()
    d = Desk.open(config=Config(), wanted=("kraken", venues.HYPERLIQUID),
                  session_factory=open_session)
    assert d.routable == (venues.HYPERLIQUID,)
    assert refusal(d, "kraken").reason == REASON_NO_ADAPTER
    assert open_session.opened == [venues.HYPERLIQUID], "no session attempt for a venue with no adapter"


def test_a_declined_confirmation_is_carried_in_rather_than_re_asked():
    # The typed real-money barrier lives in the UI layer, so the desk is *told* the outcome.
    declined = {venues.ALPACA: Unroutable(venues.ALPACA, REASON_NOT_CONFIRMED, "not confirmed")}
    open_session = factory()
    d = Desk.open(config=Config(), wanted=(venues.HYPERLIQUID, venues.ALPACA),
                  unroutable=declined, session_factory=open_session)
    assert d.routable == (venues.HYPERLIQUID,)
    assert open_session.opened == [venues.HYPERLIQUID], "a declined venue must not be connected to"


def test_a_venue_named_twice_is_opened_once():
    open_session = factory()
    Desk.open(config=Config(), wanted=(venues.HYPERLIQUID, venues.HYPERLIQUID),
              session_factory=open_session)
    assert open_session.opened == [venues.HYPERLIQUID]


def test_wanting_nothing_is_an_empty_desk_and_not_an_error():
    d = Desk.open(config=Config(), wanted=(), session_factory=factory())
    assert d.routable == ()
    assert d.session_for(venues.HYPERLIQUID) is None


def test_the_orders_log_is_shared_across_venues():
    # One log, scoped per network by ``store.placed_keys``. Two logs would make "what did I
    # send tonight" a question with two answers.
    d = Desk.open(config=Config(), wanted=(venues.HYPERLIQUID, venues.ALPACA),
                  orders_path="data/orders.jsonl", session_factory=factory())
    assert {s.orders_path for s in d.sessions.values()} == {"data/orders.jsonl"}


# ── what pools and what does not ─────────────────────────────────────────────────────────────

def test_buying_power_does_not_pool_across_venues():
    """Each session keeps its own committed total, and that is correct rather than incidental:
    there is no transfer path between a perp margin pool and equity buying power, so notional
    sent on one venue frees nothing and blocks nothing on the other. T6 pools *risk*, which is
    a portfolio quantity; this one is not.
    """
    d = Desk.open(config=Config(), wanted=(venues.HYPERLIQUID, venues.ALPACA),
                  session_factory=factory())
    session(d, venues.HYPERLIQUID)._committed = 50_000.0
    assert session(d, venues.ALPACA)._committed == 0.0


# ── can_short: asked of the venue it is a fact about ─────────────────────────────────────────

def test_shortability_is_read_from_the_venue_it_describes():
    """``can_short`` gates Alpaca shorts, so it has to be Alpaca's answer. A single-broker run
    read whichever account happened to be connected — which on a Hyperliquid session is no
    account at all, reported as "not asked" for a fact Alpaca would have answered.
    """
    accounts = {venues.ALPACA: Account(equity=100.0, can_short=True)}
    d = Desk.open(config=Config(venue=venues.HYPERLIQUID),
                  wanted=(venues.HYPERLIQUID, venues.ALPACA),
                  session_factory=factory(accounts=accounts))
    assert d.can_short(venues.ALPACA) is True


def test_shortability_is_unknown_when_the_venue_is_not_routable():
    # Distinct from False. ``core.routing`` renders "not asked" as a different refusal from a
    # measured "cannot short", because Alpaca has been seen disagreeing with itself.
    d = Desk.open(config=Config(), wanted=(venues.HYPERLIQUID,), session_factory=factory())
    assert d.can_short(venues.ALPACA) is None


def test_shortability_is_unknown_when_the_venue_reports_no_account():
    d = Desk.open(config=Config(), wanted=(venues.HYPERLIQUID,), session_factory=factory())
    assert d.can_short(venues.HYPERLIQUID) is None


def test_the_account_is_read_once_per_venue():
    # One network round-trip per venue per sitting. Re-reading between candidates would size
    # two approvals against different balances for no reason anyone asked for.
    accounts = {venues.ALPACA: Account(equity=100.0, can_short=False)}
    d = Desk.open(config=Config(), wanted=(venues.ALPACA,),
                  session_factory=factory(accounts=accounts))
    assert d.can_short(venues.ALPACA) is False
    assert d.can_short(venues.ALPACA) is False
    assert session(d, venues.ALPACA).account_reads == 1


# ── the default venue: still a setting, no longer the decision ───────────────────────────────

def test_the_configured_venue_is_the_fallback_when_routing_has_no_answer():
    d = Desk.open(config=Config(venue=venues.HYPERLIQUID),
                  wanted=(venues.HYPERLIQUID, venues.ALPACA), session_factory=factory())
    assert d.resolve(None) is d.session_for(venues.HYPERLIQUID)


def test_a_routed_venue_overrides_the_configured_one():
    d = Desk.open(config=Config(venue=venues.HYPERLIQUID),
                  wanted=(venues.HYPERLIQUID, venues.ALPACA), session_factory=factory())
    assert d.resolve(venues.ALPACA) is d.session_for(venues.ALPACA)


def test_resolving_an_unroutable_venue_is_none_and_not_a_silent_fallback():
    """A candidate routed to a venue the run cannot reach must not be placed somewhere else.
    Falling back to the configured venue would send the trade to the venue the router had
    already priced as the more expensive one, under a line that said otherwise.
    """
    d = Desk.open(config=Config(venue=venues.HYPERLIQUID), wanted=(venues.HYPERLIQUID,),
                  session_factory=factory())
    assert d.resolve(venues.ALPACA) is None


def test_the_fallback_is_none_when_the_configured_venue_itself_failed():
    d = Desk.open(config=Config(venue=venues.ALPACA, network="paper"),
                  wanted=(venues.ALPACA,),
                  session_factory=factory(fails={venues.ALPACA: MissingCredentials("x")}))
    assert d.resolve(None) is None


# ── reporting ────────────────────────────────────────────────────────────────────────────────

def test_every_wanted_venue_is_accounted_for():
    # Either routable or refused with a reason. A venue that vanished from both would be one
    # the run silently declined to consider.
    wanted = (venues.HYPERLIQUID, venues.ALPACA, "kraken")
    d = Desk.open(config=Config(), wanted=wanted,
                  session_factory=factory(fails={venues.ALPACA: MissingCredentials("x")}))
    assert set(d.routable) | set(d.unroutable) == set(wanted)


def test_describe_names_each_venue_its_network_and_why_one_dropped_out():
    d = Desk.open(config=Config(venue=venues.HYPERLIQUID, network="testnet"),
                  wanted=(venues.HYPERLIQUID, venues.ALPACA),
                  session_factory=factory(
                      fails={venues.ALPACA: MissingCredentials("ALPACA_API_KEY_ID not set")}))
    text = desk_module.describe(d)
    assert "hyperliquid testnet" in text
    assert "alpaca" in text and "ALPACA_API_KEY_ID" in text


# ── the pooled risk book ─────────────────────────────────────────────────────────────────────

class RiskySession(FakeSession):
    """A session carrying risk and equity, for the pooling arithmetic."""

    def __init__(self, config, *, equity=100_000.0, risk=0.0, unpriced=0, raises=False, **kw):
        super().__init__(config, **kw)
        self._equity = equity
        self.risk_at_stake = risk
        self.risk_unpriced = unpriced
        self._raises = raises

    def equity(self, dex=""):
        if self._raises:
            raise TimeoutError("api down")
        return self._equity


def risky(**per_venue):
    def open_session(*, config, dexs=(), orders_path=None):
        return RiskySession(config, orders_path=orders_path, **per_venue.get(config.venue, {}))
    return open_session


def _both(**per_venue):
    return Desk.open(config=Config(venue=venues.HYPERLIQUID),
                     wanted=(venues.HYPERLIQUID, venues.ALPACA),
                     session_factory=risky(**per_venue))


def test_risk_pools_across_venues():
    """The point. Losing 1% on each of two books is losing 2% of one person's account, so the
    sixth approval of a sitting sees the first five wherever they went.
    """
    book = _both(hyperliquid={"equity": 1_000.0, "risk": 20.0},
                 alpaca={"equity": 99_000.0, "risk": 2_000.0}).book()
    assert book.pool.equity == 100_000.0
    assert book.spent == 2_020.0
    assert book.remaining == pytest.approx(0.05 * 100_000.0 - 2_020.0)


def test_buying_power_is_not_pooled_alongside_it():
    # Deliberately absent: there is no transfer path between a perp margin pool and equity
    # buying power, so headroom stays a per-session question. Only risk crosses.
    desk = _both()
    assert not hasattr(desk, "headroom")
    assert not hasattr(desk, "buying_power")


def test_a_venue_that_cannot_report_equity_is_silent_not_empty():
    book = _both(hyperliquid={"raises": True}, alpaca={"equity": 100_000.0}).book()
    assert book.pool.silent == (venues.HYPERLIQUID,)
    assert book.pool.equity == 100_000.0
    assert not book.exact


def test_no_venue_reporting_equity_switches_the_ceiling_off():
    # Rather than setting it to zero, which would refuse every order in the account.
    book = _both(hyperliquid={"raises": True}, alpaca={"raises": True}).book()
    assert book.remaining is None


def test_unrecorded_risk_is_carried_so_the_total_reads_as_a_lower_bound():
    book = _both(alpaca={"equity": 100_000.0, "risk": 500.0, "unpriced": 2}).book()
    assert book.unpriced == 2
    assert not book.exact


def test_a_complete_read_says_so():
    assert _both(hyperliquid={"equity": 1.0}, alpaca={"equity": 1.0}).book().exact


def test_the_ceiling_comes_from_the_config():
    desk = Desk.open(
        config=Config(venue=venues.ALPACA, network="paper", max_portfolio_risk=0.02),
        wanted=(venues.ALPACA,), session_factory=risky(alpaca={"equity": 100_000.0}))
    assert desk.book().remaining == pytest.approx(2_000.0)


def test_no_configured_ceiling_leaves_the_gate_off():
    desk = Desk.open(
        config=Config(venue=venues.ALPACA, network="paper", max_portfolio_risk=None),
        wanted=(venues.ALPACA,), session_factory=risky(alpaca={"equity": 100_000.0}))
    assert desk.book().remaining is None


def test_a_bad_venue_network_pair_is_a_config_error_and_not_an_outage():
    """It would otherwise be caught as ``unreachable``, sending someone to check the network for
    what is a typo in a yaml file. ``testnet`` is Hyperliquid's spelling and Alpaca's rehearsal
    is ``paper``; the loader resolves that, a hand-built Config does not.
    """
    with pytest.raises(ValueError, match="network for venue"):
        Desk.open(config=Config(venue=venues.ALPACA, network="testnet"),
                  wanted=(venues.ALPACA,), session_factory=factory())
