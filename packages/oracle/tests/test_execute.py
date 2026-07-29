"""The triage-to-order-book glue: the confirmation gate, and every way out of it."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from execution import store
from execution.config import Config
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


@pytest.mark.parametrize("venue,network", [("hyperliquid", "mainnet"), ("alpaca", "live")])
def test_every_real_money_network_is_gated(tmp_path, venue, network):
    """The bug this replaces: the gate compared against ``MAINNET`` alone, which is
    Hyperliquid's spelling. Alpaca's real-money network is ``live``, did not match, and so a
    funded brokerage account connected with nothing typed."""
    path = tmp_path / "execution.yaml"
    path.write_text(f"venue: {venue}\nnetwork: {network}\n")
    rec = Recorder(["no thanks"])

    assert execute.open_session(config_path=path, input_fn=rec.input, out=rec.out) is None
    assert "not confirmed" in rec.text


@pytest.mark.parametrize("venue,network", [("hyperliquid", "testnet"), ("alpaca", "paper")])
def test_no_rehearsal_network_is_gated(tmp_path, venue, network):
    """The barrier must stay inert on both rehearsals — one that fires on paper is one that
    gets typed through by reflex, which is how it stops being a barrier on live."""
    path = tmp_path / "execution.yaml"
    path.write_text(f"venue: {venue}\nnetwork: {network}\n")
    asked = []

    class FakeSession:
        markets: dict = {}
        orders_path = "unused"

        @classmethod
        def open(cls, *, config, dexs=()):
            return cls()

    original, execute.Session = execute.Session, FakeSession
    try:
        session = execute.open_session(
            config_path=path,
            input_fn=lambda p: asked.append(p) or "", out=lambda _: None,
        )
    finally:
        execute.Session = original

    assert session is not None
    assert asked == []          # nothing was typed, because nothing was asked


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
    captured = {}

    class FakeSession:
        markets: dict = {}
        orders_path = "unused"

        @classmethod
        def open(cls, *, config, dexs=()):
            captured["config"] = config
            return cls()

    # Patched at the seam because the real ``Session.open`` connects and needs credentials;
    # the config it is handed is the whole subject here.
    original, execute.Session = execute.Session, FakeSession
    try:
        execute.open_session(
            network="mainnet", config_path=path,
            input_fn=lambda _: "yes, real money", out=lambda _: None,
        )
    finally:
        execute.Session = original

    config = captured["config"]
    assert config.network == "mainnet"        # the one thing the flag may change
    assert config.risk_pct == 0.005
    assert config.min_day_volume == 42
    assert config.min_open_interest == 43
    assert config.max_participation == 0.002
    assert config.max_notional_frac == 1.5
    assert config.enforce_liquidity is False
