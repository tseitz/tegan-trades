from datetime import date

from oracle.plan import FetchJob, plan_fetches
from oracle.route import OracleRef, RoutingTable, Unpriceable


def _table(**kw):
    base = {
        "curated": {},
        "coinbase_symbols": frozenset({"BTC", "ETH"}),
        "kraken_symbols": frozenset({"XMR"}),
        "domain_consensus": {"BTC": "crypto", "ETH": "crypto", "XMR": "crypto", "TSLA": "stock"},
    }
    base.update(kw)
    return RoutingTable(**base)


def _rows(*pairs):
    """(asset, published_at) -> objects shaped like corpus.CorpusRow."""
    from types import SimpleNamespace

    return [SimpleNamespace(asset=a, published_at=d) for a, d in pairs]


TODAY = date(2026, 7, 24)


def _asset_jobs(jobs):
    """Drop the auto-added benchmark jobs — these tests are about per-asset planning."""
    return [j for j in jobs if not j.ref.asset.startswith("__benchmark__")]


def test_start_date_is_per_asset_not_corpus_wide():
    """Absent a floor, an asset first mentioned recently doesn't need two years of history —
    fetching the corpus-wide span for every symbol would multiply the backfill for no benefit.

    ``floor_start`` is what overrides this when structure lookback is wanted; see the tests
    at the bottom of this file."""
    rows = _rows(("BTC", "2024-08-01"), ("ETH", "2026-06-01"))
    jobs, _ = plan_fetches(rows, _table(), today=TODAY, pad_days=0)
    by_asset = {j.ref.asset: j for j in jobs}
    assert by_asset["BTC"].start == date(2024, 8, 1)
    assert by_asset["ETH"].start == date(2026, 6, 1)


def test_end_date_is_today_since_future_prices_do_not_exist():
    jobs, _ = plan_fetches(_rows(("BTC", "2024-08-01")), _table(), today=TODAY)
    assert jobs[0].end == TODAY


def test_one_job_per_asset_not_per_thesis():
    rows = _rows(*[("BTC", "2025-01-01")] * 50)
    jobs, _ = plan_fetches(rows, _table(), today=TODAY)
    assert len(_asset_jobs(jobs)) == 1


def test_unpriceable_assets_are_reported_not_silently_dropped():
    rows = _rows(("BTC", "2025-01-01"), ("__basket__", "2025-01-01"))
    jobs, skipped = plan_fetches(rows, _table(), today=TODAY)
    assert [j.ref.asset for j in _asset_jobs(jobs)] == ["BTC"]
    assert [s.asset for s in skipped] == ["__basket__"]
    assert all(isinstance(s, Unpriceable) for s in skipped)


def test_undated_theses_do_not_poison_the_start_date():
    """An empty published_at sorts before every real ISO date; taking a naive min would
    drag the fetch window back to the epoch. Mirrors core.rank.corpus_span."""
    rows = _rows(("BTC", ""), ("BTC", "2025-03-01"))
    jobs, _ = plan_fetches(rows, _table(), today=TODAY, pad_days=0)
    assert jobs[0].start == date(2025, 3, 1)


def test_asset_with_only_undated_theses_is_skipped():
    jobs, skipped = plan_fetches(_rows(("BTC", "")), _table(), today=TODAY)
    assert jobs == []
    assert [s.reason for s in skipped] == ["undated"]


def test_start_is_padded_back_for_the_entry_bar():
    """Entry is the close on publish day. If that's a weekend/holiday the series must
    already contain the preceding bar, so the window opens a few days early."""
    rows = _rows(("TSLA", "2025-03-10"))
    jobs, _ = plan_fetches(rows, _table(), today=TODAY, pad_days=5)
    assert jobs[0].start == date(2025, 3, 5)


def test_cached_span_coverage_skips_the_fetch():
    rows = _rows(("BTC", "2025-01-01"))
    cached = {("coinbase", "BTC-USD"): (date(2024, 12, 1), TODAY)}
    jobs, _ = plan_fetches(rows, _table(), today=TODAY, cached_spans=cached)
    assert _asset_jobs(jobs) == []


def test_partial_cache_still_refetches():
    rows = _rows(("BTC", "2025-01-01"))
    cached = {("coinbase", "BTC-USD"): (date(2025, 6, 1), TODAY)}  # missing the early half
    jobs, _ = plan_fetches(rows, _table(), today=TODAY, cached_spans=cached)
    assert len(_asset_jobs(jobs)) == 1


def test_jobs_are_ordered_deterministically():
    rows = _rows(("ETH", "2025-01-01"), ("BTC", "2025-01-01"), ("XMR", "2025-01-01"))
    jobs = _asset_jobs(plan_fetches(rows, _table(), today=TODAY)[0])
    assert [j.ref.asset for j in jobs] == sorted(j.ref.asset for j in jobs)
    assert all(isinstance(j, FetchJob) for j in jobs)
    assert all(isinstance(j.ref, OracleRef) for j in jobs)


# ── benchmarks must span the whole corpus ───────────────────────────────────

def test_benchmark_spans_the_corpus_not_its_own_mentions():
    """^GSPC is both a benchmark and an ordinary asset. Windowing it to its own first
    mention silently strips the benchmark off every earlier stock/macro call."""
    rows = _rows(("BTC", "2024-07-31"), ("SPX", "2025-09-25"))
    table = _table(
        curated={"SPX": {"source": "yahoo", "symbol": "^GSPC"}},
        domain_consensus={"BTC": "crypto", "SPX": "stock"},
    )
    jobs, _ = plan_fetches(rows, table, today=TODAY, pad_days=0)
    gspc = next(j for j in jobs if j.ref.symbol == "^GSPC")
    assert gspc.start == date(2024, 7, 31)


def test_unmentioned_benchmark_is_still_fetched():
    """A corpus with no SPX theses at all still needs ^GSPC to score its stock calls."""
    jobs, _ = plan_fetches(_rows(("BTC", "2024-07-31")), _table(), today=TODAY, pad_days=0)
    assert any(j.ref.symbol == "^GSPC" and j.start == date(2024, 7, 31) for j in jobs)


def test_benchmark_not_refetched_when_already_fully_cached():
    cached = {
        ("yahoo", "^GSPC"): (date(2024, 1, 1), TODAY),
        ("coinbase", "BTC-USD"): (date(2024, 1, 1), TODAY),
    }
    jobs, _ = plan_fetches(_rows(("BTC", "2024-07-31")), _table(), today=TODAY,
                           cached_spans=cached)
    assert jobs == []


# ── derived assets are computed, not fetched — but their legs must exist ─────

_DERIVED = {"ETH/BTC": {"derived": {"numerator": "ETH", "denominator": "BTC"}}}


def test_a_derived_asset_is_reported_as_skipped_rather_than_crashing_the_run():
    """Adding ``DerivedRef`` to the ``Route`` union broke this: the loop read
    ``resolved.source`` on anything that wasn't ``Unpriceable`` and raised AttributeError,
    killing the whole `fetch-prices` run — which the nightly job depends on."""
    jobs, skipped = plan_fetches(
        _rows(("ETH/BTC", "2026-01-01")), _table(curated=_DERIVED), today=TODAY, cached_spans={})
    assert [s.asset for s in skipped if s.reason == "derived"] == ["ETH/BTC"]
    assert "ETH/BTC" not in {j.ref.asset for j in jobs}


def test_a_derived_assets_legs_are_planned_even_when_nobody_holds_a_thesis_on_them():
    """A leg need not be a corpus asset at all — nothing requires a BTC thesis for ``ETH/BTC``
    to need BTC's bars. Without this the ratio could never be built."""
    jobs, _ = plan_fetches(
        _rows(("ETH/BTC", "2026-01-01")), _table(curated=_DERIVED), today=TODAY, cached_spans={})
    assert {"ETH", "BTC"} <= {j.ref.asset for j in _asset_jobs(jobs)}


def test_a_leg_with_a_later_mention_is_widened_to_the_ratios_window():
    """Otherwise the leg is fetched from its own first mention and the divided series is too
    short at the front — a silently truncated ratio rather than a missing one."""
    jobs, _ = plan_fetches(
        _rows(("ETH/BTC", "2024-01-01"), ("BTC", "2026-06-01")),
        _table(curated=_DERIVED), today=TODAY, cached_spans={})
    btc = next(j for j in _asset_jobs(jobs) if j.ref.asset == "BTC")
    assert btc.start < date(2024, 1, 2)


def test_a_legs_own_earlier_mention_is_not_narrowed_by_the_ratio():
    jobs, _ = plan_fetches(
        _rows(("ETH/BTC", "2026-06-01"), ("BTC", "2024-01-01")),
        _table(curated=_DERIVED), today=TODAY, cached_spans={})
    btc = next(j for j in _asset_jobs(jobs) if j.ref.asset == "BTC")
    assert btc.start < date(2024, 1, 2)


# ── a tradeable proxy needs BOTH series cached ───────────────────────────────

def test_a_tradeable_proxy_plans_the_priced_symbol_and_the_traded_one():
    """Two consumers, two symbols: `score-roster` grades on ^DJI, `setups` draws zones on
    DIA. Planning only the priced symbol leaves setups with no bars and the asset counted
    as `assets_uncached` — a silent skip, not an error."""
    table = _table(curated={"DJI": {"source": "yahoo", "symbol": "^DJI", "tradeable": "DIA"}})
    jobs, _ = plan_fetches(_rows(("DJI", "2026-02-17")), table, today=TODAY, pad_days=0)
    symbols = {j.ref.symbol for j in _asset_jobs(jobs)}
    assert symbols == {"^DJI", "DIA"}
    assert all(j.start == date(2026, 2, 17) for j in _asset_jobs(jobs)), \
        "both legs need the same history or the zone and the grade disagree on dates"


def test_the_traded_leg_is_not_itself_marked_tradeable():
    """Otherwise the job carries a `tradeable` pointing at itself, and the next reader of
    `trade_symbol` on a fetch job resolves DIA -> DIA -> ... reading as a proxy chain."""
    table = _table(curated={"DJI": {"source": "yahoo", "symbol": "^DJI", "tradeable": "DIA"}})
    jobs, _ = plan_fetches(_rows(("DJI", "2026-02-17")), table, today=TODAY, pad_days=0)
    traded = next(j for j in _asset_jobs(jobs) if j.ref.symbol == "DIA")
    assert traded.ref.tradeable is None
    assert traded.ref.needs_validation is False   # curated, so never probed


def test_an_already_cached_traded_leg_is_not_refetched():
    table = _table(curated={"DJI": {"source": "yahoo", "symbol": "^DJI", "tradeable": "DIA"}})
    spans = {("yahoo", "DIA"): (date(2026, 1, 1), TODAY)}
    jobs, _ = plan_fetches(
        _rows(("DJI", "2026-02-17")), table, today=TODAY, pad_days=0, cached_spans=spans
    )
    assert {j.ref.symbol for j in _asset_jobs(jobs)} == {"^DJI"}


# ── floor_start: history for structure, not just for grading ─────────────────
#
# The window this module planned was built for GRADING, which reads bars FORWARD from a
# publish date — so opening at the earliest mention was right. Structure reads BACKWARD:
# weekly trend, dealing range and order blocks all need lookback that predates the first
# time anyone mentioned the asset. Measured 2026-08-10, before this existed: 95 of 329
# cached series held under 90 daily bars, and those assets refuse as `no_dealing_range`
# when the truth is that nobody fetched their history.

def test_floor_start_deepens_a_recently_mentioned_asset():
    """The whole point: an asset first mentioned last month still needs a year of bars
    behind it, or its weekly structure cannot be read at all."""
    rows = _rows(("TSLA", "2026-06-01"))
    jobs, _ = plan_fetches(
        rows, _table(), today=TODAY, pad_days=0, floor_start=date(2024, 8, 1)
    )
    assert _asset_jobs(jobs)[0].start == date(2024, 8, 1)


def test_floor_start_never_narrows_an_asset_with_an_earlier_mention():
    """A floor is a floor, not an assignment. An asset discussed before it keeps its own
    earlier window — otherwise the grading span this module was built for would shrink."""
    rows = _rows(("BTC", "2024-01-15"))
    jobs, _ = plan_fetches(
        rows, _table(), today=TODAY, pad_days=0, floor_start=date(2024, 8, 1)
    )
    assert _asset_jobs(jobs)[0].start == date(2024, 1, 15)


def test_floor_start_refetches_a_series_whose_cache_is_too_shallow():
    """A cached span that satisfied the old window must not satisfy the deeper one, or the
    backfill silently no-ops on exactly the 95 series it exists to fix."""
    rows = _rows(("BTC", "2026-06-01"))
    cached = {("coinbase", "BTC-USD"): (date(2026, 5, 1), TODAY)}
    jobs, _ = plan_fetches(
        rows, _table(), today=TODAY, pad_days=0, cached_spans=cached,
        floor_start=date(2024, 8, 1),
    )
    assert len(_asset_jobs(jobs)) == 1


def test_omitting_floor_start_leaves_planning_exactly_as_it_was():
    """Default-off. Every caller that does not ask for lookback plans the same window it
    planned before this parameter existed."""
    rows = _rows(("TSLA", "2026-06-01"))
    without = plan_fetches(rows, _table(), today=TODAY, pad_days=0)[0]
    explicit_none = plan_fetches(rows, _table(), today=TODAY, pad_days=0, floor_start=None)[0]
    assert without == explicit_none
    assert _asset_jobs(without)[0].start == date(2026, 6, 1)
