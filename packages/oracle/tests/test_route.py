from types import SimpleNamespace

import pytest
from oracle import route as route_mod
from oracle.route import (
    CONFLICT,
    DerivedRef,
    OracleRef,
    RoutingTable,
    Unpriceable,
    build_domain_consensus,
    route,
)


def _table(**kw):
    base = {
        "curated": {},
        "coinbase_symbols": frozenset({"BTC", "ETH", "SOL", "SPX", "META", "IP", "HYPE"}),
        "kraken_symbols": frozenset({"XMR", "TRX", "BTC"}),
        "domain_consensus": {},
    }
    base.update(kw)
    return RoutingTable(**base)


# ── domain consensus ────────────────────────────────────────────────────────

def test_domain_consensus_takes_the_majority_domain():
    rows = [("SPX", "stock")] * 118 + [("SPX", "macro")] * 84 + [("SPX", "crypto")] * 2
    assert build_domain_consensus(rows)["SPX"] == "stock"


def test_domain_consensus_collapses_macro_and_stock_for_routing():
    """Routing only needs 'is this crypto or not' — SPX splits stock/macro across theses
    and either answer sends it to Yahoo."""
    rows = [("DXY", "macro")] * 64
    assert build_domain_consensus(rows)["DXY"] == "macro"


# ── curated map wins, always ────────────────────────────────────────────────

def test_curated_mapping_beats_every_heuristic():
    table = _table(
        curated={"SPX": {"source": "yahoo", "symbol": "^GSPC"}},
        domain_consensus={"SPX": "crypto"},  # even a wrong consensus must not override
    )
    ref = route("SPX", table)
    assert isinstance(ref, OracleRef)
    assert (ref.source, ref.symbol, ref.curated) == ("yahoo", "^GSPC", True)
    assert ref.needs_validation is False


def test_curated_can_declare_an_asset_unpriceable():
    table = _table(curated={"BTC.D": {"unpriceable": "dominance_metric"}})
    result = route("BTC.D", table)
    assert isinstance(result, Unpriceable) and result.reason == "dominance_metric"


def test_curated_can_declare_an_asset_derived_from_two_others():
    """ETH/BTC is 29 corpus rows and both legs are already cached, so it is a division rather
    than a new source (§6f). See ``oracle.derived`` for how the bars are built."""
    table = _table(curated={"ETH/BTC": {"derived": {"numerator": "ETH", "denominator": "BTC"}}})
    ref = route("ETH/BTC", table)
    assert isinstance(ref, DerivedRef)
    assert (ref.asset, ref.numerator, ref.denominator) == ("ETH/BTC", "ETH", "BTC")


def test_a_slash_in_a_label_is_never_on_its_own_evidence_of_a_ratio():
    """Curated only. 'BTC/USD' is one instrument, not a ratio of two, and inferring pairs from
    punctuation is the same guessing this module exists to refuse — see the SPX collision."""
    table = _table(domain_consensus={"BTC/USD": "crypto"})
    result = route("BTC/USD", table)
    assert not isinstance(result, DerivedRef)
    assert isinstance(result, Unpriceable)


def test_an_unpriceable_declaration_still_wins_over_a_derived_one():
    """ALTBTC keeps ``unpriceable`` because its numerator is a basket, and the basket is the
    unpriceable half. Order matters if an entry ever carries both keys."""
    table = _table(curated={"ALTBTC": {
        "unpriceable": "derived_ratio",
        "derived": {"numerator": "ALTS", "denominator": "BTC"},
    }})
    assert isinstance(route("ALTBTC", table), Unpriceable)


# ── the collision guard (the reason this module exists) ─────────────────────

def test_spx_is_never_routed_to_the_coinbase_memecoin():
    """Coinbase lists SPX6900 under the symbol 'SPX'. The corpus has 204 SPX theses that
    mean the S&P 500. Auto-routing on symbol availability would silently price 5.5% of
    the corpus against a memecoin."""
    table = _table(domain_consensus={"SPX": "stock"})
    result = route("SPX", table)
    assert not (isinstance(result, OracleRef) and result.source == "coinbase")


@pytest.mark.parametrize("asset,consensus", [("SPX", "stock"), ("META", "stock"), ("IP", "crypto")])
def test_known_collisions_route_by_consensus_not_availability(asset, consensus):
    table = _table(domain_consensus={asset: consensus})
    result = route(asset, table)
    expected = "coinbase" if consensus == "crypto" else "yahoo"
    assert isinstance(result, OracleRef) and result.source == expected


def test_crypto_consensus_asset_absent_from_both_exchanges_is_unmapped():
    table = _table(domain_consensus={"WEIRDCOIN": "crypto"})
    result = route("WEIRDCOIN", table)
    assert isinstance(result, Unpriceable) and result.reason == "unmapped"


def test_asset_with_no_consensus_at_all_is_a_conflict_not_a_guess():
    """No corpus evidence for the domain means we cannot safely pick a source. Guessing
    is exactly the failure mode this module exists to prevent."""
    result = route("AMBIGUOUS", _table())
    assert isinstance(result, Unpriceable) and result.reason == CONFLICT


# ── auto-derivation for the long tail ───────────────────────────────────────

def test_crypto_prefers_coinbase_over_kraken():
    table = _table(domain_consensus={"BTC": "crypto"})
    ref = route("BTC", table)
    assert isinstance(ref, OracleRef) and (ref.source, ref.symbol) == ("coinbase", "BTC-USD")


def test_crypto_falls_back_to_kraken_when_coinbase_lacks_it():
    table = _table(domain_consensus={"XMR": "crypto"})
    ref = route("XMR", table)
    assert isinstance(ref, OracleRef) and (ref.source, ref.symbol) == ("kraken", "XMRUSD")


def test_uncurated_equity_is_routed_to_yahoo_but_flagged_for_validation():
    """A bare ticker off a transcript is only a guess until Yahoo confirms it resolves to
    a real instrument — so the fetch stage must still verify before trusting a price."""
    table = _table(domain_consensus={"TSLA": "stock"})
    ref = route("TSLA", table)
    assert isinstance(ref, OracleRef)
    assert (ref.source, ref.symbol) == ("yahoo", "TSLA")
    assert ref.needs_validation is True


# ── structural sentinels ────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", ["__basket__", "__macro__"])
def test_canon_sentinels_are_unpriceable(asset):
    result = route(asset, _table())
    assert isinstance(result, Unpriceable) and result.reason in {"basket", "macro"}


# ── structural backstop for labels that aren't tickers at all ───────────────

@pytest.mark.parametrize(
    "label",
    ["HARD_ASSETS", "SEMIS/AI", "US Rates/Inflation", "IRAN_CEASEFIRE", "COLLECTIBLES"],
)
def test_theme_and_event_labels_are_never_auto_routed(label):
    """The LLM lifts prose out of transcripts as an 'asset'. Yahoo will happily match some
    unrelated instrument for these, so anything not shaped like an exchange ticker must be
    curated or refused."""
    table = _table(domain_consensus={label: "stock"})
    result = route(label, table)
    assert isinstance(result, Unpriceable) and result.reason == CONFLICT


def test_plausible_ticker_shapes_still_pass_through():
    for label in ("TSLA", "BRK.B", "DX-Y"):
        table = _table(domain_consensus={label: "stock"})
        assert isinstance(route(label, table), OracleRef)


def test_curated_entry_survives_the_ticker_shape_guard():
    """`ETH/BTC` and `FED_FUNDS_RATE` are unticker-like but explicitly curated — the
    curated verdict must win, with its own specific reason, not a generic conflict."""
    table = _table(curated={"ETH/BTC": {"unpriceable": "derived_ratio"}})
    result = route("ETH/BTC", table)
    assert isinstance(result, Unpriceable) and result.reason == "derived_ratio"


def test_routing_is_deterministic():
    table = _table(domain_consensus={"BTC": "crypto"})
    assert route("BTC", table) == route("BTC", table)


# ── the committed config itself ─────────────────────────────────────────────
#
# These read cfg/ rather than a fixture on purpose. The defect they guard is a *curation*
# error, not a code one — nothing in route.py is wrong when two keys name one instrument, so
# only the real file can catch it.

# Two asset keys may legitimately share a symbol only when they are genuinely different things
# that happen to be priced off the same series. Each entry must say why, because the default
# reading of a shared symbol is "these are duplicates and one of them should be an alias".
INTENTIONAL_SHARED_SYMBOLS = {
    # A currency is not a currency pair. The corpus carries EUR/GBP crosses and British Pound
    # futures under the bare-currency keys, so folding them into the dollar pair would file a
    # cross as a USD trade. Safe today only because EUR and GBP are the *base* currency, so the
    # direction sense of the pair matches the currency — see USDCAD below for the case where
    # it does not.
    ("yahoo", "EURUSD=X"): {"EUR", "EURUSD"},
    ("yahoo", "GBPUSD=X"): {"GBP", "GBPUSD"},
}


def _curated_routes():
    import yaml
    from oracle.assemble import CONFIG_DIR
    raw = yaml.safe_load((CONFIG_DIR / "oracle_map.yaml").read_text())
    routes = raw.get("assets", raw)
    return {a: s for a, s in routes.items() if isinstance(s, dict) and "symbol" in s}


def _curated_unpriceable_reasons():
    import yaml
    from oracle.assemble import CONFIG_DIR
    raw = yaml.safe_load((CONFIG_DIR / "oracle_map.yaml").read_text())
    routes = raw.get("assets", raw)
    return {a: s["unpriceable"] for a, s in routes.items() if isinstance(s, dict) and "unpriceable" in s}


def test_every_curated_unpriceable_reason_belongs_to_a_named_group():
    """A reason spelled in cfg/oracle_map.yaml that route.py cannot name is invisible to
    setups_cli's tally — it falls into the catch-all `ungrouped` bucket instead of `computable`,
    `no route` or `not an instrument`. `no_venue` (PURR) did exactly this until route.py named
    it; this test is what stops the next new reason doing the same silently."""
    known = route_mod.NOT_AN_ASSET | route_mod.COMPUTABLE | route_mod.NO_ROUTE
    reasons = set(_curated_unpriceable_reasons().values())
    assert reasons <= known, f"unrecognized unpriceable reasons: {reasons - known}"


def test_jpy_stays_curated_unpriceable_until_the_invert_flag_lands():
    """`test_a_bare_currency_never_routes_to_a_pair_that_inverts_it` only looks at rows with a
    `symbol` key, so it is vacuous for an `unpriceable` row — it would not catch JPY being
    wrongly given a `symbol` route. This pins JPY's row directly: routing bare JPY to Yahoo's
    JPY=X, where JPY is the quote currency, would invert all 25 JPY theses (the CAD trap)."""
    reasons = _curated_unpriceable_reasons()
    assert reasons.get("JPY") == route_mod.NEEDS_INVERT


def test_no_two_asset_keys_route_to_one_instrument_unless_declared():
    """`SILVER` and `XAG` both routed to `SI=F`, so the queue offered one trade twice —
    identical in every number — burning two slots of a sitting and double-counting it in §4's
    mining. Eight symbols were reachable under seventeen keys. The fix is an alias in
    `cfg/assets.yaml` so the two spellings resolve to one asset *before* routing is consulted;
    this test is what stops the next one being added silently."""
    from collections import defaultdict
    shared = defaultdict(set)
    for asset, spec in _curated_routes().items():
        shared[(spec.get("source"), spec["symbol"])].add(asset)

    offenders = {
        sym: keys for sym, keys in shared.items()
        if len(keys) > 1 and INTENTIONAL_SHARED_SYMBOLS.get(sym) != keys
    }
    assert not offenders, (
        "asset keys sharing one instrument without a declared reason — alias them in "
        f"cfg/assets.yaml or add them to INTENTIONAL_SHARED_SYMBOLS: {offenders}"
    )


def test_a_bare_currency_never_routes_to_a_pair_that_inverts_it():
    """The USDCAD trap. `CAD` was routed to `USDCAD=X`, where CAD is the *quote* currency, so
    a bullish-CAD thesis ("equivalently bearish USDCAD", TTrades) was priced and scored as a
    bullish USDCAD one — the opposite trade. Direction is never flipped anywhere in the engine,
    so a bare currency may only route to a pair it is the *base* of."""
    for asset, spec in _curated_routes().items():
        symbol = spec["symbol"]
        if not symbol.endswith("=X") or len(asset) != 3:
            continue
        pair = symbol[:-2]
        assert pair.startswith(asset), (
            f"{asset} routes to {symbol}, where it is the quote currency — a long {asset} "
            f"thesis would score as a long {pair}, which is the opposite trade. Drop the "
            f"route or add an explicit inversion (§29)."
        )


# ── tradeable instrument: what an order goes to, when the priced thing can't be bought ──

def test_a_curated_entry_can_name_a_tradeable_instrument_apart_from_the_priced_one():
    """The Dow is priced on ^DJI because that is what the roster's theses are about, and
    traded as DIA because no venue in this repo lists an index. Two different questions,
    so two different fields — see the `pricing` paragraph in cfg/venue_map.yaml."""
    table = _table(curated={"DJI": {"source": "yahoo", "symbol": "^DJI", "tradeable": "DIA"}})
    ref = route("DJI", table)
    assert ref == OracleRef(
        asset="DJI", source="yahoo", symbol="^DJI", curated=True, tradeable="DIA"
    )
    assert ref.symbol == "^DJI"       # grading basis, unchanged
    assert ref.trade_symbol == "DIA"  # what a zone and an order are quoted on


def test_trade_symbol_falls_back_to_the_priced_symbol():
    """Every asset without a proxy must behave exactly as it did before, so `trade_symbol`
    is safe to read unconditionally rather than guarded at each call site."""
    table = _table(curated={"SPX": {"source": "yahoo", "symbol": "^GSPC"}})
    ref = route("SPX", table)
    assert ref.tradeable is None
    assert ref.trade_symbol == "^GSPC"


def test_an_uncurated_asset_has_no_tradeable_override():
    """`tradeable` is curation-only. Inferring "the ETF for this index" from a ticker string
    is the same guess that routes GOLD to Gold.com, Inc."""
    table = _table(domain_consensus={"HOOD": "stock"})
    assert route("HOOD", table).trade_symbol == "HOOD"


# ── wraps: the reverse key, for a wrapper fund's roster fold ────────────────────────────────

def test_load_wraps_on_a_file_with_no_wraps_section_returns_empty(tmp_path):
    """Guards the two `assets:`-only fixtures above (`:318`, `:331`) — a loader that requires
    the new section would break every table built before this ticket."""
    (tmp_path / "oracle_map.yaml").write_text("assets:\n  BTC: {source: coinbase, symbol: BTC-USD}\n")
    assert route_mod.load_wraps(tmp_path) == {}


def test_load_wraps_reads_the_section(tmp_path):
    (tmp_path / "oracle_map.yaml").write_text(
        "assets: {}\nwraps:\n  HODL: BTC\n  ETHB: ETH\n"
    )
    assert route_mod.load_wraps(tmp_path) == {"HODL": "BTC", "ETHB": "ETH"}


def _wraps():
    from oracle.assemble import CONFIG_DIR
    return route_mod.load_wraps(CONFIG_DIR)


def test_wraps_keys_resolve_to_themselves():
    """`build_readings` looks a wrap up by the canonical asset `resolve_asset` already produced
    — so this map is keyed on canonical identity, not on the raw ticker. If `HODL` ever entered
    `cfg/assets.yaml` or `cfg/tickers.json` as an alias for something else, `resolve_asset`
    would stop returning `HODL` for `HODL` and the wrap would go dead with nothing failing —
    the row would quietly revert to the `silent` this ticket exists to fix. Pinning today's
    identity turns that into a loud failure instead."""
    from core.canon import load_registry, resolve_asset
    from oracle.assemble import CONFIG_DIR
    registry = load_registry(CONFIG_DIR)
    for key in _wraps():
        assert resolve_asset(key, registry)[0] == key, (
            f"{key} no longer resolves to itself — the wrap in cfg/oracle_map.yaml is dead"
        )


def test_wraps_targets_are_routable():
    """The fold a wrapper borrows has to come from an asset this engine can actually price and
    route — a typo'd target would attach a real-looking `via` suffix to nothing."""
    table = route_mod.the_routing_table()
    for target in _wraps().values():
        assert isinstance(route(target, table), route_mod.Priceable), (
            f"{target} does not route — a wraps: target must be a priceable asset"
        )


@pytest.mark.needs_ore
def test_wraps_keys_have_no_theses_of_their_own():
    """The curation test, stated as code: a `wraps:` entry only belongs where the wrapper never
    speaks for itself. The day `HODL` gets its own thesis, replacing its fold with BTC's would
    bury a real view rather than filling a silence."""
    from core.canon import load_registry
    from oracle.assemble import CONFIG_DIR
    from oracle.corpus import iter_rows
    covered = {row.asset for row in iter_rows(load_registry(CONFIG_DIR))}
    for key in _wraps():
        assert key not in covered, f"{key} now has its own theses — reconsider the wrap"


@pytest.mark.needs_ore
def test_wraps_targets_have_theses_to_lend():
    """The mirror of the test above: a target with nothing in the corpus lends a wrapper
    silence dressed up as a borrowed fold, which is indistinguishable from a mistyped target."""
    from core.canon import load_registry
    from oracle.assemble import CONFIG_DIR
    from oracle.corpus import iter_rows
    covered = {row.asset for row in iter_rows(load_registry(CONFIG_DIR))}
    for key, target in _wraps().items():
        assert target in covered, f"{target} has no theses of its own — {key} would borrow silence"


def test_wraps_is_not_the_inverse_of_tradeable():
    """`tradeable` and `wraps:` answer different questions and must never be derived from one
    another. `IWM` is `tradeable` for RUT and still carries its own 7 theses
    (`cfg/oracle_map.yaml:59-60`) — deriving `wraps:` from `tradeable` would merge Russell
    sentiment into IWM's."""
    tradeable_targets = {
        spec["tradeable"] for spec in _curated_routes().values() if spec.get("tradeable")
    }
    offenders = set(_wraps()) & tradeable_targets
    assert not offenders, (
        f"{offenders} are both a `tradeable` target and a `wraps:` key — IWM's own theses show "
        "why that reverse relation is not the same as `wraps:`"
    )


# ── the one correct way to build the table ──────────────────────────────────────────────────

def test_the_routing_table_helper_carries_all_three_inputs(tmp_path):
    """The failure this exists to stop is silent, not loud. A table missing its domain consensus
    still routes — it just refuses most of the corpus as ``conflict``, which reads as an engine
    fault. Three probes were debugged past that before the helper existed, so the test asserts
    the inputs actually arrive rather than that the call returns something.

    ``wraps`` is a fourth input added alongside this ticket — an input arriving unasserted is
    exactly the silent-stub failure this test exists to catch, so it is pinned here too.
    """
    (tmp_path / "oracle_map.yaml").write_text(
        "assets:\n  BTC: {source: coinbase, symbol: BTC-USD}\nwraps:\n  HODL: BTC\n"
    )
    rows = [SimpleNamespace(asset="ETH", domain="crypto"),
            SimpleNamespace(asset="ETH", domain="crypto")]
    table = route_mod.the_routing_table(
        tmp_path, rows=rows, listings_map={"coinbase": ["ETH-USD"], "kraken": []})
    assert table.curated                      # cfg reached it
    assert table.domain_consensus == {"ETH": "crypto"}   # corpus rows reached it
    assert "ETH-USD" in table.coinbase_symbols          # listings reached it
    assert table.wraps == {"HODL": "BTC"}               # the wraps section reached it


def test_a_table_without_domain_consensus_refuses_rather_than_erroring(tmp_path):
    """Why the helper is worth having: this is what a stubbed input produces, and nothing about
    it looks like a mistake at the call site."""
    (tmp_path / "oracle_map.yaml").write_text("assets: {}\n")
    stubbed = route_mod.load_routing_table(
        tmp_path, [], listings={"coinbase": [], "kraken": []})
    assert isinstance(route_mod.route("ETH", stubbed), route_mod.Unpriceable)
