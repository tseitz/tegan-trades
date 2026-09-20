from __future__ import annotations

import re
from datetime import UTC, date, datetime
from functools import partial
from types import SimpleNamespace

import dashboard.api as api
import dashboard.assets as assets
import review.cli as review_cli
import review.render as render_module
from core.review import (
    HOLD,
    NO_VIEW,
    SILENT,
    TRIM,
    Holding,
    Location,
    Reading,
    RosterLean,
)
from fastapi.testclient import TestClient
from oracle.portfolios import Benchmark, Mandate, Portfolio, Position
from review.cli import ReviewResult

MANDATE = Mandate(name="test", benchmarks=(Benchmark(type="held_flat"),),
                  horizon="position", risk_posture="moderate")


def _book(name, *, mandate=MANDATE, tickers=("VTI",), updated=None):
    return Portfolio(
        name=name, mandate=mandate,
        positions=tuple(
            Position(holding=Holding(ticker=t, shares=1.0, cost=None), domain="stock")
            for t in tickers
        ),
        updated=updated,
    )


def _reading(ticker, *, verdict, price, shares=1.0, cost=None):
    return Reading(
        holding=Holding(ticker=ticker, shares=shares, cost=cost),
        roster=RosterLean(lean=SILENT, bulls=0, bears=0, people=0, newest=None,
                          age_days=None, voices=(), thin=False),
        location=Location(where="at_resistance", basis="range", position=0.5),
        verdict=verdict, price=price, weekly_trend="uptrend",
    )


def _grid_readings():
    """A graded row, a priced-but-ungraded row, and an unpriced row — the fixture the #87
    fidelity tests all share."""
    return (
        _reading("AAA", verdict=TRIM, price=100.0, shares=2.0, cost=50.0),
        _reading("BBB", verdict=HOLD, price=10.0, shares=3.0, cost=None),
        _reading("CCC", verdict=NO_VIEW, price=None, shares=1.0),
    )


def _grid_result(readings, *, book_name="retirement"):
    return ReviewResult(
        book=_book(book_name), readings=list(readings), contexts=(), mismatched=(),
        levels=((), (), 0), chains=(), macro=(),
    )


def _fake_freshness(message="prices: fetched 1 hours ago"):
    """A tiny stand-in for `oracle.freshness.Freshness` — not that type itself, which would
    put `oracle` in a dashboard test's imports (allowed there, but pointlessly coupling this
    file to a boundary it doesn't need to know about)."""
    return SimpleNamespace(message=message)


def _review_response(monkeypatch, tmp_path, result, *, name="retirement", freshness=None):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_review] = lambda: result
    app.dependency_overrides[api.data_freshness] = lambda: freshness or _fake_freshness()
    client = TestClient(app)
    return client.get(f"/api/mandates/{name}/review")


def test_two_mandates_come_back_as_two_names_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [
        _book("retirement"), _book("crypto"),
    ]
    client = TestClient(app)

    response = client.get("/api/mandates")

    assert response.status_code == 200
    assert response.json() == {"mandates": [{"name": "retirement"}, {"name": "crypto"}]}


def test_envelope_is_an_object_not_a_bare_array(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    client = TestClient(app)

    body = client.get("/api/mandates").json()

    assert isinstance(body, dict)
    assert "mandates" in body


def test_default_dependency_reaches_review_cli_load_books(monkeypatch, tmp_path):
    """Drives the endpoint through the real dependency, with no override — the only thing
    pinning "never by reading the directory again"."""
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    calls = []

    def fake_load_books(*, warn=None):
        calls.append(warn)
        return [_book("retirement")]

    monkeypatch.setattr(api, "load_books", fake_load_books)
    client = TestClient(api.create_app())

    response = client.get("/api/mandates")

    assert response.status_code == 200
    assert calls, "review.cli.load_books (via dashboard.api.load_books) was never called"


def test_fresh_clone_with_no_portfolios_returns_an_empty_envelope(monkeypatch, tmp_path):
    """Never monkeypatch `portfolios.DATA_ROOT` — `available()` captured it as a default
    argument at import time, so the patch is inert. Bind `available` to `tmp_path` instead."""
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    empty = tmp_path / "empty-portfolios"
    empty.mkdir()
    monkeypatch.setattr(review_cli.portfolios, "available", partial(
        review_cli.portfolios.available, root=empty,
    ))
    client = TestClient(api.create_app())

    response = client.get("/api/mandates")

    assert response.json() == {"mandates": []}


def test_name_comes_from_book_name_not_the_mandate_policy_name(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    colliding = Mandate(name="growth", benchmarks=(Benchmark(type="held_flat"),),
                        horizon="position", risk_posture="moderate")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [
        _book("retirement", mandate=colliding),
    ]
    client = TestClient(app)

    response = client.get("/api/mandates")

    assert response.json() == {"mandates": [{"name": "retirement"}]}


def test_unregistered_api_path_returns_json_404_not_the_spa(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    monkeypatch.setattr(assets, "WEB_DIST", dist)
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: []
    client = TestClient(app)

    response = client.get("/api/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_a_path_traversal_request_cannot_read_outside_web_dist(monkeypatch, tmp_path):
    """A plain `..` in a request `TestClient` builds gets normalised away before it ever
    reaches the app, which would pass against the vulnerable version of `assets.catch_all`
    just as easily as the fixed one. `%2e%2e` (or `//`) survives that normalisation and is
    what an actual client — including a percent-encoded `fetch()` from a browser tab — can
    send, so those are the forms that actually exercise the guard.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("should never be served")
    monkeypatch.setattr(assets, "WEB_DIST", dist)
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: []
    client = TestClient(app)

    for path in ("/%2e%2e/outside-secret.txt", f"/{'%2e%2e/' * 6}etc/passwd"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.text == "<html>spa</html>"


# ── the review grid (#87) ───────────────────────────────────────────────────


def test_the_grid_is_cell_by_cell_identical_to_the_terminal(monkeypatch, tmp_path):
    """AC 1 and AC 4. Row order is asserted by comparing line to line."""
    readings = _grid_readings()
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    terminal = render_module.render(list(readings), portfolio="retirement",
                                    as_of=date.fromisoformat(body["as_of"]))
    lines = terminal.splitlines()
    header_index = next(i for i, line in enumerate(lines) if "TICKER" in line)
    header_cols = re.split(r"\s{2,}", lines[header_index].strip())
    # The terminal `rstrip()`s the trailing empty verdict header; `columns` still carries it.
    assert header_cols == body["grid"]["columns"][:-1]

    row_lines = lines[header_index + 1:header_index + 1 + len(readings)]
    for row_line, row in zip(row_lines, body["grid"]["rows"], strict=True):
        assert re.split(r"\s{2,}", row_line.strip()) == [c["text"] for c in row["cells"]]


def test_sorting_by_value_matches_the_terminals_by_size_ranking(monkeypatch, tmp_path):
    """AC3, at #84's agreed seam — the JSON the browser receives, not a front-end runner.
    `render.ranked` is already public so a second Surface sorts identically rather than
    growing its own copy of `_by_size`; `web/src/grid/sort.ts` is that second copy, and this
    pins the rule it implements: descending by `cells[3]["value"]`, nulls last."""
    readings = _grid_readings()
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    def sort_key(row):
        value = row["cells"][3]["value"]
        return (value is None, -value if value is not None else 0)

    sorted_tickers = [row["ticker"] for row in sorted(body["grid"]["rows"], key=sort_key)]
    expected = render_module.ranked(list(readings), by_size=True)

    assert sorted_tickers == [reading.holding.ticker for reading in expected]


def test_numeric_cells_are_correct_to_the_cent(monkeypatch, tmp_path):
    readings = _grid_readings()
    graded = readings[0]
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    row = body["grid"]["rows"][0]
    assert row["cells"][3]["value"] == graded.market_value
    assert row["cells"][5]["value"] == graded.pnl
    assert row["cells"][6]["value"] == graded.pnl_pct


def test_an_unpriced_rows_computed_cells_survive_as_null_not_zero(monkeypatch, tmp_path):
    readings = _grid_readings()
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    row = body["grid"]["rows"][2]
    assert row["cells"][3] == {"text": "—", "value": None}     # VALUE
    assert row["cells"][5] == {"text": "—", "value": None}     # P&L
    assert row["cells"][6] == {"text": "—", "value": None}     # P&L %


def test_totals_exclude_the_unpriced_row_and_match_the_terminals_tail(monkeypatch, tmp_path):
    readings = _grid_readings()
    graded, ungraded, _unpriced = readings
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    assert body["grid"]["totals"]["market_value"] == graded.market_value + ungraded.market_value
    assert body["grid"]["totals"]["unpriced"] == 1

    terminal = render_module.render(list(readings), portfolio="retirement",
                                    as_of=date.fromisoformat(body["as_of"]))
    tail_lines = [line.strip() for line in terminal.splitlines()
                 if line.strip().startswith(("total", "P&L"))]
    # Lifted out of the same `render()` string above, not out of `render.totals_lines` — that
    # would only assert the wire calls the function the wire calls.
    assert body["grid"]["totals"]["lines"] == tail_lines


def test_a_book_with_no_cost_basis_anywhere_has_no_pnl_line(monkeypatch, tmp_path):
    """The ordinary shape of a synced crypto wallet — `render.py`'s own docstring on the
    tail says so."""
    only_ungraded = [_reading("BBB", verdict=HOLD, price=10.0, shares=3.0, cost=None)]
    body = _review_response(monkeypatch, tmp_path, _grid_result(only_ungraded)).json()

    assert not any(line.startswith("P&L") for line in body["grid"]["totals"]["lines"])


def test_an_empty_mandate_returns_no_rows_or_lines(monkeypatch, tmp_path):
    response = _review_response(monkeypatch, tmp_path, _grid_result([]))

    assert response.status_code == 200
    body = response.json()
    assert body["grid"]["rows"] == []
    assert body["grid"]["totals"]["lines"] == []


def test_review_mandate_comes_from_book_name_not_the_url_path(monkeypatch, tmp_path):
    result = _grid_result(_grid_readings(), book_name="actual-name")
    response = _review_response(monkeypatch, tmp_path, result, name="different-name")

    assert response.status_code == 200
    assert response.json()["mandate"] == "actual-name"


# ── the review header (#89) ─────────────────────────────────────────────────


def _result_with_book(book, readings=None, *, mismatched=()):
    return ReviewResult(
        book=book, readings=list(readings or _grid_readings()), contexts=(),
        mismatched=mismatched, levels=((), (), 0), chains=(), macro=(),
    )


def test_an_unpriced_row_is_marked_and_its_priced_siblings_are_not(monkeypatch, tmp_path):
    readings = _grid_readings()  # AAA priced, BBB priced, CCC unpriced
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    unpriced_by_ticker = {row["ticker"]: row["unpriced"] for row in body["grid"]["rows"]}
    assert unpriced_by_ticker == {"AAA": False, "BBB": False, "CCC": True}


def test_mismatches_reach_the_header_and_match_render(monkeypatch, tmp_path):
    mismatched = (("AAA", 100.0, 20.0),)
    result = _result_with_book(_book("retirement"), mismatched=mismatched)
    body = _review_response(monkeypatch, tmp_path, result).json()

    assert body["header"]["mismatches"] == render_module.mismatch_lines(mismatched)


def test_a_clean_book_has_no_mismatches_in_the_header(monkeypatch, tmp_path):
    body = _review_response(monkeypatch, tmp_path, _grid_result(_grid_readings())).json()

    assert body["header"]["mismatches"] == []


def test_header_prices_is_present_on_every_response_including_an_empty_book(monkeypatch, tmp_path):
    body = _review_response(monkeypatch, tmp_path, _grid_result([]),
                            freshness=_fake_freshness("prices: fetched 3 hours ago")).json()

    assert body["header"]["prices"] == "prices: fetched 3 hours ago"


def test_written_reflects_the_books_age_and_is_none_without_one(monkeypatch, tmp_path):
    dated = _result_with_book(_book("retirement", updated=date(2025, 1, 1)))
    body = _review_response(monkeypatch, tmp_path, dated).json()
    as_of = date.fromisoformat(body["as_of"])
    assert body["header"]["written"] == render_module.written_text(
        dated.book.age_days(on=as_of))

    undated = _result_with_book(_book("retirement"))
    body = _review_response(monkeypatch, tmp_path, undated).json()
    assert body["header"]["written"] is None


def test_stale_banner_matches_render_for_a_stale_book_and_is_none_for_a_fresh_one(
    monkeypatch, tmp_path,
):
    stale = _result_with_book(_book("retirement", updated=date(2020, 1, 1)))
    body = _review_response(monkeypatch, tmp_path, stale).json()
    as_of = date.fromisoformat(body["as_of"])
    assert stale.book.is_stale(on=as_of)
    assert body["header"]["stale_banner"] == render_module.stale_banner(
        stale.book.age_days(on=as_of))

    fresh = _result_with_book(_book("retirement", updated=datetime.now(UTC).date()))
    body = _review_response(monkeypatch, tmp_path, fresh).json()
    as_of = date.fromisoformat(body["as_of"])
    assert not fresh.book.is_stale(on=as_of)
    assert body["header"]["stale_banner"] is None


def test_a_page_load_never_syncs_the_price_cache(monkeypatch, tmp_path):
    """AC6 / #84 story 37: `oracle.setups_sync.ensure_fresh` must never be reached from a page
    load. `api.review_for` is stubbed too — the real one reaches `listings.load_or_fetch`,
    which fetches over HTTP when the cache file is absent (`oracle/listings.py:62-70`), so an
    unstubbed version would break this test on a fresh clone regardless of the sync guard.
    Drives the real `mandate_review`, not an override, so its own lookup logic runs for real.
    """
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")

    def _must_not_sync(*args, **kwargs):
        raise AssertionError("setups_sync.ensure_fresh must never run from a page load")

    monkeypatch.setattr(review_cli.setups_sync, "ensure_fresh", _must_not_sync)
    stub_result = _grid_result(_grid_readings())
    monkeypatch.setattr(api, "review_for", lambda books, **kw: [stub_result])

    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    app.dependency_overrides[api.data_freshness] = lambda: _fake_freshness()
    client = TestClient(app)

    response = client.get("/api/mandates/retirement/review")

    assert response.status_code == 200


def test_404_for_an_unknown_mandate(monkeypatch, tmp_path):
    """Drives the real `mandate_review`, only overriding `mandate_books` — proving the
    dependency is reachable and that a bad name never gets as far as `review_for`."""
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    app.dependency_overrides[api.data_freshness] = lambda: _fake_freshness()
    client = TestClient(app)

    response = client.get("/api/mandates/nope/review")

    assert response.status_code == 404
