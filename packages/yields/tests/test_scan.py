from datetime import UTC, date, datetime

from core.altsignal import AltSignalReading
from core.canon import Registry
from core.review import Holding
from oracle.altsignal_config import AltSignalConfig, WrapperEntry
from oracle.portfolios import Mandate, Portfolio, Position
from yields.scan import NOT_FETCHED, yields_for

AS_OF = date(2026, 9, 21)
READ_AT = datetime(2026, 9, 19, tzinfo=UTC)

CONTRACT_STETH = "0x" + "1" * 40
CONTRACT_METH = "0x" + "3" * 40

WRAPPER_STETH = WrapperEntry(
    asset="ETH", wrapper="STETH", contract=CONTRACT_STETH,
    llama_protocol="lido", llama_pools=("pool-steth",),
)
WRAPPER_METH = WrapperEntry(
    asset="ETH", wrapper="METH", contract=CONTRACT_METH,
    llama_protocol="mantle", llama_pools=("pool-meth",),
)

REGISTRY = Registry(assets={"weth": "ETH"})


def _mandate(name="crypto"):
    return Mandate(name=name, benchmarks=(), horizon="position", risk_posture="aggressive")


def _book(name, positions, *, mandate_name=None):
    return Portfolio(
        name=name, mandate=_mandate(mandate_name or name), positions=tuple(positions),
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


def _facts(slug, *, audited=True, first_tvl="2020-01-01"):
    return [
        AltSignalReading(
            source="defillama", kind="venue_safety_facts", key=slug,
            value={"audited": audited, "forked_from": [], "incidents": 0}, observed_at=READ_AT,
        ),
        AltSignalReading(
            source="defillama", kind="venue_first_tvl", key=slug,
            value=first_tvl, observed_at=READ_AT,
        ),
    ]


def _pool(pool_id, apy, *, outlier=False):
    return AltSignalReading(
        source="defillama", kind="venue_pool", key=pool_id,
        value={"apy": apy, "apy_reward": None, "sigma": 0.1, "count": 1000,
               "outlier": outlier, "stablecoin": False},
        observed_at=READ_AT,
    )


def test_no_wrappers_configured_yields_empty_result_with_counts():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=())
    book = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_METH)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read([]))
    assert result.assets == ()
    assert result.configured == 0
    assert result.matched == 0


def test_two_mandates_holding_one_token_collapse_to_one_row_naming_both():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido"), _pool("pool-steth", 2.25)]
    crypto = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_METH)])
    robinhood = _book("robinhood", [_position("WETH", 0.5, figi=CONTRACT_METH)])
    result = yields_for([crypto, robinhood], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read(store))
    [asset] = result.assets
    assert asset.asset == "ETH"
    assert asset.mandates == ("crypto", "robinhood")


def test_a_held_wrapper_is_present_and_marked_not_dropped():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido", audited=False), _pool("pool-steth", 2.25)]
    book = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_STETH)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read(store))
    [asset] = result.assets
    [option] = asset.options
    assert option.wrapper == "STETH"
    assert option.held == 1.0
    assert option.gate is not None and not option.gate.passed


def test_a_held_wrapper_with_no_stored_reading_survives_as_not_fetched():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    book = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_STETH)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read([]))
    [asset] = result.assets
    [option] = asset.options
    assert option.state == NOT_FETCHED
    assert option.held == 1.0
    assert option.facts is None


def test_a_held_wrapper_flagged_outlier_survives():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido"), _pool("pool-steth", 999.0, outlier=True)]
    book = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_STETH)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read(store))
    [asset] = result.assets
    [option] = asset.options
    assert option.facts.outlier is True
    assert option.held == 1.0


def test_a_held_wrapper_failing_the_gate_survives_with_its_reasons():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido", audited=False), _pool("pool-steth", 2.25)]
    book = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_STETH)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read(store))
    [asset] = result.assets
    [option] = asset.options
    assert option.gate.reasons == ("no audit on record",)


def test_an_unheld_wrapper_failing_the_gate_is_dropped():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido", audited=False), _pool("pool-steth", 2.25)]
    book = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_METH)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read(store))
    assert result.assets == ()


def test_a_book_with_no_readable_figi_reports_unknown_rather_than_nothing_held():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH,))
    store = [*_facts("lido"), _pool("pool-steth", 2.25)]
    book = _book("crypto", [_position("ETH", 1.0, figi=None)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read(store))
    [asset] = result.assets
    assert asset.held_state_readable is False


def test_configured_and_matched_counts_a_mistyped_asset():
    unmatched = WrapperEntry(
        asset="SOL", wrapper="MSOL", contract="0x" + "9" * 40,
        llama_protocol="marinade", llama_pools=("pool-msol",),
    )
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH, unmatched))
    store = [*_facts("lido"), _pool("pool-steth", 2.25)]
    book = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_METH)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read(store))
    assert result.configured == 2
    assert result.matched == 1


def test_gate_passing_options_sort_by_apy_descending():
    cfg = AltSignalConfig(chains=(), markets=(), wrappers=(WRAPPER_STETH, WRAPPER_METH))
    store = [
        *_facts("lido"), _pool("pool-steth", 2.16),
        *_facts("mantle"), _pool("pool-meth", 2.99),
    ]
    book = _book("crypto", [_position("ETH", 1.0, figi=CONTRACT_METH)])
    result = yields_for([book], altsignal_cfg=cfg, as_of=AS_OF, registry=REGISTRY,
                        store_read=_store_read(store))
    [asset] = result.assets
    assert [o.wrapper for o in asset.options] == ["METH", "STETH"]
