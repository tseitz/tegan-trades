from datetime import date

from oracle.plan import FetchJob, plan_fetches
from oracle.route import OracleRef, RoutingTable, Unpriceable


def _table(**kw):
    base = dict(
        curated={},
        coinbase_symbols=frozenset({"BTC", "ETH"}),
        kraken_symbols=frozenset({"XMR"}),
        domain_consensus={"BTC": "crypto", "ETH": "crypto", "XMR": "crypto", "TSLA": "stock"},
    )
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
    """An asset first mentioned recently doesn't need two years of history — fetching the
    corpus-wide span for every symbol would multiply the backfill for no benefit."""
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
