"""The triage-to-order-book glue: the confirmation gate, and every way out of it."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from execution import desk as desk_module
from execution import store
from execution.config import Config, MissingCredentials
from execution.desk import Desk, Unroutable
from execution.liquidity import Liquidity
from execution.plan import Market, OrderPlan
from execution.session import Session
from execution.wire import Placement
from oracle import execute

# ── fakes ───────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StubCandidate:
    asset: str = "ETH"
    direction: str = "long"
    entry: float = 3_200.0
    stop: float = 3_050.0
    target: float = 3_900.0
    key: str = "abc123"


class FakeBroker:
    def __init__(self, placement=None, *, raises=None):
        self._placement = placement or Placement(ok=True, order_ids=(1, 2, 3))
        self._raises = raises
        self.placed: list[OrderPlan] = []

    def markets(self):
        return {"ETH": Market(coin="ETH", sz_decimals=4)}

    def equity(self, dex: str = "") -> float:
        return 10_000.0

    def account(self):
        # The perp venue's answer: no account-wide budget, so the gate stays off here. The
        # equity broker's own reads are exercised in ``packages/execution``.
        return None

    def resting(self):
        return None

    def positions(self):
        return None

    def cancel(self, order_id: str):
        return None

    def liquidity(self, coin):
        return Liquidity(coin=coin, day_volume=50_000_000.0, open_interest=100_000_000.0,
                         bid_depth=500_000.0, ask_depth=500_000.0, spread=0.0001)

    def depth(self, coin):
        return None

    def place(self, plan):
        if self._raises:
            raise self._raises
        self.placed.append(plan)
        return self._placement


class Recorder:
    """Collects printed output and feeds scripted answers."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.lines: list[str] = []

    def out(self, text=""):
        self.lines.append(str(text))

    def input(self, _prompt=""):
        return self.answers.pop(0) if self.answers else ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _session(tmp_path, broker=None):
    broker = broker or FakeBroker()
    return Session(
        broker=broker,
        config=Config(network="testnet"),
        markets=broker.markets(),
        orders_path=tmp_path / "orders.jsonl",
    )


# ── the confirmation gate ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
def test_yes_places_the_order(tmp_path, answer):
    session = _session(tmp_path)
    rec = Recorder([answer])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)

    assert len(session.broker.placed) == 1
    assert "placed on testnet" in rec.text
    assert store.load(session.orders_path)[0]["outcome"] == store.PLACED


@pytest.mark.parametrize("answer", ["", "n", "no", "q", "anything else"])
def test_anything_but_yes_declines(tmp_path, answer):
    """The default must be *not* trading. A stray return key cannot open a position."""
    session = _session(tmp_path)
    rec = Recorder([answer])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)

    assert session.broker.placed == []
    assert "declined" in rec.text


def test_declining_is_recorded_not_merely_printed(tmp_path):
    """So the order log answers 'what did I approve and then think better of', which is the
    half of the question the venue cannot reconstruct."""
    session = _session(tmp_path)
    rec = Recorder(["n"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)

    row = store.load(session.orders_path)[0]
    assert row["outcome"] == store.REFUSED
    assert row["reason"] == execute.DECLINED


def test_the_preview_precedes_the_prompt(tmp_path):
    """A person must see size, risk and both exits before answering."""
    session = _session(tmp_path)
    rec = Recorder(["n"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)
    assert "BUY" in rec.text and "3200" in rec.text and "stop loss" in rec.text


# ── refusals never reach the prompt ─────────────────────────────────────────────────────────

def test_an_unlisted_asset_is_refused_without_asking(tmp_path):
    """No point asking whether to send an order that cannot be built."""
    session = _session(tmp_path)
    rec = Recorder(["y"])   # would say yes if asked
    execute.offer(session, StubCandidate(asset="NOTATHING"), input_fn=rec.input, out=rec.out)

    assert session.broker.placed == []
    assert "not executable" in rec.text
    assert store.load(session.orders_path)[0]["outcome"] == store.REFUSED


def test_a_dormant_market_is_refused_without_asking(tmp_path):
    """``xyz:DXY``: mapped, quoted by nobody, and the funding log has said so for months.

    Refused on the map plus the log alone, before a single network call — so it holds on a
    venue outage and on testnet, where the book is mock and proves nothing either way.
    """
    session = _session(tmp_path)
    rec = Recorder(["y"])   # would say yes if asked
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out,
                  is_dormant=lambda *_: True)

    assert session.broker.placed == []
    assert "not executable" in rec.text
    row = store.load(session.orders_path)[0]
    assert row["outcome"] == store.REFUSED
    assert row["reason"] == execute.DORMANT


def test_dormancy_is_asked_about_the_session_venue(tmp_path):
    """A market dead on one venue and busy on another is two different answers."""
    session = _session(tmp_path)
    asked: list[tuple[str, str]] = []
    execute.offer(session, StubCandidate(), input_fn=Recorder(["n"]).input, out=lambda *_: None,
                  is_dormant=lambda asset, venue: asked.append((asset, venue)) or False)
    assert asked == [("ETH", session.config.venue)]


def test_a_live_market_still_reaches_the_prompt(tmp_path):
    """The gate must be inert on healthy markets — every one of them, by default."""
    session = _session(tmp_path)
    rec = Recorder(["y"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out,
                  is_dormant=lambda *_: False)
    assert len(session.broker.placed) == 1


# ── failure modes ───────────────────────────────────────────────────────────────────────────

def test_a_venue_rejection_is_reported_loudly(tmp_path):
    session = _session(tmp_path, FakeBroker(Placement(ok=False, error="insufficient margin")))
    rec = Recorder(["y"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)
    assert "REJECTED" in rec.text and "insufficient margin" in rec.text


def test_a_partially_resting_bracket_names_the_legs_that_survived(tmp_path):
    """The worst outcome available — an entry resting with no stop behind it. It must be
    impossible to miss, because only a human can decide what to do about it."""
    session = _session(tmp_path, FakeBroker(
        Placement(ok=False, order_ids=(111, 222), error="stop rejected")))
    rec = Recorder(["y"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)

    assert "DID rest" in rec.text
    assert "111" in rec.text and "222" in rec.text


def test_an_exception_does_not_end_the_session(tmp_path):
    """A venue timeout must not cost the rest of the queue — the approval is already durable
    and the remaining candidates are still worth reviewing."""
    session = _session(tmp_path, FakeBroker(raises=TimeoutError("venue did not respond")))
    rec = Recorder(["y"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)

    assert "placement failed" in rec.text
    assert "TimeoutError" in rec.text
    assert store.load(session.orders_path)[0]["reason"] == "error"


# ── the real-money barrier ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("venue,network", [("hyperliquid", "mainnet"), ("alpaca", "live")])
def test_real_money_needs_the_exact_phrase(venue, network):
    rec = Recorder(["yes"])
    assert execute.confirm_real_money(venue, network, rec.input, rec.out) is False
    assert "not confirmed" in rec.text


@pytest.mark.parametrize("venue,network", [("hyperliquid", "mainnet"), ("alpaca", "live")])
def test_real_money_accepts_the_phrase(venue, network):
    rec = Recorder([execute.REAL_MONEY_CONFIRMATION])
    assert execute.confirm_real_money(venue, network, rec.input, rec.out) is True


@pytest.mark.parametrize("venue,network", [("hyperliquid", "mainnet"), ("alpaca", "live")])
def test_the_banner_names_the_account_it_is_about(venue, network):
    """A prompt reading *** MAINNET *** over an Alpaca brokerage account is one a reader
    would be right to distrust, and distrusting this prompt is the whole failure mode."""
    rec = Recorder([execute.REAL_MONEY_CONFIRMATION])
    execute.confirm_real_money(venue, network, rec.input, rec.out)
    assert venue in rec.text.lower()
    assert network in rec.text.lower()


def _hyperliquid_config(tmp_path, network: str):
    """A config naming Hyperliquid, so the *other* venue is Alpaca and the tier translation is
    the thing under test. cfg/execution.yaml names alpaca, and reading it here would make these
    tests move with a setting."""
    path = tmp_path / "execution.yaml"
    path.write_text(f"venue: hyperliquid\nnetwork: {network}\n")
    return path


class DeskFactory:
    """A session factory for ``open_desk``: records which venues it was asked to connect to."""

    def __init__(self, fails=None):
        self.fails = fails or {}
        self.configs: list = []

    def __call__(self, *, config, dexs=(), orders_path=None):
        self.configs.append(config)
        if config.venue in self.fails:
            raise self.fails[config.venue]
        return _FakeVenueSession(config)

    @property
    def venues(self) -> list[str]:
        return [c.venue for c in self.configs]


class _FakeVenueSession:
    markets: dict = {}

    def __init__(self, config):
        self.config = config
        self.orders_path = "unused"

    @property
    def network(self) -> str:
        return self.config.network

    def read_account(self):
        return None


@pytest.mark.parametrize("venue,network", [("hyperliquid", "mainnet"), ("alpaca", "live")])
def test_every_real_money_network_is_gated(tmp_path, venue, network):
    """The bug this replaces: the gate compared against ``MAINNET`` alone, which is
    Hyperliquid's spelling. Alpaca's real-money network is ``live``, did not match, and so a
    funded brokerage account connected with nothing typed."""
    path = tmp_path / "execution.yaml"
    path.write_text(f"venue: {venue}\nnetwork: {network}\n")
    rec = Recorder(["no thanks"])

    assert execute.open_desk(wanted=(), config_path=path,
                             input_fn=rec.input, out=rec.out) is None
    assert "not confirmed" in rec.text


@pytest.mark.parametrize("venue,network", [("hyperliquid", "testnet"), ("alpaca", "paper")])
def test_no_rehearsal_network_is_gated(tmp_path, venue, network):
    """The barrier must stay inert on both rehearsals — one that fires on paper is one that
    gets typed through by reflex, which is how it stops being a barrier on live."""
    path = tmp_path / "execution.yaml"
    path.write_text(f"venue: {venue}\nnetwork: {network}\n")
    asked = []

    desk = execute.open_desk(
        wanted=(), config_path=path, session_factory=DeskFactory(),
        input_fn=lambda p: asked.append(p) or "", out=lambda _: None,
    )
    assert desk is not None
    assert asked == []          # nothing was typed, because nothing was asked


def test_the_barrier_is_taken_once_per_real_money_venue(tmp_path):
    """A run at the real-money tier reaches real money on every venue it opens, because the
    tier is translated and not the word — so each venue is asked for itself. Asking once and
    applying the answer to both would let one typed phrase open a brokerage account nobody
    named."""
    rec = Recorder([execute.REAL_MONEY_CONFIRMATION, execute.REAL_MONEY_CONFIRMATION])
    desk = execute.open_desk(
        wanted=("alpaca",), config_path=_hyperliquid_config(tmp_path, "mainnet"),
        session_factory=DeskFactory(), input_fn=rec.input, out=rec.out,
    )
    assert desk is not None
    assert sorted(desk.routable) == ["alpaca", "hyperliquid"]
    banners = [ln for ln in rec.lines if "real funds" in ln]
    assert len(banners) == 2, "each venue gets its own banner naming its own network"
    assert any("ALPACA LIVE" in ln for ln in banners)
    assert any("HYPERLIQUID MAINNET" in ln for ln in banners)


def test_declining_one_venue_leaves_the_other_routable(tmp_path):
    """"Real funds on the perp book tonight, not on the brokerage account" is an answer worth
    being able to give, and a single up-front barrier could not express it."""
    rec = Recorder([execute.REAL_MONEY_CONFIRMATION, "no thanks"])
    factory = DeskFactory()
    desk = execute.open_desk(
        wanted=("alpaca",), config_path=_hyperliquid_config(tmp_path, "mainnet"),
        session_factory=factory, input_fn=rec.input, out=rec.out,
    )
    assert desk is not None
    assert desk.routable == ("hyperliquid",)
    assert factory.venues == ["hyperliquid"], "a declined venue must not be connected to"
    assert desk.refusal_for("alpaca").reason == desk_module.REASON_NOT_CONFIRMED


def test_the_configured_venue_is_always_opened_even_when_nothing_routes_to_it(tmp_path):
    # It is the fallback for a candidate routing has no answer for, so discovering it is
    # unreachable at that point would be discovering it too late.
    desk = execute.open_desk(wanted=("alpaca",), session_factory=DeskFactory(),
                             config_path=_hyperliquid_config(tmp_path, "testnet"),
                             out=lambda _: None)
    assert desk is not None
    assert sorted(desk.routable) == ["alpaca", "hyperliquid"]


def test_a_venue_that_cannot_be_reached_does_not_end_the_run(tmp_path):
    factory = DeskFactory(fails={"alpaca": MissingCredentials("ALPACA_API_KEY_ID not set")})
    rec = Recorder([])
    desk = execute.open_desk(wanted=("alpaca",), session_factory=factory,
                             config_path=_hyperliquid_config(tmp_path, "testnet"),
                             input_fn=rec.input, out=rec.out)
    assert desk is not None
    assert desk.routable == ("hyperliquid",)
    assert "ALPACA_API_KEY_ID" in rec.text, "the reason belongs on screen, not only in the object"


def test_no_reachable_venue_is_reported_and_returns_nothing(tmp_path):
    factory = DeskFactory(fails={"hyperliquid": MissingCredentials("HYPERLIQUID_SECRET_KEY")})
    rec = Recorder([])
    assert execute.open_desk(wanted=(), session_factory=factory,
                             config_path=_hyperliquid_config(tmp_path, "testnet"),
                             input_fn=rec.input, out=rec.out) is None
    assert "no venue is reachable" in rec.text


# ── HIP-3 discovery ─────────────────────────────────────────────────────────────────────────

def test_hyperliquid_dexs_finds_the_builders_in_the_venue_map():
    """Without these the SDK loads only the core book and every non-crypto asset fails to
    resolve to an asset index."""
    dexs = execute.hyperliquid_dexs()
    assert "xyz" in dexs
    assert "" not in dexs


# ── the rehearsal venue stays honest ────────────────────────────────────────────────────────

class ThinBroker(FakeBroker):
    """A market mainnet would refuse: no book at all, the xyz:DXY case."""

    def liquidity(self, coin):
        return Liquidity(coin=coin)   # spread None -> no two-sided book


def test_testnet_warns_when_the_gate_would_have_fired(tmp_path):
    """Not enforcing on testnet must not mean staying quiet about it — otherwise a dead
    market looks healthy in rehearsal, which is the opposite of the point."""
    session = _session(tmp_path, ThinBroker())
    rec = Recorder(["n"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)

    assert "would fail the liquidity gate" in rec.text
    assert "no two-sided book" in rec.text


def test_the_warning_does_not_claim_to_know_what_mainnet_would_do(tmp_path):
    """The verdict is computed from the connected network's book. On testnet that book is
    mock, so asserting a mainnet outcome would be a confident falsehood — xyz:SP500 has no
    testnet book and $457M/day of real one."""
    session = _session(tmp_path, ThinBroker())
    rec = Recorder(["n"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)

    assert "mainnet would refuse" not in rec.text.lower()
    assert "not a verdict on the real market" in rec.text


def test_testnet_still_allows_the_order_despite_the_warning(tmp_path):
    session = _session(tmp_path, ThinBroker())
    rec = Recorder(["y"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)
    assert len(session.broker.placed) == 1


def test_no_warning_when_liquidity_is_healthy(tmp_path):
    session = _session(tmp_path)
    rec = Recorder(["n"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)
    assert "would fail the liquidity gate" not in rec.text


# ── the equity venue does not inherit the perp venue's warning ──────────────────────────────

def test_alpaca_does_not_print_the_liquidity_warning(tmp_path):
    """It was a constant. ``AlpacaBroker.liquidity`` returns None for every equity, so the
    "could not read this market's liquidity" line fired identically on a fund trading 175
    times a day and one trading 39,000 — it could not distinguish them, so it said nothing."""
    broker = ThinBroker()
    session = Session(
        broker=broker,
        config=Config(venue="alpaca", network="paper"),
        markets=broker.markets(),
        orders_path=tmp_path / "orders.jsonl",
    )
    rec = Recorder(["n"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)
    assert not any("liquidity gate" in line for line in rec.lines)


def test_alpaca_paper_is_never_called_mock(tmp_path):
    """Paper reads the same market data as live — there is no paper price. Only the fill is
    simulated, and optimistically. Telling the reader to discount real data was backwards."""
    broker = ThinBroker()
    session = Session(
        broker=broker,
        config=Config(venue="alpaca", network="paper"),
        markets=broker.markets(),
        orders_path=tmp_path / "orders.jsonl",
    )
    rec = Recorder(["n"])
    execute.offer(session, StubCandidate(), input_fn=rec.input, out=rec.out)
    assert not any("mock" in line for line in rec.lines)


# ── --network changes where, never how much ─────────────────────────────────────────────────

def test_a_network_override_preserves_every_other_setting(tmp_path):
    """The rebuild this replaced listed four of eight fields, so ``--network`` silently reset
    the liquidity floors, the enforcement override and the participation ceiling to defaults.
    A flag about *where* to trade must not change *how much* to risk."""
    path = tmp_path / "execution.yaml"
    path.write_text(
        "venue: hyperliquid\nnetwork: testnet\nrisk_pct: 0.005\n"
        "min_day_volume: 42\nmin_open_interest: 43\nmax_participation: 0.002\n"
        "max_notional_frac: 1.5\nenforce_liquidity: false\n"
    )
    # Injected at the seam because the real ``Session.open`` connects and needs credentials;
    # the config it is handed is the whole subject here.
    factory = DeskFactory()
    execute.open_desk(
        wanted=(), network="mainnet", config_path=path,
        session_factory=factory, input_fn=lambda _: "yes, real money", out=lambda _: None,
    )
    config = factory.configs[0]
    assert config.network == "mainnet"        # the one thing the flag may change
    assert config.risk_pct == 0.005
    assert config.min_day_volume == 42
    assert config.min_open_interest == 43
    assert config.max_participation == 0.002
    assert config.max_notional_frac == 1.5
    assert config.enforce_liquidity is False


# ── the routed venue is the venue, and never quietly the other one ───────────────────────────

def _desk(tmp_path, sessions, *, unroutable=None):
    return Desk(
        config=Config(venue="hyperliquid", network="testnet"),
        orders_path=tmp_path / "orders.jsonl",
        sessions=sessions,
        unroutable=unroutable or {},
    )


def test_a_candidate_is_offered_on_the_venue_it_was_routed_to(tmp_path):
    session = _session(tmp_path)
    desk = _desk(tmp_path, {"hyperliquid": session})
    rec = Recorder(["y"])
    execute.offer_routed(desk, StubCandidate(), "hyperliquid",
                         input_fn=rec.input, out=rec.out, is_dormant=lambda *_: False)
    assert len(session.broker.placed) == 1


def test_an_unreachable_routed_venue_is_never_silently_swapped_for_another(tmp_path):
    """The failure this guards is subtle and expensive: the queue has already printed which
    venue was cheaper and by how much, so placing on the runner-up makes that line a false
    statement about an order that exists — and pays the difference the router was built to
    stop paying.
    """
    fallback = _session(tmp_path)
    desk = _desk(tmp_path, {"hyperliquid": fallback}, unroutable={
        "alpaca": Unroutable("alpaca", desk_module.REASON_NO_CREDENTIALS, "no key"),
    })
    rec = Recorder(["y"])
    execute.offer_routed(desk, StubCandidate(), "alpaca",
                         input_fn=rec.input, out=rec.out, is_dormant=lambda *_: False)
    assert fallback.broker.placed == [], "nothing may be placed on a venue nobody chose"
    assert "not executable" in rec.text
    assert "alpaca" in rec.text and "no key" in rec.text


def test_an_unreachable_routed_venue_is_recorded_not_merely_printed(tmp_path):
    # "Approved and not sent" is the class of outcome the order log exists to keep.
    desk = _desk(tmp_path, {}, unroutable={
        "alpaca": Unroutable("alpaca", desk_module.REASON_NO_CREDENTIALS, "no key"),
    })
    execute.offer_routed(desk, StubCandidate(), "alpaca",
                         input_fn=lambda _: "y", out=lambda _: None)
    rows = store.load(desk.orders_path)
    assert [r["reason"] for r in rows] == [execute.UNROUTABLE]
    assert rows[0]["network"] == desk.network


def test_no_routing_answer_falls_back_to_the_configured_venue(tmp_path):
    # ``Config.venue`` is no longer the decision, and this is what is left of it.
    session = _session(tmp_path)
    desk = _desk(tmp_path, {"hyperliquid": session})
    rec = Recorder(["y"])
    execute.offer_routed(desk, StubCandidate(), None,
                         input_fn=rec.input, out=rec.out, is_dormant=lambda *_: False)
    assert len(session.broker.placed) == 1


def test_a_fallback_to_an_unreachable_configured_venue_refuses_too(tmp_path):
    desk = _desk(tmp_path, {}, unroutable={
        "hyperliquid": Unroutable("hyperliquid", desk_module.REASON_UNREACHABLE, "api down"),
    })
    rec = Recorder(["y"])
    execute.offer_routed(desk, StubCandidate(), None, input_fn=rec.input, out=rec.out)
    assert "hyperliquid" in rec.text and "api down" in rec.text
