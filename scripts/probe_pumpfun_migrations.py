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

**Conclusion: nothing free and keyless answers this question today.** Building
``oracle/altsignal/pumpfun.py`` needs one of:

- A Solana Tracker API key (sign up at solanatracker.io — free tier exists, per the earlier
  research; this probe could not confirm its limits without one)
- Trying the Cloudflare-blocked pump.fun endpoint through the Webshare proxy this repo already
  has for YouTube, on the chance the block is IP-reputation-based like YouTube's was
- Falling back to PumpPortal's WebSocket after all, which reopens the "no always-on process in
  this repo" question the design spec deliberately avoided

This is a real decision point, not something to guess past — see the plan's task 5.

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
