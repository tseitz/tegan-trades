import json

from distill.fetch_tickers import build_snapshot, fetch_rows, write_snapshot


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
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, pages):
        self._pages = pages
        self.params = []

    def get(self, url, params=None, timeout=None):
        self.params.append(params)
        return _FakeResp(self._pages[params["page"] - 1])


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
