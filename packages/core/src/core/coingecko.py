"""The CoinGecko Demo key, read once for every caller.

Two packages call CoinGecko (`distill.fetch_tickers`, `oracle.altsignal.coingecko`) and neither
may import the other, so the key's name lives here. Keyless still works, but it shares a rate
limit with everything else on the IP, and the droplet's datacenter IP took a 429 on most nights.
"""
from __future__ import annotations

import os

from core.env import load_env

KEY_ENV = "COINGECKO_DEMO_API_KEY"


def headers() -> dict[str, str]:
    """``x-cg-demo-api-key`` when a key is set, else nothing. Demo keys use the same
    ``api.coingecko.com`` host as keyless; only Pro keys move to ``pro-api``."""
    load_env()
    key = os.environ.get(KEY_ENV)
    return {"x-cg-demo-api-key": key} if key else {}
