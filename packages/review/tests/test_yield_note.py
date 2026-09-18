from datetime import UTC, date, datetime

from core.altsignal import AltSignalReading
from core.review import NO_VIEW, UNREADABLE, Holding, Location, Reading, RosterLean
from oracle.altsignal_config import AltSignalConfig, WrapperEntry
from oracle.portfolios import Position
from review.yield_note import yield_notes

AS_OF = date(2026, 9, 17)
READ_AT = datetime(2026, 9, 15, tzinfo=UTC)

CONTRACT_STETH = "0x" + "1" * 40
CONTRACT_RETH = "0x" + "2" * 40
CONTRACT_METH = "0x" + "3" * 40

WRAPPER_STETH = WrapperEntry(
    asset="ETH", wrapper="STETH", contract=CONTRACT_STETH,
    llama_protocol="lido", llama_pools=("pool-steth",),
)
WRAPPER_RETH = WrapperEntry(
    asset="ETH", wrapper="RETH", contract=CONTRACT_RETH,
    llama_protocol="rocket-pool", llama_pools=("pool-reth",),
)


def _reading(ticker, *, price=100.0, shares=1.0):
    return Reading(
        holding=Holding(ticker=ticker, shares=shares, cost=None),
        roster=RosterLean(lean="silent", bulls=0, bears=0, people=0, newest=None,
                          age_days=None, voices=(), thin=False),
        location=Location(where=UNREADABLE, basis="none"),
        verdict=NO_VIEW, price=price, weekly_trend=None,
    )


def _position(ticker, shares, *, figi=None):
    return Position(holding=Holding(ticker=ticker, shares=shares, cost=None),
                    domain="crypto", figi=figi)


def _store_read(rows):
    def read(*, source=None, kind=None, key=None, since=None):
        return [
            r for r in rows
            if (source is None or r.source == source)
            and (kind is None or r.kind == kind)
            and (key is None or r.key == key)
        ]
    return read


def _facts(slug, *, audited=True):
    return [
        AltSignalReading(
            source="defillama", kind="venue_safety_facts", key=slug,
            value={"audited": audited, "forked_from": [], "incidents": 0}, observed_at=READ_AT,
        ),
        AltSignalReading(
            source="defillama", kind="venue_first_tvl", key=slug,
            value="2020-01-01", observed_at=READ_AT,
        ),
    ]


def _pool(pool_id, apy):
    return AltSignalReading(
        source="defillama", kind="venue_pool", key=pool_id,
        value={"apy": apy, "apy_reward": None, "sigma": 0.1, "count": 1000,
               "outlier": False, "stablecoin": False},
        observed_at=READ_AT,
    )


def test_no_config_yields_nothing():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=())
    positions = [_position("ETH", 1.0, figi=CONTRACT_METH)]
    notes = yield_notes(
        [_reading("ETH")], ["ETH"], positions, altsignal_cfg=cfg, as_of=AS_OF,
        store_read=_store_read([]),
    )
    assert notes == ()


def test_no_stored_pool_yields_nothing():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    positions = [_position("ETH", 1.0, figi=CONTRACT_METH)]
    notes = yield_notes(
        [_reading("ETH")], ["ETH"], positions, altsignal_cfg=cfg, as_of=AS_OF,
        store_read=_store_read([]),
    )
    assert notes == ()


def test_gate_failing_yields_nothing_even_with_real_stored_facts():
    """An empty store also produces silence, so this fixture stores real facts whose
    ``audited`` is ``False`` — the only way to prove AC 4 actually gates rather than
    coincidentally passing because nothing was fetched."""
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido", audited=False), _pool("pool-steth", 2.25)]
    positions = [_position("ETH", 1.0, figi=CONTRACT_METH)]
    notes = yield_notes(
        [_reading("ETH")], ["ETH"], positions, altsignal_cfg=cfg, as_of=AS_OF,
        store_read=_store_read(store),
    )
    assert notes == ()


def test_a_wrapper_already_held_by_contract_is_dropped_and_named():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH, WRAPPER_RETH))
    store = [*_facts("rocket-pool"), _pool("pool-reth", 2.16)]
    positions = [_position("ETH", 1.0, figi=CONTRACT_STETH)]
    [note] = yield_notes(
        [_reading("ETH", shares=1.0)], ["ETH"], positions, altsignal_cfg=cfg, as_of=AS_OF,
        store_read=_store_read(store),
    )
    assert note.wrapper == "RETH"
    assert note.already == (("STETH", 1.0),)


def test_a_wrapper_already_held_by_ticker_on_a_figiless_position_is_dropped_and_named():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH, WRAPPER_RETH))
    store = [*_facts("rocket-pool"), _pool("pool-reth", 2.16)]
    positions = [
        _position("ETH", 1.0, figi=CONTRACT_METH),   # satisfies the readability gate (AC 3)
        _position("STETH", 2.5, figi=None),           # matched by ticker, not contract
    ]
    readings = [_reading("ETH", shares=1.0), _reading("STETH", shares=2.5)]
    [note] = yield_notes(
        readings, ["ETH", "STETH"], positions, altsignal_cfg=cfg, as_of=AS_OF,
        store_read=_store_read(store),
    )
    assert note.wrapper == "RETH"
    assert note.already == (("STETH", 2.5),)


def test_a_book_with_only_bloomberg_figis_is_silent():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido"), _pool("pool-steth", 2.25)]
    positions = [_position("ETH", 1.0, figi="BBG00564XQN4")]
    notes = yield_notes(
        [_reading("ETH")], ["ETH"], positions, altsignal_cfg=cfg, as_of=AS_OF,
        store_read=_store_read(store),
    )
    assert notes == ()


def test_two_passing_wrappers_suggests_the_higher_apy():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH, WRAPPER_RETH))
    store = [
        *_facts("lido"), _pool("pool-steth", 2.25),
        *_facts("rocket-pool"), _pool("pool-reth", 2.16),
    ]
    positions = [_position("ETH", 1.0, figi=CONTRACT_METH)]
    [note] = yield_notes(
        [_reading("ETH")], ["ETH"], positions, altsignal_cfg=cfg, as_of=AS_OF,
        store_read=_store_read(store),
    )
    assert note.wrapper == "STETH"


def test_two_positions_canonicalising_to_one_asset_produce_one_note_on_the_larger_holding():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido"), _pool("pool-steth", 2.25)]
    readings = [
        _reading("ETH", price=2454.0, shares=0.11026),
        _reading("WETH", price=2454.0, shares=0.5),
    ]
    positions = [
        _position("ETH", 0.11026, figi=None),
        _position("WETH", 0.5, figi=CONTRACT_METH),
    ]
    [note] = yield_notes(
        readings, ["ETH", "ETH"], positions, altsignal_cfg=cfg, as_of=AS_OF,
        store_read=_store_read(store),
    )
    assert note.reading.holding.ticker == "WETH"
