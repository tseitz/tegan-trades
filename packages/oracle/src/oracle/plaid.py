"""Pull what you hold straight from the broker, and write it into the file you already keep.

**This does not replace ``portfolios.py`` — it feeds it.** A sync rewrites the ``positions:``
block of ``data/portfolios/<name>.yaml`` and touches nothing else, so ``review`` keeps one
reader, the ``levels:`` knob and every comment you wrote survive, and the staleness warning
keeps working for free (the file's mtime moves when the sync writes it). If Plaid breaks or
you stop paying for it, the last good snapshot is still on disk and still reviewable. A second
code path that bypassed the file would have none of those properties.

**What Plaid gives and what it costs.** ``/investments/holdings/get`` returns positions, not a
balance — the distinction ``scripts/probe_plaid_coverage.py`` exists to check. Both brokers we
hold accounts at answer YES to it (M1 ``ins_116960``, Robinhood ``ins_54``), which contradicts
Plaid's own published coverage page for M1; the probe is the authority, not the page. Holdings
calls bill against the plan's Item count, not per call, so a nightly sync costs nothing extra.

**Read-only by construction.** The only products ever requested are ``investments``, so the
token this creates cannot move money. It still reads your positions, so it lives in ``.env``
with the signing key and never in ``data/`` — an access token is not regenerable ore.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

from core.env import load_env

from oracle import portfolios

HOST = "https://production.plaid.com"

# Everything a broker will hand back. `cash` is dropped rather than held: a dollar has no
# chart, so a cash row would enter `review` as a position with no level and no roster view and
# sit there forever reading NO_READ. Same reason buying power was left out by hand.
CASH_TYPES = frozenset({"cash"})
CRYPTO_TYPES = frozenset({"cryptocurrency"})

# Plaid spells a currency-as-security like `CUR:USD`. Matching the prefix catches those even
# when `type` is something other than `cash`, which it is for a few institutions.
_CURRENCY = re.compile(r"^CUR:", re.IGNORECASE)


class PlaidError(Exception):
    """A Plaid call that failed, carrying the body — Plaid puts the actionable part there."""


# Plaid's own word for a brokerage or retirement account, as opposed to `depository` (a
# current or savings account) and `credit`. One login can reach all three — a SoFi connection
# arrives with Checking, Savings and Self-directed together — and only this one holds money you
# could put into a position. Summing the others would report a savings balance as buying power.
INVESTMENT = "investment"


# The row shapes and the file writer live in `portfolios`, beside the reader that parses them
# back. Re-exported under the old names so this module still reads as the Plaid adapter.
Row = portfolios.Row
Skipped = portfolios.Skipped

SOURCE = portfolios.Source(name="Plaid", command="plaid-sync")


def env_key(portfolio: str) -> str:
    """The ``.env`` variable holding one account's access token."""
    return "PLAID_ACCESS_TOKEN_" + re.sub(r"[^A-Z0-9]+", "_", portfolio.upper()).strip("_")


def access_token(portfolio: str) -> str:
    load_env()
    key = env_key(portfolio)
    token = os.environ.get(key)
    if not token:
        raise PlaidError(f"{key} is not set — run `uv run plaid-link {portfolio}` first")
    return token


def credentials() -> tuple[str, str]:
    load_env()
    client_id, secret = os.environ.get("PLAID_CLIENT_ID"), os.environ.get("PLAID_SECRET")
    if not client_id or not secret:
        raise PlaidError("PLAID_CLIENT_ID and PLAID_SECRET are not set — see .env.example")
    return client_id, secret


def post(path: str, payload: dict, *, timeout: int = 30) -> dict:
    client_id, secret = credentials()
    body = json.dumps({"client_id": client_id, "secret": secret, **payload}).encode()
    request = urllib.request.Request(f"{HOST}{path}", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise PlaidError(f"{path} failed ({exc.code}): {exc.read().decode()[:600]}") from exc


def hosted_link(portfolio: str) -> tuple[str, str]:
    """``(link_token, url)`` for a browser login. Hosted rather than embedded on purpose:
    Plaid's normal Link is a JavaScript widget, which would mean running a web server out of a
    command-line tool. Hosted Link is a plain URL that opens anywhere, including a phone."""
    found = post("/link/token/create", {
        "user": {"client_user_id": f"tegan-trades-{portfolio}"},
        "client_name": "tegan-trades",
        "products": ["investments"],
        "country_codes": ["US"],
        "language": "en",
        "hosted_link": {},
    })
    url = found.get("hosted_link_url")
    if not url:
        raise PlaidError(f"Plaid returned no hosted_link_url: {sorted(found)}")
    return found["link_token"], url


def public_token(link_token: str) -> str | None:
    """The token a finished login leaves behind, or None while it is still in progress."""
    found = post("/link/token/get", {"link_token": link_token})
    for session in found.get("link_sessions") or ():
        for added in (session.get("results") or {}).get("item_add_results") or ():
            if added.get("public_token"):
                return added["public_token"]
    return None


def wait_for_login(link_token: str, *, every: int = 3, patience: int = 600,
                   tick=None) -> str:
    """Poll until the browser login lands. Polling rather than a webhook because a webhook
    needs a public URL, and this runs on a laptop."""
    deadline = time.monotonic() + patience
    while time.monotonic() < deadline:
        found = public_token(link_token)
        if found:
            return found
        if tick:
            tick()
        time.sleep(every)
    raise PlaidError(f"no login completed within {patience}s — re-run to get a fresh link")


def exchange(token: str) -> str:
    return post("/item/public_token/exchange", {"public_token": token})["access_token"]


def holdings(token: str) -> dict:
    return post("/investments/holdings/get", {"access_token": token}, timeout=60)


def _domain(security: dict) -> str:
    return "crypto" if (security.get("type") or "").lower() in CRYPTO_TYPES else "stock"


def _is_cash(security: dict) -> bool:
    return ((security.get("type") or "").lower() in CASH_TYPES
            or bool(_CURRENCY.match(security.get("ticker_symbol") or "")))


def rows_from(payload: dict, *, accounts: tuple[str, ...] = ()
              ) -> tuple[tuple[Row, ...], tuple[Skipped, ...], tuple[str, ...]]:
    """``(rows, skipped, account labels)`` from one ``/investments/holdings/get`` response.

    ``accounts`` narrows an Item that holds more than one account — a broker where the Roth
    and the taxable account arrive together. Empty means all of them, which is the right
    default: an account you forgot to name would otherwise vanish from the review without
    anything failing.

    Holdings of the same security across two accounts are **summed**, because the file allows
    one row per ticker and the chart does not care which sleeve the shares sit in. Cost is
    re-derived from the combined basis so the blend stays a true average price.
    """
    securities = {s["security_id"]: s for s in payload.get("securities") or ()}
    labels = {a["account_id"]: f"{a.get('name') or a['account_id']} ({a.get('mask') or '—'})"
              for a in payload.get("accounts") or ()}

    shares_by: dict[str, float] = {}
    basis_by: dict[str, float | None] = {}
    domains: dict[str, str] = {}
    figis: dict[str, str | None] = {}
    marks: dict[str, float | None] = {}
    skipped: list[Skipped] = []
    used: list[str] = []

    for held in payload.get("holdings") or ():
        account = held.get("account_id")
        if accounts and account not in accounts:
            continue
        if account in labels and labels[account] not in used:
            used.append(labels[account])

        security = securities.get(held.get("security_id")) or {}
        name = security.get("name") or held.get("security_id") or "?"
        if _is_cash(security):
            continue                      # not a skip worth reporting; cash is never a row
        ticker = (security.get("ticker_symbol") or "").strip().upper()
        if not ticker:
            skipped.append(Skipped(what=name, why="Plaid gave no ticker symbol"))
            continue
        shares = held.get("quantity")
        if not isinstance(shares, int | float) or shares <= 0:
            skipped.append(Skipped(what=f"{ticker} ({name})",
                                   why=f"quantity is {shares!r}"))
            continue

        basis = held.get("cost_basis")
        shares_by[ticker] = shares_by.get(ticker, 0.0) + float(shares)
        # One account missing its basis poisons the blend, so the whole ticker loses its cost
        # rather than reporting an average drawn from half the shares.
        running = basis_by.get(ticker, 0.0)
        basis_by[ticker] = (None if running is None or not isinstance(basis, int | float)
                            else running + float(basis))
        domains[ticker] = _domain(security)
        figis[ticker] = security.get("figi")
        # The instrument price, not the position value. Two accounts holding the same security
        # report the same mark, so last-one-wins is not a merge decision — it is the same number.
        mark = held.get("institution_price")
        marks[ticker] = float(mark) if isinstance(mark, int | float) else None

    rows: list[Row] = []
    for ticker, total in sorted(shares_by.items()):
        basis = basis_by[ticker]
        rows.append(Row(ticker=ticker, shares=total, domain=domains[ticker],
                        cost=(basis / total if basis is not None and total > 0 else None),
                        figi=figis[ticker], mark=marks[ticker]))
    return tuple(rows), tuple(skipped), tuple(used)


def cash_from(payload: dict, *, accounts: tuple[str, ...] = ()
              ) -> tuple[float | None, dict[str, float]]:
    """``(total, per account)`` — money you could put into a position today.

    ``balances.available`` and not ``balances.current``: on a brokerage account ``current`` is
    the whole account including every security in it, so reporting it as cash would say a
    $115k retirement book has $115k to deploy.

    **Only ``investment`` accounts count.** One login reaches a savings account as easily as a
    brokerage one, and a savings balance is not buying power — the SoFi connection arrives with
    $44k of savings beside $7k of actual cash. Returns None rather than 0.0 when no investment
    account reported a balance at all, so "the broker did not say" stays distinguishable from
    "you have nothing".

    **The breakdown is what makes merging two accounts into one file honest.** A Roth and a
    Traditional IRA are one retirement book to think about, and summing their positions is
    right — the same ticker in both is one exposure. Their *cash* does not combine that way:
    you cannot buy in the Roth with Traditional money. The total is still a true number, but
    only the split says which of it you can actually spend where.
    """
    seen = [a for a in payload.get("accounts") or ()
            if (a.get("type") or "") == INVESTMENT
            and (not accounts or a.get("account_id") in accounts)
            and isinstance((a.get("balances") or {}).get("available"), int | float)]
    if not seen:
        return None, {}
    by = {str(a.get("name") or a["account_id"]): float(a["balances"]["available"])
          for a in seen}
    return float(sum(by.values())), by


def remember_token(portfolio: str, token: str, *, env: Path) -> str:
    """Append the access token to ``.env``, or say it is already there.

    Appended rather than printed to the terminal: the token reads your positions, and a
    terminal is scrollback, a screen share and a shell history. Never overwrites an existing
    line — a stale token that still works is a re-link away, but a clobbered one is not
    recoverable from here.
    """
    key = env_key(portfolio)
    existing = env.read_text(encoding="utf-8") if env.exists() else ""
    if re.search(rf"^\s*(export\s+)?{re.escape(key)}=", existing, re.MULTILINE):
        return f"{key} already set in {env} — left alone. Delete that line to re-link."
    with env.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        linked = datetime.now(UTC).date().isoformat()
        handle.write(f"\n# Plaid access token for the {portfolio} account. Read-only "
                     f"(investments), linked {linked}.\n{key}={token}\n")
    return f"wrote {key} to {env}"
