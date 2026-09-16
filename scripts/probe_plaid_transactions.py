"""What Plaid actually calls a transaction, on the account this repo will cache it for.

**Why this probe exists rather than a Plaid docs table.** ``plaid.py:11-13`` sets this repo's
rule for an unverifiable Plaid claim — the probe is the authority, not the page. Plaid's own
``/investments/transactions/get`` docs list ``type``/``subtype`` values, but which pairs a real
institution actually sends, and how it spells a dividend versus a deposit, is an empirical
question. ``core.transactions.InvestmentTransaction.kind`` is filled from this probe's output,
not from the docs.

RUN IT:

    uv run python scripts/probe_plaid_transactions.py [portfolio]

Defaults to ``retirement``, the only account this ticket (#65) covers. Needs a linked account —
run ``uv run plaid-link <portfolio>`` first if ``PLAID_ACCESS_TOKEN_<PORTFOLIO>`` is not in
``.env``.

**This is a billed call.** ``/investments/transactions/get`` is a metered product distinct from
``/investments/holdings/get``; the first call against an Item starts a monthly subscription on
that connection that Plaid does not let you cancel without removing and re-linking it. Spec #63
already priced and accepted this (~$0.17/month, one connection) — this comment exists so running
the probe twice by accident does not read as a surprise the second time either; the subscription
is already started after the first run.

FINDINGS (retirement, last 90 days, 218 rows, 2026-09-16):

    93  type='buy'        subtype='buy'
    76  type='sell'       subtype='sell'
    30  type='cash'       subtype='dividend'
    13  type='transfer'   subtype='transfer'
     2  type='fee'        subtype='miscellaneous fee'   (docs say "management fee" — wrong guess)
     2  type='cash'       subtype='interest'
     2  type='cash'       subtype='withdrawal'

**``('transfer', 'transfer')`` cannot be resolved to deposit or withdrawal from ``(type,
subtype)`` alone** — M1 sends the same pair for both directions, and only ``amount``'s sign
says which way the cash moved. ``InvestmentTransaction.kind`` maps this pair to ``"other"``
rather than guessing a direction from the type alone; a caller that needs the direction reads
``amount``'s sign directly. This account's ``transfer`` rows are most likely the Roth funding
the Traditional as it gets built out — an internal reallocation, not household cash moving in
or out — which is a second reason not to force it into "deposit" or "withdrawal".

**No ``deposit`` pair appeared in this window.** ``('cash', 'deposit')`` in
``core.transactions``'s table is Plaid's documented spelling, carried over unconfirmed by this
probe — this account has not received an external deposit in the last 90 days. Re-run the probe
with a wider window (or after depositing) to confirm it before leaning on it for anything that
matters.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

from oracle import plaid

# A short window is enough to see which (type, subtype) pairs a real account actually produces
# without paging through two years of history just to answer that question.
WINDOW_DAYS = 90


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("portfolio", nargs="?", default="retirement")
    args = parser.parse_args(argv)

    try:
        token = plaid.access_token(args.portfolio)
    except plaid.PlaidError as exc:
        print(exc, file=sys.stderr)
        return 1

    today = datetime.now(UTC).date()
    start = today - timedelta(days=WINDOW_DAYS)
    try:
        payload = plaid.investment_transactions(token, start=start, end=today, offset=0, count=500)
    except plaid.PlaidError as exc:
        print(exc, file=sys.stderr)
        return 1

    rows = payload.get("investment_transactions") or []
    total = payload.get("total_investment_transactions")
    print(f"{args.portfolio}: {len(rows)} of {total} transaction(s) in the last "
          f"{WINDOW_DAYS} days\n")

    pairs = Counter((row.get("type"), row.get("subtype")) for row in rows)
    for (type_, subtype), count in sorted(pairs.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  type={type_!r:<12} subtype={subtype!r}")

    if not rows:
        print("\nno transactions in this window — widen WINDOW_DAYS or check a busier account")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
