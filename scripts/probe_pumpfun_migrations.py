"""Can anything free and keyless tell us which pump.fun tokens recently graduated?

"Graduated" means a token survived its bonding curve and moved to real liquidity (Raydium) —
the one pump.fun signal worth tracking, per the Phase 5 alt-signal design (everything else on
the platform is thousands of new, mostly-rug launches a day).

Two candidates were checked live on 2026-09-04, and **both need something this environment
does not have**:

1. **pump.fun's own frontend API** (``frontend-api.pump.fun``) — the endpoint its own website
   calls, unofficial but widely used by third-party tools. Returns Cloudflare error 1016
   ("origin DNS error" family) from this network — the same shape as the YouTube caption-fetch
   block this repo already works around with a residential proxy (see root `CLAUDE.md`'s
   ``WEBSHARE_PROXY_*`` note). Untested whether a proxy fixes it; not assumed either way.
2. **Solana Tracker** (``data.solanatracker.io``) — the REST provider named in the Phase 5
   research as the free-tier substitute for PumpPortal's WebSocket-only migration stream.
   Every endpoint, including the documented free tier, returned ``{"error": "API key is
   required"}`` (HTTP 401). "Free" here means free *after* signing up for a key, not keyless.

**Resolved 2026-09-04: nothing free and keyless answers this, but Solana Tracker's free tier
does.** ``GET /tokens/multi/graduated`` with an ``x-api-key`` header works, is a purpose-built
endpoint for exactly this signal, and its 2,500 requests/month free tier easily covers a
nightly poll (~30 calls/month). No credit card. See ``oracle/altsignal/pumpfun.py``, which
this probe's finding — Solana Tracker over the Cloudflare-blocked frontend API, and a nightly
poll over PumpPortal's WebSocket — is built against.

The Cloudflare block on pump.fun's own frontend API (point 1 above) was never retried through
the Webshare proxy, since point 2 resolved the question first.

RUN IT:

    uv run python scripts/probe_pumpfun_migrations.py
    uv run python scripts/probe_pumpfun_migrations.py --solanatracker-key YOUR_KEY

NEEDS: nothing to reproduce the failure above. A key, once you have one, to get past it.
"""
from __future__ import annotations

import argparse
import sys

import requests

FRONTEND_API = "https://frontend-api.pump.fun/coins/king-of-the-hill"
SOLANA_TRACKER = "https://data.solanatracker.io/tokens/latest"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) tegan-trades/0.1"


def check_frontend_api() -> str:
    try:
        resp = requests.get(FRONTEND_API, headers={"User-Agent": USER_AGENT}, timeout=15)
    except requests.RequestException as exc:
        return f"unreachable: {exc}"
    return f"{resp.status_code}: {resp.text[:200]}"


def check_solana_tracker(api_key: str | None) -> str:
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["x-api-key"] = api_key
    try:
        resp = requests.get(SOLANA_TRACKER, headers=headers, timeout=15)
    except requests.RequestException as exc:
        return f"unreachable: {exc}"
    return f"{resp.status_code}: {resp.text[:200]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--solanatracker-key", help="test with a real key instead of none")
    args = parser.parse_args(argv)

    print("pump.fun frontend API (no key, unofficial):")
    print(f"  {check_frontend_api()}")

    print("\nSolana Tracker (documented free tier, no key):")
    print(f"  {check_solana_tracker(None)}")

    if args.solanatracker_key:
        print("\nSolana Tracker, with the provided key:")
        print(f"  {check_solana_tracker(args.solanatracker_key)}")
    else:
        print("\nNo --solanatracker-key given — pass one once you have signed up to see "
              "whether the free tier actually reaches a migrations/graduations endpoint.")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
