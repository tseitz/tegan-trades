"""What do Coinbase's accounts and portfolio-breakdown reads return, and do they agree?

Prints shapes to the terminal only — types, field names, zero/non-zero, whether totals
reconcile. The repo is public: findings below record the *shapes* Coinbase uses, never this
account's assets or amounts. Signs only through ``coinbase_account.private``'s allowlist.

WHAT IT FOUND, 2026-10-17, on a View-only ECDSA key:

1. **``key_permissions`` sends every boolean, ``false`` included**, so failing closed on a
   missing ``can_*`` field costs nothing on a well-scoped key.
2. **``/accounts`` cannot see staked funds.** A coin held only staked has no account there at
   all; one held both ways shows only its liquid part. Every currency ever listed comes back,
   ~85% of them at zero, all in one 250-row page.
3. **The portfolio breakdown sees everything and reconciles**: summing ``spot_positions``'
   ``total_balance_fiat`` matched ``portfolio_balances.total_balance`` to the cent. A coin held
   liquid and staked is two entries, ``ACCOUNT_TYPE_WALLET`` and ``ACCOUNT_TYPE_STAKED_FUNDS``.
   USD is ``ACCOUNT_TYPE_FIAT``. ``is_cash`` is not the same set as ``wallet.STABLES``
   — it can be false on a dollar stablecoin.
4. **Field shapes differ inside one entry**: ``total_balance_crypto`` / ``_fiat`` are floats,
   ``cost_basis`` and ``average_entry_price`` are ``{value: str, currency}``, and every
   ``portfolio_balances.total_*`` is ``{value: str, currency}``.
5. **``cost_basis`` is the entry's total, not per unit**, present on every entry here.
6. **Celo is spelled ``CGLD``** — Coinbase's pre-rebrand code.
7. **The two reads spell ``platform`` differently**: ``ACCOUNT_PLATFORM_CONSUMER`` on
   ``/accounts``, ``PLATFORM_CONSUMER`` in the breakdown.

So rows come from the breakdown, and ``/accounts`` is not in ``PRIVATE_PATHS``.

RUN IT:

    uv run python scripts/probe_coinbase_account.py
"""

from __future__ import annotations

from oracle import coinbase_account as cb


def _f(amount) -> float:
    if isinstance(amount, dict):
        amount = amount.get("value")
    try:
        return float(amount or 0)
    except (TypeError, ValueError):
        return float("nan")


def main() -> None:
    perms = cb.private("/api/v3/brokerage/key_permissions")
    print("key_permissions:", {k: v for k, v in perms.items() if k != "portfolio_uuid"})
    print("  portfolio_uuid present:", "portfolio_uuid" in perms)
    uuid = cb.check_permissions(perms)
    print("  gate: passed\n")

    breakdown = cb.private(f"/api/v3/brokerage/portfolios/{uuid}").get("breakdown", {})
    print(f"\nbreakdown keys: {sorted(breakdown)}")
    balances = breakdown.get("portfolio_balances", {})
    print("portfolio_balances fields:", sorted(balances))
    spot = breakdown.get("spot_positions", [])
    print(
        f"spot_positions: {len(spot)}, fields={sorted(set().union(*spot)) if spot else []}"
    )
    for pos in spot:
        print(
            f"  {pos.get('asset')}: account_type={pos.get('account_type')} "
            f"is_cash={pos.get('is_cash')} has_cost_basis={bool(_f(pos.get('cost_basis')))}"
        )
    for key in (
        "equity_positions",
        "prediction_markets_positions",
        "perp_positions",
        "futures_positions",
    ):
        print(f"{key}: {len(breakdown.get(key) or [])}")

    total = _f(balances.get("total_balance"))
    summed = sum(_f(p.get("total_balance_fiat")) for p in spot)
    gap = summed - total
    print(
        f"\nsum(spot total_balance_fiat) - total_balance = {gap:+.2f} "
        f"({gap / total:+.3%} of total)"
        if total
        else "\ntotal_balance is zero"
    )


if __name__ == "__main__":
    main()
