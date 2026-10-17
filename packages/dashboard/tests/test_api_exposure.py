from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import dashboard.api as api
import dashboard.assets as assets
import pytest
from core.networth import Excluded, Exposure, Part, Slice
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, result):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.exposure_result] = lambda: result
    app.dependency_overrides[api.data_freshness] = lambda: SimpleNamespace(message="prices: fresh")
    return TestClient(app)


def _exposure():
    return Exposure(
        total=1000.0,
        slices=(
            Slice("ETH", 623.456, 0.623456, (Part("crypto", "ETH", 400.0),
                                             Part("robinhood", "BMNR", 223.456))),
            Slice("Cash", 376.544, 0.376544, (Part("treasury", "parked", 376.544),)),
        ),
        excluded=(Excluded("crypto", "ANSEM", "unpriced"),),
    )


def test_exposure_envelope(monkeypatch, tmp_path):
    body = _client(monkeypatch, tmp_path, (_exposure(), [])).get("/api/exposure").json()

    assert set(body) == {"total", "slices", "excluded", "problems", "prices"}
    assert body["prices"] == "prices: fresh"
    assert body["problems"] == []
    assert body["excluded"] == [{"book": "crypto", "ticker": "ANSEM", "reason": "unpriced"}]
    eth, cash = body["slices"]
    assert (eth["asset"], cash["asset"]) == ("ETH", "Cash")
    assert eth["parts"] == [{"book": "crypto", "ticker": "ETH", "value": 400.0},
                            {"book": "robinhood", "ticker": "BMNR", "value": 223.456}]


def test_exposure_value_and_pct_to_the_cent(monkeypatch, tmp_path):
    body = _client(monkeypatch, tmp_path, (_exposure(), [])).get("/api/exposure").json()

    assert body["total"] == 1000.0
    assert body["slices"][0]["value"] == pytest.approx(623.46, abs=0.005)
    assert body["slices"][0]["pct"] == pytest.approx(0.6235, abs=0.00005)


def test_exposure_none_result_is_empty_not_an_error(monkeypatch, tmp_path):
    body = _client(monkeypatch, tmp_path, (None, ["portfolio x unreadable"])).get(
        "/api/exposure").json()

    assert body["total"] is None
    assert body["slices"] == []
    assert body["excluded"] == []
    assert body["problems"] == ["portfolio x unreadable"]


def test_exposure_null_pct_survives(monkeypatch, tmp_path):
    result = Exposure(total=0.0, slices=(Slice("ETH", 0.0, None, ()),),
                      excluded=(Excluded("crypto", "X", "mismatched"),))

    body = _client(monkeypatch, tmp_path, (result, [])).get("/api/exposure").json()

    assert body["slices"][0]["pct"] is None
    assert body["excluded"][0]["reason"] == "mismatched"


def test_load_warning_reaches_problems(monkeypatch, tmp_path):
    seen = {}

    def fake_assemble(books, *, as_of, warn):
        seen["books"] = books
        warn("treasury: bad row")

    monkeypatch.setattr(api, "assemble", fake_assemble)
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_load] = lambda: (["book"], ["portfolios/x.yaml: skipped"])
    app.dependency_overrides[api.as_of_today] = lambda: date(2026, 10, 15)
    app.dependency_overrides[api.data_freshness] = lambda: SimpleNamespace(message="m")

    body = TestClient(app).get("/api/exposure").json()

    assert seen["books"] == ["book"]
    assert body["problems"] == ["portfolios/x.yaml: skipped", "treasury: bad row"]
    assert body["total"] is None
