"""The fetch stage's refusal to cache a series that is not the asset it claims to be.

``fetch_cli`` is otherwise a thin CLI over tested parts and has no tests for that reason. This
one earns its place: it is a gate, and the failure it prevents is silent — a contradicted series
that reaches disk is graded, drawn on and offered exactly like a correct one.
"""
from __future__ import annotations

from datetime import date

from oracle import cache, fetch_cli
from oracle.marks import Mark, index_marks
from oracle.plan import FetchJob
from oracle.route import OracleRef
from oracle.series import Bar


def _job(asset):
    return FetchJob(
        ref=OracleRef(asset=asset, source="yahoo", symbol=asset, needs_validation=True),
        start=date(2026, 7, 1), end=date(2026, 7, 31),
    )


def _bars(close):
    return [Bar(date=date(2026, 7, 31), open=close, high=close, low=close, close=close)]


def _patch(monkeypatch, *, close, merged):
    monkeypatch.setattr(fetch_cli.yahoo, "probe", lambda symbol: "EQUITY")
    monkeypatch.setattr(fetch_cli._SOURCES["yahoo"], "fetch_daily",
                        lambda symbol, start, end: _bars(close))
    monkeypatch.setattr(cache, "merge", lambda series, root=None: merged.append(series))


def test_a_contradicted_series_never_reaches_disk(monkeypatch, tmp_path):
    """Yahoo's bare WTI is W&T Offshore at 3.51 and Lighter's WTI is crude at 87.40. The type
    probe clears it — W&T Offshore genuinely is an EQUITY — so this is the only thing standing
    between the corpus and three weeks of grading crude theses against an E&P company."""
    merged = []
    _patch(monkeypatch, close=3.51, merged=merged)

    _job_out, status, n = fetch_cli._run_job(
        _job("WTI"), root=tmp_path, marks=index_marks([Mark("lighter", "WTI", 87.40)]),
    )

    assert status.startswith("rejected:wrong_instrument")
    assert merged == [], "a refused series must not be cached"
    assert n == 0


def test_the_rejection_carries_the_ratio(monkeypatch, tmp_path):
    """A human fixing the map needs to know whether it was an undeclared proxy (10x) or a
    collision (four orders of magnitude), and the verdict alone does not say. 87.40 against
    3.51 is 24.9x."""
    _patch(monkeypatch, close=3.51, merged=[])

    _, status, _ = fetch_cli._run_job(
        _job("WTI"), root=tmp_path, marks=index_marks([Mark("lighter", "WTI", 87.40)]),
    )

    assert "24.9" in status


def test_a_confirmed_series_is_cached(monkeypatch, tmp_path):
    merged = []
    _patch(monkeypatch, close=64000.0, merged=merged)

    _, status, n = fetch_cli._run_job(
        _job("BTC"), root=tmp_path, marks=index_marks([Mark("lighter", "BTC", 64100.0)]),
    )

    assert status == "ok"
    assert len(merged) == 1 and n == 1


def test_an_asset_no_venue_lists_is_still_cached(monkeypatch, tmp_path):
    """121 of 188 guesses are absent from every venue that can be asked, including CAT and CVX.
    Silence is a property of the venue, not evidence about the route — refusing on it would
    empty most of the equity corpus to catch nothing."""
    merged = []
    _patch(monkeypatch, close=350.0, merged=merged)

    _, status, _ = fetch_cli._run_job(_job("CAT"), root=tmp_path, marks=index_marks([]))

    assert status == "ok"
    assert len(merged) == 1


def test_a_curated_route_is_cached_without_being_second_guessed(monkeypatch, tmp_path):
    """`OIL -> CL=F` was chosen by hand. A venue disagreeing with a deliberate human choice is
    a question for a human, not grounds for an automatic refusal."""
    merged = []
    _patch(monkeypatch, close=81.75, merged=merged)
    job = FetchJob(
        ref=OracleRef(asset="OIL", source="yahoo", symbol="CL=F", curated=True),
        start=date(2026, 7, 1), end=date(2026, 7, 31),
    )

    _, status, _ = fetch_cli._run_job(
        job, root=tmp_path, marks=index_marks([Mark("lighter", "OIL", 0.01)]),
    )

    assert status == "ok"
