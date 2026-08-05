"""One-time CoinGecko snapshot fetcher -> cfg/tickers.json. Run manually to refresh
(hits api.coingecko.com); resolve-time never touches the network. Free API, no key."""
from __future__ import annotations

import argparse
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parsed before anything is fetched, which is the whole point.

    This ``main`` took an ``argv`` and ignored it: the first statement was a live CoinGecko
    call and the second overwrote ``cfg/tickers.json``, a tracked source of truth. So
    ``fetch-tickers --help`` spent a request and restaged 1,071 changed ranks instead of
    printing help — found by sweeping ``--help`` across every console script to check they
    still imported. The signature was the trap: it looked like every other CLI here, so
    nothing suggested the argument was decorative.
    """
    parser = argparse.ArgumentParser(
        description="Refresh cfg/tickers.json from CoinGecko (network; free, no key).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="where to write (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report, but write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin network glue
    args = parse_args(argv)
    snapshot = build_snapshot(fetch_rows())
    if args.dry_run:
        print(f"would write {len(snapshot)} tickers -> {args.out}")
        return 0
    path = write_snapshot(snapshot, path=args.out)
    print(f"wrote {len(snapshot)} tickers -> {path}")
    return 0
