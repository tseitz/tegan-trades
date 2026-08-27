"""Does Plaid actually serve *holdings* for the brokers we hold accounts at?

Plaid connects to an institution for one set of products at a time, and the set differs per
institution. The distinction that matters here is exact: **Balance is not Investments.**
Balance says an account is worth $113,906; ``/investments/holdings/get`` says it is 3.32 ALAB
and 1.98 AMAT. Only the second one is a portfolio, and only the second one feeds `review`.

**Why this probe exists rather than a link to a coverage table.** Plaid's own institution page
for M1 Finance lists Assets, Auth and Balance — no Investments — while third-party roundups
list M1 among brokers Plaid exposes investment data for. Those cannot both be right, and the
answer decides whether an adapter is worth writing at all. Plaid's docs say the coverage table
is not updated in real time and to use ``/institutions/*`` instead, so that is what this asks.

RUN IT:

    uv run python scripts/probe_plaid_coverage.py
    uv run python scripts/probe_plaid_coverage.py --institution "Fidelity" --institution "M1"

NEEDS: ``PLAID_CLIENT_ID`` and ``PLAID_SECRET`` in ``.env``. Sign up at
dashboard.plaid.com/signup — a team created after 2026-04-15 gets the free Trial plan, which
includes Investments and 10 live connections. Two brokerage accounts fit inside that.

**Runs against Production, not Sandbox, and that is deliberate.** Sandbox serves a handful of
invented institutions ("First Platypus Bank"), so a Sandbox answer about M1 would be an answer
about nothing. This spends no money: institution lookups are metadata, not a linked account.

READ THE OUTPUT LIKE THIS. `investments: yes` means Plaid will serve holdings for that
institution and an adapter is worth building. `investments: no` means the account stays a
hand-kept file — which is not a failure, just the answer.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.env import load_env

# The brokers this repo actually holds accounts at. Extend with --institution rather than by
# editing: the point of the probe is the question, not this particular pair.
DEFAULT_INSTITUTIONS = ("M1 Finance", "Robinhood")

HOST = "https://production.plaid.com"

# What we are really asking about. Listed alongside `investments` so the output can show the
# difference between "Plaid reaches this broker" and "Plaid reaches this broker's positions" —
# an institution supporting only these is exactly the trap this probe exists to catch.
NEIGHBOURS = ("balance", "auth", "assets", "transactions")


def _post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    request = Request(f"{HOST}{path}", data=body,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _credentials() -> tuple[str, str]:
    load_env()
    client_id, secret = os.environ.get("PLAID_CLIENT_ID"), os.environ.get("PLAID_SECRET")
    if not client_id or not secret:
        raise SystemExit(
            "PLAID_CLIENT_ID and PLAID_SECRET are not set. Sign up at "
            "dashboard.plaid.com/signup, then put both in .env — see .env.example."
        )
    return client_id, secret


def search(name: str, *, client_id: str, secret: str, products: list[str]) -> list[dict]:
    """Institutions matching ``name`` that support ``products``.

    Filtering server-side rather than fetching everything and checking ``products`` locally:
    the endpoint's own filter is the authoritative answer, and a local check would be our
    reading of a field rather than Plaid's.
    """
    found = _post("/institutions/search", {
        "client_id": client_id, "secret": secret,
        "query": name, "products": products, "country_codes": ["US"],
    })
    return found.get("institutions", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--institution", action="append", default=[],
                        help="broker to check; repeatable. Defaults to the two we hold.")
    args = parser.parse_args(argv)
    wanted = args.institution or list(DEFAULT_INSTITUTIONS)

    client_id, secret = _credentials()
    print(f"asking Plaid Production about {len(wanted)} institution(s)\n")

    verdicts: dict[str, bool] = {}
    for name in wanted:
        try:
            with_investments = search(name, client_id=client_id, secret=secret,
                                      products=["investments"])
            reachable = search(name, client_id=client_id, secret=secret,
                               products=["balance"])
        except HTTPError as exc:
            # Printed in full: Plaid puts the actionable part (a product not enabled on the
            # plan, a bad key) in the body, and the status alone says none of it.
            print(f"  {name:<14} ERROR {exc.code} — {exc.read().decode()[:400]}")
            continue
        except URLError as exc:
            print(f"  {name:<14} unreachable — {exc.reason}")
            continue

        hit = next((i for i in with_investments
                    if name.split()[0].lower() in i["name"].lower()), None)
        near = next((i for i in reachable
                     if name.split()[0].lower() in i["name"].lower()), None)
        verdicts[name] = hit is not None

        if hit is not None:
            others = sorted(set(hit.get("products", ())) & set(NEIGHBOURS))
            print(f"  {name:<14} investments: YES  ({hit['name']}, {hit['institution_id']})"
                  f"{'  also ' + ', '.join(others) if others else ''}")
        elif near is not None:
            # The trap, named out loud. Plaid reaches the broker but not its positions, which
            # from an integration's point of view is the same as not reaching it.
            print(f"  {name:<14} investments: no   — but Plaid does reach it "
                  f"({near['name']}) for balance only. Balance is a total, not positions.")
        else:
            print(f"  {name:<14} investments: no   — not found under this name at all; "
                  f"try a different spelling with --institution")

    good = [n for n, ok in verdicts.items() if ok]
    bad = [n for n, ok in verdicts.items() if not ok]
    print()
    if good:
        print(f"adapter is worth writing for: {', '.join(good)}")
    if bad:
        print(f"stays a hand-kept file:       {', '.join(bad)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
