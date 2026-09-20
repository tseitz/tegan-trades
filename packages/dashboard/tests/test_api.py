from __future__ import annotations

from functools import partial

import dashboard.api as api
import dashboard.assets as assets
import review.cli as review_cli
from core.review import Holding
from fastapi.testclient import TestClient
from oracle.portfolios import Benchmark, Mandate, Portfolio, Position

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
