"""What does Kraken's ``BalanceEx`` actually return, and how do its codes join to a price?

Prints every balance code with its fields, the USD pair it joins to, and a mark — to the
terminal only. The repo is public: findings below record the *shapes* Kraken uses, never this
account's assets or amounts.

WHAT IT FOUND, 2026-10-15, on a Query-Funds-only key:

1. **``BalanceEx`` lists every asset the account ever touched, mostly at zero**, and most
   of the non-zero ones are cents of dust. Each value is ``{"balance", "hold_trade"}`` as
   strings; ``balance`` is the total (``hold_trade`` is the part reserved by open orders).
2. **A suffixed balance is spelled with the altname, a plain one with the asset code**:
   ``XXBT`` but ``XBT.M``. Pairs key on the code (``XXBTZUSD``, ``base: XXBT``), so joining a
   suffixed balance to its price goes through ``/0/public/Assets``' altname.
3. **Suffixes seen in ``Assets``**: ``.S .M .P`` (earn), ``.HOLD`` (on hold, fiat and USD),
   and ``.CORE .INK .TEMPO`` — chain-wrapped variants (``USDC.CORE``, ``ETH.INK``,
   ``USDT0.TEMPO``).
4. **A batched ``Ticker`` call fails whole on one unknown pair** (``EQuery:Unknown asset
   pair``). Safe only when every name comes from ``AssetPairs``.
5. **``AssetPairs`` has ``.d`` dark-pool twins** of real pairs (``XXBTZUSD.d``); skip them.
6. A signed ``OpenOrders`` call answered ``EGeneral:Permission denied`` — not proof the key
   cannot trade, since permissions are independent checkboxes; the key page is the evidence.
   Removed from this script so nothing committed signs outside ``PRIVATE_PATHS``.

RUN IT:

    uv run python scripts/probe_kraken_account.py
"""

from __future__ import annotations

from oracle import kraken_account as ka


def main() -> None:
    balances = ka.private("/0/private/BalanceEx")
    pairs = ka.public("/0/public/AssetPairs")
    usd_by_base = {
        info["base"]: (name, info.get("wsname"))
        for name, info in pairs.items()
        if info.get("quote") == "ZUSD" and not name.endswith(".d")
    }
    print(f"{len(balances)} balance codes\n")
    for code, fields in sorted(balances.items()):
        base, _, suffix = code.partition(".")
        pair = usd_by_base.get(base)
        mark = None
        if pair:
            ticker = ka.public("/0/public/Ticker", {"pair": pair[0]})
            mark = next(iter(ticker.values()))["c"][0]
        print(f"{code:12} suffix={suffix or '-':5} fields={fields}")
        print(f"{'':12} pair={pair} mark={mark}\n")

    try:
        ka.public("/0/public/Ticker", {"pair": "XXBTZUSD,NOPEUSD"})
        print("batched Ticker with one unknown pair: tolerated")
    except ka.KrakenError as exc:
        print(f"batched Ticker with one unknown pair: fails whole — {exc}")


if __name__ == "__main__":
    main()
