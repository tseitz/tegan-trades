from datetime import date, timedelta
from types import SimpleNamespace

import review.cli as cli
from core.canon import load_registry
from core.review import (
    LEVELS_LED,
    NO_READ,
    NO_VIEW,
    SENTIMENT_LED,
    UNREADABLE,
    Holding,
    Location,
    Reading,
    RosterLean,
)
from oracle.portfolios import Benchmark, Mandate, Portfolio, Position
from oracle.route import RoutingTable
from oracle.series import Bar, PriceSeries
from review.cli import CONFIG_DIR, build_readings, load_books, refresh_argv, review_for

AS_OF = date(2025, 6, 30)
REGISTRY = load_registry(CONFIG_DIR)


def _series(symbol="VTI", *, source="yahoo", bars=400, start=100.0):
    """A long, gently trending series — enough history for weekly swings, a dealing range
    and at least one order block to exist."""
    out = []
    day = AS_OF - timedelta(days=bars)
    price = start
    for i in range(bars):
        price += 0.9 if (i // 15) % 2 == 0 else -0.6
        out.append(Bar(date=day + timedelta(days=i), open=price, high=price + 1.5,
                       low=price - 1.5, close=price))
    return PriceSeries(symbol=symbol, source=source, bars=tuple(out))


def _table(**consensus):
    return RoutingTable(curated={}, coinbase_symbols=frozenset(),
                        kraken_symbols=frozenset(), domain_consensus=consensus)


MANDATE = Mandate(name="test", benchmarks=(Benchmark(type="held_flat"),),
                  horizon="position", risk_posture="moderate")


def _book(*tickers, domain="stock"):
    return Portfolio(
        name="test", mandate=MANDATE,
        positions=tuple(
            Position(holding=Holding(ticker=t, shares=1.0, cost=None), domain=domain)
            for t in tickers
        ),
    )


def _folded(person, lean, *, published_at="2025-06-01"):
    return SimpleNamespace(
        person_canonical=person, asset_canonical="VTI",
        current=SimpleNamespace(lean=lean,
                                source=SimpleNamespace(published_at=published_at)),
    )


def test_a_priced_holding_gets_a_price_and_a_weekly_trend():
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(VTI="stock"), folded_by_asset={}, as_of=AS_OF,
        series_cache={"VTI": _series()},
    )
    assert len(readings) == 1
    assert readings[0].price is not None
    assert readings[0].weekly_trend is not None


def test_an_unroutable_holding_is_reported_not_dropped():
    """Silently skipping it would leave a position you own out of a review of what you own —
    the one failure this whole command exists to prevent."""
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(), folded_by_asset={}, as_of=AS_OF, series_cache={},
    )
    assert [r.holding.ticker for r in readings] == ["VTI"]
    assert readings[0].price is None
    assert readings[0].location.where == UNREADABLE


def test_a_routable_but_uncached_holding_is_also_reported():
    """'Nobody has fetched this yet' and 'this is not an instrument' are opposite problems
    with opposite fixes, and both end here as a row with no price rather than as no row."""
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(VTI="stock"), folded_by_asset={}, as_of=AS_OF,
        series_cache={"VTI": None},
    )
    assert readings[0].price is None


def test_the_roster_split_reaches_the_reading():
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(VTI="stock"), as_of=AS_OF,
        folded_by_asset={"VTI": [_folded("A", "bearish"), _folded("B", "bearish")]},
        series_cache={"VTI": _series()},
    )
    assert readings[0].roster.bears == 2


def test_an_asset_with_no_stances_reads_as_silent_not_as_an_error():
    readings, _ = build_readings(
        _book("VTI"), registry=REGISTRY, table=_table(VTI="stock"), folded_by_asset={}, as_of=AS_OF,
        series_cache={"VTI": _series()},
    )
    assert readings[0].verdict == NO_VIEW


def test_readings_come_back_in_file_order():
    """Ranking is the renderer's job. Reordering here too would give two places that decide
    what you look at first, and they would drift."""
    readings, _ = build_readings(
        _book("AAA", "BBB", "CCC"), registry=REGISTRY, table=_table(), folded_by_asset={}, as_of=AS_OF,
        series_cache={},
    )
    assert [r.holding.ticker for r in readings] == ["AAA", "BBB", "CCC"]


def test_the_structure_each_reading_was_drawn_from_comes_back_alongside_it():
    """Scanning for levels needs it, and rebuilding structure is the expensive half of the
    loop. One entry per position, aligned with the readings, `None` where nothing priced."""
    result = build_readings(
        _book("VTI", "NOPE"), registry=REGISTRY, table=_table(VTI="stock"),
        folded_by_asset={}, as_of=AS_OF, series_cache={"VTI": _series()},
    )
    assert len(result.contexts) == len(result.readings) == 2
    assert result.contexts[0] is not None
    assert result.contexts[1] is None


# ── warming prices before reading them ─────────────────────────────────────


def test_a_refresh_fetches_only_what_the_account_holds():
    """The corpus pass is ~300 assets and none of them are yours. A midday price check that
    walked it would take minutes to answer a question about 77 tickers."""
    argv = refresh_argv("retirement")
    assert "--held-only" in argv
    assert argv[argv.index("--portfolio") + 1] == "retirement"


def test_a_refresh_skips_the_hourly_pass():
    """`review` draws on daily and weekly bars only. The hourly series exists for `setups`'
    entry trigger, and warming it here would roughly double the wait for nothing on screen."""
    assert "--no-intraday" in refresh_argv("retirement")


# ── review_for: the typed result the terminal and digest both read ─────────


def _reading(ticker, *, price=100.0):
    return Reading(
        holding=Holding(ticker=ticker, shares=1.0, cost=None),
        roster=RosterLean(lean="silent", bulls=0, bears=0, people=0, newest=None,
                          age_days=None, voices=(), thin=False),
        location=Location(where=UNREADABLE, basis="none"),
        verdict=NO_VIEW, price=price, weekly_trend=None,
    )


def test_review_for_bundles_mismatch_and_levels_onto_the_result(monkeypatch):
    """`review_for` is the seam both `digest` and the terminal read from — this asserts the
    bundle actually carries mismatch and levels, not just readings, which `build_readings`'s
    own tests already cover on their own. `chains`/`macro` come back empty here since no
    `altsignal_cfg` was supplied, matching what `digest` asks for today.
    """
    monkeypatch.setattr(cli.corpus, "iter_rows", lambda registry: iter(()))
    monkeypatch.setattr(cli.listings, "load_or_fetch", lambda path: {})
    monkeypatch.setattr(cli, "load_all_stances", lambda: [])

    reading = _reading("VTI", price=100.0)
    monkeypatch.setattr(
        cli, "build_readings",
        lambda book, **_: cli.Read(readings=[reading], contexts=(None,)))

    book = Portfolio(name="test", mandate=MANDATE,
                     positions=(Position(holding=reading.holding, domain="stock", mark=50.0),))

    [result] = review_for([book], as_of=AS_OF, registry=REGISTRY)
    assert result.book is book
    assert result.readings == [reading]
    # No context came back from the stubbed `build_readings`, so nothing is standing on or
    # closing in on a level — but the shape must still be the full, uncapped triple.
    assert result.levels == ((), (), 0)
    assert [ticker for ticker, _, _ in result.mismatched] == ["VTI"]
    assert result.chains == () and result.macro == ()
    # No `data/transactions/test.json` exists, so the trailing field rides on the result as
    # `None` rather than the caller having to know to ask for it separately.
    assert result.history is None


def test_load_books_skips_a_bad_file_and_reports_it(monkeypatch):
    """The seam `digest` calls instead of reaching into `oracle.portfolios` directly — see
    ADR-0004. One bad file must not cost every other account's review."""
    good = Portfolio(name="good", mandate=MANDATE, positions=())

    def _load(name):
        if name == "bad":
            raise cli.portfolios.PortfolioError("bad.yaml: malformed")
        return good

    monkeypatch.setattr(cli.portfolios, "available", lambda: ("good", "bad"))
    monkeypatch.setattr(cli.portfolios, "load", _load)

    warnings = []
    assert load_books(warn=warnings.append) == [good]
    assert warnings == ["portfolio 'bad' was skipped — bad.yaml: malformed"]


def test_load_books_needs_no_warn_callback(monkeypatch):
    """`warn` is optional — a bad file is still skipped, just silently, for a caller that does
    not care to report it."""
    good = Portfolio(name="good", mandate=MANDATE, positions=())

    def _load(name):
        if name == "bad":
            raise cli.portfolios.PortfolioError("bad.yaml: malformed")
        return good

    monkeypatch.setattr(cli.portfolios, "available", lambda: ("good", "bad"))
    monkeypatch.setattr(cli.portfolios, "load", _load)
    assert load_books() == [good]


CONSERVATIVE = Mandate(name="c", benchmarks=(Benchmark(type="held_flat"),),
                       horizon="position", risk_posture="conservative")


def test_review_for_carries_leads_with_through_to_the_reading(monkeypatch):
    """Seam 3. Without `build_readings` passing `book.mandate.leads_with` into `review()`,
    every reading would silently stay sentiment-led no matter what the file says, and every
    other test in this module — including `test_a_priced_holding_...` above — would still
    pass. Every asset is forced unpriceable so the assertion holds regardless of what is
    actually cached on disk; this test is about `leads_with`, not about pricing.
    """
    monkeypatch.setattr(cli.corpus, "iter_rows", lambda registry: iter(()))
    monkeypatch.setattr(cli.listings, "load_or_fetch", lambda path: {})
    monkeypatch.setattr(cli, "load_all_stances", lambda: [])
    monkeypatch.setattr(cli, "route", lambda asset, table: object())

    levels_book = Portfolio(name="levels", mandate=CONSERVATIVE, positions=_book("VTI").positions)
    sentiment_book = _book("VTI")  # MANDATE's risk_posture is "moderate" — sentiment-led

    [levels_result] = review_for([levels_book], as_of=AS_OF, registry=REGISTRY)
    [sentiment_result] = review_for([sentiment_book], as_of=AS_OF, registry=REGISTRY)

    assert levels_result.readings[0].leads_with == LEVELS_LED
    assert levels_result.readings[0].verdict == NO_READ
    assert sentiment_result.readings[0].leads_with == SENTIMENT_LED
    assert sentiment_result.readings[0].verdict == NO_VIEW


def test_main_drops_the_standing_group_on_a_levels_led_book_unless_dash_levels(monkeypatch):
    """ADR-0002's fold happens in `main()`, not the view — this is the one place that decides
    what fits on a screen, and it has to drop the standing group before `cap()` so `suppressed`
    stays honest about what a run is actually withholding. `--levels` means "show me
    everything" and must keep meaning that."""
    reading = _reading("VTI")
    book = Portfolio(name="test", mandate=CONSERVATIVE,
                     positions=(Position(holding=reading.holding, domain="stock"),))
    result = cli.ReviewResult(book=book, readings=[reading], contexts=(None,),
                              mismatched=(), levels=(("standing-sentinel",), (), 0),
                              chains=(), macro=())

    monkeypatch.setattr(cli.portfolios, "load", lambda name: book)
    monkeypatch.setattr(cli.altsignal_config, "load", lambda path: None)
    monkeypatch.setattr(cli, "review_for", lambda books, **kw: [result])

    calls = []
    monkeypatch.setattr(
        cli, "render_levels",
        lambda standing, closing, suppressed, **kw: calls.append(
            (standing, closing, suppressed)) or "LEVELS")

    assert cli.main(["test"]) == 0
    standing, _closing, suppressed = calls[-1]
    assert standing == ()
    assert suppressed == 0

    assert cli.main(["test", "--levels"]) == 0
    standing, _closing, suppressed = calls[-1]
    assert standing == ("standing-sentinel",)
    assert suppressed == 0
