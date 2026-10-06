import json

import pytest
import requests
from distill.fetch_tickers import RETRIES, build_snapshot, fetch_rows, write_snapshot


def test_build_snapshot_uppercases_symbol_and_keeps_rank():
    rows = [
        {"symbol": "btc", "name": "Bitcoin", "market_cap_rank": 1},
        {"symbol": "eth", "name": "Ethereum", "market_cap_rank": 2},
        {"symbol": "", "name": "junk", "market_cap_rank": None},  # skipped
    ]
    snap = build_snapshot(rows)
    assert snap == {
        "BTC": {"name": "Bitcoin", "market_cap_rank": 1},
        "ETH": {"name": "Ethereum", "market_cap_rank": 2},
    }


class _FakeResp:
    def __init__(self, data, status_code=200, headers=None):
        self._data = data
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, pages):
        self._pages = pages
        self.params = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.params.append(params)
        return _FakeResp(self._pages[params["page"] - 1])


class _RateLimitedSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.headers = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.headers.append(headers)
        return self._responses.pop(0)


def test_fetch_rows_paginates_and_truncates_to_top_n():
    pages = [
        [{"symbol": "btc", "market_cap_rank": 1}, {"symbol": "eth", "market_cap_rank": 2}],
        [{"symbol": "sol", "market_cap_rank": 3}, {"symbol": "xrp", "market_cap_rank": 4}],
    ]
    session = _FakeSession(pages)
    rows = fetch_rows(top_n=3, session=session, per_page=2)
    assert [r["symbol"] for r in rows] == ["btc", "eth", "sol"]  # 3, not 4
    assert [p["page"] for p in session.params] == [1, 2]
    assert session.params[0]["order"] == "market_cap_desc"


def test_write_snapshot_round_trips(tmp_path):
    out = tmp_path / "tickers.json"
    write_snapshot({"BTC": {"name": "Bitcoin", "market_cap_rank": 1}}, out)
    assert json.loads(out.read_text())["BTC"]["market_cap_rank"] == 1


def test_fetch_rows_waits_out_a_429_then_succeeds():
    session = _RateLimitedSession([
        _FakeResp(None, 429, {"Retry-After": "7"}),
        _FakeResp(None, 429),
        _FakeResp([{"symbol": "btc", "market_cap_rank": 1}]),
    ])
    waits = []
    rows = fetch_rows(top_n=1, session=session, per_page=1, sleep=waits.append)
    assert [r["symbol"] for r in rows] == ["btc"]
    assert waits[0] == 7.0
    assert len(waits) == 2


def test_fetch_rows_raises_once_retries_are_spent():
    session = _RateLimitedSession([_FakeResp(None, 429)] * RETRIES)
    with pytest.raises(requests.HTTPError):
        fetch_rows(top_n=1, session=session, per_page=1, sleep=lambda _s: None)


def test_fetch_rows_sends_the_demo_key(monkeypatch):
    monkeypatch.setenv("COINGECKO_DEMO_API_KEY", "cg-test")
    session = _RateLimitedSession([_FakeResp([{"symbol": "btc"}])])
    fetch_rows(top_n=1, session=session, per_page=1)
    assert session.headers == [{"x-cg-demo-api-key": "cg-test"}]
