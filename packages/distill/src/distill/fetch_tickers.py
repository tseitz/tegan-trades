"""One-time CoinGecko snapshot fetcher -> cfg/tickers.json. Run manually to refresh
(hits api.coingecko.com); resolve-time never touches the network. Free API, no key."""
from __future__ import annotations

import json
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUT = _REPO_ROOT / "cfg" / "tickers.json"
API = "https://api.coingecko.com/api/v3/coins/markets"
DEFAULT_TOP_N = 1000
PER_PAGE = 250
TIMEOUT = 30.0


def build_snapshot(rows: list[dict]) -> dict:
    """Pure: API market rows -> { UPPER_SYMBOL: {name, market_cap_rank} }."""
    snapshot: dict[str, dict] = {}
    for row in rows:
        symbol = (row.get("symbol") or "").upper()
        if not symbol:
            continue
        snapshot[symbol] = {"name": row.get("name"), "market_cap_rank": row.get("market_cap_rank")}
    return snapshot


def fetch_rows(top_n: int = DEFAULT_TOP_N, *, session=None, per_page: int = PER_PAGE) -> list[dict]:
    session = session or requests.Session()
    rows: list[dict] = []
    pages = (top_n + per_page - 1) // per_page
    for page in range(1, pages + 1):
        resp = session.get(
            API,
            params={"vs_currency": "usd", "order": "market_cap_desc",
                    "per_page": per_page, "page": page},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        rows.extend(resp.json())
    return rows[:top_n]


def write_snapshot(snapshot: dict, path=DEFAULT_OUT) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin network glue
    snapshot = build_snapshot(fetch_rows())
    path = write_snapshot(snapshot)
    print(f"wrote {len(snapshot)} tickers -> {path}")
    return 0
