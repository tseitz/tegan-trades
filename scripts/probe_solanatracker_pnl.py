"""Can the free Solana Tracker key reach the PnL endpoints, and is a wallet-watcher affordable?

Three questions, asked before anything is built against them. This is the "trending wallets"
idea's go/no-go: a leaderboard of the best traders of the last N days, re-pulled nightly so a
wallet that stops winning falls off by itself, is only possible if the free tier answers.

1. **Does the free tier reach `/v2/pnl/*` at all?** The graduated-tokens endpoint already works
   on this key, but a newer endpoint family is a separate entitlement. ``probe_pumpfun_migrations``
   is next door precisely because "free" there meant "free *after* you sign up" — the same shape
   of surprise, one layer in. A 401 and a 403 mean different things here: no key versus a key
   this plan does not cover.
2. **What does a leaderboard row actually carry?** Wallet identity tagging (KOL, bot, pool,
   developer) is the load-bearing field, not the PnL. Any raw "top trader" ranking is topped by
   MEV bots and liquidity pools, and following those is worthless. If the tag is absent or
   always null, the list cannot be filtered and the feature does not work.
3. **Is there a batch call?** The free tier is 2,500 requests/month and the nightly pump.fun
   poll already takes ~30. Watching 50 wallets one-at-a-time is ~1,500/month, which does not
   fit beside anything else. A batch endpoint decides whether this watches 50 wallets or 5.

**Paths are guessed, so each is tried in several spellings and the winner is reported.** The
lesson is `probe_alchemy_wallet`'s: Alchemy's own docs spell Solana `sol-mainnet`, that name is
rejected, and the rejection reads as an entitlement problem — an hour lost in a dashboard
toggling something already on. A name is not support. Probe the name before blaming the key.

FINDINGS, measured 2026-09-08 on the free tier. **Go — with one design change.**

1. **Every PnL path answers 200 on the free key.** No paywall, and no 403 anywhere. Both API
   generations work, so v2 is a choice rather than a requirement. The spellings that are real:
   ``/v2/pnl/leaderboard/top`` and ``/top-traders/all`` (but *not* ``/pnl/leaderboard/top``),
   ``/v2/pnl/tokens/{mint}/traders``, ``/top-traders/{mint}``, ``/first-buyers/{mint}`` (but
   *not* ``/tokens/{mint}/first-buyers``), ``/v2/pnl/wallets/{wallet}``, ``/pnl/{wallet}``.
2. **``identity`` is null on all 25 rows, so the bot filter this was supposed to lean on does
   not exist in practice.** The field is in the schema and is never populated. Filter on
   ``counts.trades`` instead, which is behaviour rather than a label and so cannot go
   unmaintained: the top 25 split cleanly into ≤8,700 trades/90d and ≥128,000, with nothing in
   between. A wallet turning 2,017,789 trades in 90 days is a market maker whatever it is
   called, and no tag is needed to see it.
   **The filter also improves the list rather than merely shortening it** — ROI runs inverse to
   trade count here (the seven busiest wallets return 5–32%, while the 300-to-2,000-trade rows
   return 146–180%). The wallets worth copying are the ones the raw ranking buries.
   One caveat that survives the filter: rows 13–23 cluster at ~1,900–2,240 trades and ~56–62%
   ROI, which reads as one operator's many wallets rather than eleven independent traders.
   Treat leaderboard rows as addresses, not as people.
3. **Batch is real and it is a POST**, which is why the GET probe below reports 400 rather than
   404: ``POST /v2/pnl/wallets/batch`` with ``{"wallets": [...]}``. The key must be ``wallets``
   — ``addresses`` returns "Body must include a non-empty wallets array". So watching N wallets
   costs ~1 request, not N, and the whole feature fits in ~60 requests/month against the 2,500
   cap. Cost was never the constraint it looked like.

Note ``pnlAdjustments.invalidPnl`` on the top row: $7.9M of $31.9M realised was corrected away
under ``pnlMode: strict``. A quarter of the headline number is untrusted by the source itself,
so rank on the adjusted figure and never on ``realizedRaw``.

RUN IT:

    uv run python scripts/probe_solanatracker_pnl.py
    uv run python scripts/probe_solanatracker_pnl.py --wallet <addr> --token <mint>

NEEDS: ``SOLANATRACKER_API_KEY`` in ``.env`` — the same key ``fetch-altsignal`` already uses.
Free tier, so this spends no real money, but it does spend from the monthly request cap: it
prints the count and every rate-limit header it is given. Roughly a dozen calls.
"""
from __future__ import annotations

import argparse
import json
import sys

import requests
from oracle.altsignal import pumpfun

HOST = "https://data.solanatracker.io"

# BONK: public, enormous, and old enough that any token-scoped endpoint has data for it. The
# point is reachability, so a token nobody has to look up beats a fresh graduate that may be
# too thin to answer.
DEFAULT_TOKEN = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# Every documented spelling seen in their docs, SDK and changelog. Tried in order; the first
# 200 wins and the rest are still reported, because a 403 among 404s is the interesting result
# — it says the path is real and the plan is the problem.
CANDIDATES = {
    "leaderboard": ("/v2/pnl/leaderboard/top", "/pnl/leaderboard/top", "/top-traders/all"),
    "token traders": ("/v2/pnl/tokens/{token}/traders", "/top-traders/{token}"),
    "first buyers": ("/first-buyers/{token}", "/tokens/{token}/first-buyers"),
    "wallet pnl": ("/v2/pnl/wallets/{wallet}", "/pnl/{wallet}"),
    "wallet batch": ("/v2/pnl/wallets/summary/batch", "/v2/pnl/wallets/batch"),
}

# Tags that would let a bot or a liquidity pool be filtered out of the ranking. Question 2 is
# answered by whether any of these appear in a row, under any name.
TAG_HINTS = ("tag", "tags", "label", "labels", "type", "identity", "isBot", "is_bot")

_calls = 0


def hit(path: str, key: str, params: dict | None = None) -> tuple[int, object, dict]:
    """One request, reporting the raw status. Deliberately not ``oracle.http.get_json`` — that
    helper retries and folds 404 into ``None``, and the status code *is* the finding here."""
    global _calls
    _calls += 1
    resp = requests.get(
        f"{HOST}{path}",
        headers={"x-api-key": key, "User-Agent": "tegan-trades/0.1"},
        params=params,
        timeout=20,
    )
    limits = {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower()}
    try:
        return resp.status_code, resp.json(), limits
    except ValueError:
        return resp.status_code, resp.text[:200], limits


def first_working(question: str, paths: tuple[str, ...], key: str, **fmt) -> object | None:
    """Try each spelling, print every outcome, return the payload of the first that answers."""
    print(f"\n{question}")
    payload = None
    for path in paths:
        filled = path.format(**fmt)
        status, body, limits = hit(filled, key, params={"days": 90, "limit": 5})
        note = ""
        if status == 401:
            note = "  <- key not accepted"
        elif status == 403:
            note = "  <- path exists, plan does not cover it"
        elif status == 404:
            note = "  <- wrong spelling, or not a real path"
        print(f"  {status}  {filled}{note}")
        if limits:
            print(f"       limits: {limits}")
        if status == 200 and payload is None:
            payload = body
    return payload


def rows_of(payload: object) -> list[dict]:
    """Their envelopes vary by endpoint — a bare list here, ``{data: [...]}`` there. Pull the
    first list of dicts out of whatever came back rather than assuming one shape."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def report_tags(payload: object) -> None:
    """Question 2. Prints one row verbatim, because 'is there an identity tag' is not a question
    anyone should answer from a summary."""
    rows = rows_of(payload)
    if not rows:
        print("  no rows to inspect — cannot answer the tagging question")
        return
    found = sorted({k for row in rows for k in row if any(h in k for h in TAG_HINTS)})
    print(f"  {len(rows)} rows; identity-ish fields: {found or 'NONE — cannot filter bots/pools'}")
    print(f"  first row verbatim:\n{json.dumps(rows[0], indent=2)[:1200]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--wallet", help="default: the top wallet the leaderboard returns")
    args = parser.parse_args(argv)

    key = pumpfun.load_api_key()
    if not key:
        print("SOLANATRACKER_API_KEY is not set — see .env.example", file=sys.stderr)
        return 1

    board = first_working("Q1/Q2 — leaderboard (the trending-wallets feed):",
                          CANDIDATES["leaderboard"], key, token=args.token, wallet="")
    if board is not None:
        report_tags(board)

    # Chain off the leaderboard rather than hardcoding someone's address: the probe finds its
    # own subject, and nobody has to paste a wallet they own into a terminal.
    wallet = args.wallet
    if not wallet:
        for row in rows_of(board):
            for field in ("wallet", "address", "owner"):
                if isinstance(row.get(field), str):
                    wallet = row[field]
                    break
            if wallet:
                break

    first_working("Q1 — top traders for one token:",
                  CANDIDATES["token traders"], key, token=args.token, wallet=wallet or "")
    first_working("Q1 — who bought a token early:",
                  CANDIDATES["first buyers"], key, token=args.token, wallet=wallet or "")

    if wallet:
        print(f"\n  (using wallet {wallet})")
        first_working("Q1 — one wallet's PnL:",
                      CANDIDATES["wallet pnl"], key, token=args.token, wallet=wallet)
        first_working("Q3 — batch, the one that decides 50 wallets vs 5:",
                      CANDIDATES["wallet batch"], key, token=args.token, wallet=wallet)
    else:
        print("\nNo wallet found and none given — skipped the wallet endpoints. "
              "Re-run with --wallet <addr> to reach them.")

    print(f"\nspent {_calls} requests of the 2,500/month free tier")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
