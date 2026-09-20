from __future__ import annotations

import re
from datetime import date
from functools import partial

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


def _book(name, *, mandate=MANDATE, tickers=("VTI",)):
    return Portfolio(
        name=name, mandate=mandate,
        positions=tuple(
            Position(holding=Holding(ticker=t, shares=1.0, cost=None), domain="stock")
            for t in tickers
        ),
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


def _review_response(monkeypatch, tmp_path, result, *, name="retirement"):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_review] = lambda: result
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


def test_404_for_an_unknown_mandate(monkeypatch, tmp_path):
    """Drives the real `mandate_review`, only overriding `mandate_books` — proving the
    dependency is reachable and that a bad name never gets as far as `review_for`."""
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    client = TestClient(app)

    response = client.get("/api/mandates/nope/review")

    assert response.status_code == 404
