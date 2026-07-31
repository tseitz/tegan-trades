from oracle.instruments import alias_map
from oracle.route import RoutingTable

STOCKS = {a: "stock" for a in
          ("RUT", "IWM", "DJI", "DIA", "EUR", "EURUSD", "SPY", "QQQ", "AAPL")}


def _table(**kw):
    base = dict(
        curated={},
        coinbase_symbols=frozenset({"BTC", "ETH"}),
        kraken_symbols=frozenset(),
        domain_consensus=STOCKS | {"BTC": "crypto", "ETH": "crypto"},
    )
    base.update(kw)
    return RoutingTable(**base)


def _venues(coverage):
    return lambda asset: list(coverage.get(asset, ()))


# ── the curated `tradeable` case: two labels, one order ─────────────────────

def test_an_index_priced_on_its_fund_is_an_alias_of_that_fund():
    table = _table(curated={"RUT": {"source": "yahoo", "symbol": "^RUT", "tradeable": "IWM"}})
    aliases = alias_map(["RUT", "IWM"], table,
                        venues_for=_venues({"RUT": ["alpaca"],
                                            "IWM": ["alpaca", "lighter", "aster"]}))
    assert aliases == {"RUT": "IWM"}


def test_the_label_that_reaches_more_venues_is_the_one_kept():
    """The label decides the venue lookup, so folding IWM into RUT would forfeit Lighter and
    Aster on a trade that can reach them — a dedupe that quietly narrows execution."""
    table = _table(curated={"DJI": {"source": "yahoo", "symbol": "^DJI", "tradeable": "DIA"}})
    aliases = alias_map(["DJI", "DIA"], table,
                        venues_for=_venues({"DJI": ["alpaca"], "DIA": []}))
    assert aliases == {"DIA": "DJI"}


def test_equal_coverage_resolves_alphabetically_rather_than_by_iteration_order():
    """Stability is the whole requirement: the label sets the decision key, so it must not
    depend on the order assets happened to arrive in."""
    table = _table(curated={
        "EUR": {"source": "yahoo", "symbol": "EURUSD=X"},
        "EURUSD": {"source": "yahoo", "symbol": "EURUSD=X"},
    })
    coverage = _venues({})
    assert alias_map(["EUR", "EURUSD"], table, venues_for=coverage) == {"EURUSD": "EUR"}
    assert alias_map(["EURUSD", "EUR"], table, venues_for=coverage) == {"EURUSD": "EUR"}


# ── what must NOT be merged ─────────────────────────────────────────────────

def test_a_label_with_no_twin_gets_no_entry():
    """The map carries aliases only. An identity entry for every asset would make every caller
    unable to tell "this was folded" from "this was seen"."""
    table = _table()
    assert alias_map(["SPY", "QQQ", "AAPL"], table, venues_for=_venues({})) == {}


def test_the_same_symbol_on_two_sources_is_two_instruments():
    """``LINK`` on Yahoo is Interlink Electronics and ``LINK`` on Coinbase is Chainlink. The
    identity is (source, symbol) — a bare symbol match is how this repo prices a memecoin as
    the S&P."""
    table = _table(
        curated={"LINKY": {"source": "yahoo", "symbol": "LINK"}},
        domain_consensus={"LINKY": "stock", "LINK": "crypto"},
        coinbase_symbols=frozenset({"LINK"}),
    )
    assert alias_map(["LINK", "LINKY"], table, venues_for=_venues({})) == {}


def test_an_unpriceable_label_is_not_an_alias_of_anything():
    """Two assets that both fail to route share a reason, not an instrument."""
    table = _table(curated={
        "HARD_ASSETS": {"unpriceable": "basket"},
        "SEMIS": {"unpriceable": "basket"},
    })
    assert alias_map(["HARD_ASSETS", "SEMIS"], table, venues_for=_venues({})) == {}


def test_a_derived_ratio_is_not_folded_into_its_numerator():
    """``ETH/BTC`` is computed from two series and has no ``(source, symbol)`` of its own —
    reading one off it is the crash ``plan_fetches`` already took once."""
    table = _table(curated={
        "ETH/BTC": {"derived": {"numerator": "ETH", "denominator": "BTC"}},
    })
    assert alias_map(["ETH/BTC", "ETH"], table, venues_for=_venues({})) == {}


# ── the grading basis is untouched ──────────────────────────────────────────

def test_two_indices_that_merely_share_a_venue_symbol_are_not_aliases():
    """Aliasing is about the *priced* instrument. Two different indices that both reach Alpaca
    as the same ETF would be a venue-map bug, and folding them here would hide it."""
    table = _table(curated={
        "RUT": {"source": "yahoo", "symbol": "^RUT", "tradeable": "IWM"},
        "RUSSELL2000": {"source": "yahoo", "symbol": "^RUT"},
    })
    aliases = alias_map(["RUT", "RUSSELL2000"], table, venues_for=_venues({}))
    assert aliases == {}


# ── against the committed config ────────────────────────────────────────────

def _live():
    """The curated map as shipped, against the bare tickers the venue map trades.

    Both halves are needed and only one is in ``oracle_map.yaml``: ``URANIUM`` is curated onto
    ``URA`` while ``URA`` itself routes to Yahoo off corpus domain consensus, so a table built
    from the curated file alone cannot see that pair at all. ``tradeable`` targets are added for
    the same reason — ``DIA`` is named by ``DJI`` and is otherwise nowhere.
    """
    from oracle import venue_map
    from oracle.route import load_curated

    curated = load_curated(venue_map.CFG_PATH.parent)
    bare = set(venue_map.load()) | {
        entry["tradeable"] for entry in curated.values()
        if isinstance(entry, dict) and entry.get("tradeable")
    }
    table = RoutingTable(
        curated=curated,
        coinbase_symbols=frozenset(),
        kraken_symbols=frozenset(),
        domain_consensus={t: "stock" for t in bare},
    )
    return sorted(set(curated) | bare), table, venue_map


def test_the_committed_config_folds_exactly_the_pairs_it_double_offers_today():
    """Pinned exactly, not by membership: a *new* merge is the thing worth catching. Two labels
    landing on one instrument by accident would silently join their supporters into one
    agreement count, which is a wrong number rather than a redundant row."""
    labels, table, venue_map = _live()
    assert alias_map(labels, table, venues_for=venue_map.venues_for) == {
        "DIA": "DJI",           # no venue carries a Dow; DJI's alpaca row is the only way in
        "EURUSD": "EUR",
        "GBPUSD": "GBP",
        "RUT": "IWM",           # IWM reaches three venues, RUT only Alpaca
        "URANIUM": "URA",       # the concept label and the fund it was always priced on
    }, "a new alias pair appeared — confirm the two labels really are one trade"


def test_folded_labels_do_not_disagree_with_their_survivor_about_a_venue_symbol():
    """The survivor's venue row is the one the merged trade is placed on, so a pair that
    disagreed would silently reroute the order to the other label's instrument. Nothing in
    ``alias_map`` can detect that — it is a fact about ``cfg/venue_map.yaml``."""
    labels, table, venue_map = _live()
    for folded, survivor in alias_map(labels, table, venues_for=venue_map.venues_for).items():
        for venue in venue_map.venues_for(folded):
            theirs = venue_map.listing(survivor, venue)
            if theirs is None:
                continue
            assert venue_map.listing(folded, venue).symbol == theirs.symbol, (
                f"{folded} and {survivor} are one instrument but name different {venue} "
                f"symbols — merging them would place the order on the wrong one"
            )


