# execution

Turns an approved `Candidate` into a resting bracket order — a limit entry with a take-profit
and a stop-loss that arm only once it fills.

**Two venues, one Protocol.** `hyperliquid` places perps (crypto, and equities via the `xyz`
HIP-3 builder); `alpaca` places US equities and ETFs, long or short, at a regulated
broker-dealer. They differ in almost every mechanic — an EIP-712 signature against an API
key pair, a three-order grouping against a nested OTOCO, a reverse-engineered tick grid
against SEC Rule 612 — and in nothing about intent. Both refuse to leave an entry resting
without a stop attached to it.

Which one runs is `venue:` in `cfg/execution.yaml`, and **the network belongs to the venue**:
`testnet | mainnet` for Hyperliquid, `paper | live` for Alpaca. Naming a venue without a
network gets that venue's rehearsal, never the other's. Both real-money networks need the same
typed confirmation, which is why the check reads `venues.REAL_MONEY` rather than comparing
against `"mainnet"` — a comparison that knows one spelling waves the other through.

**The two accounts are separate pools and `risk_pct` applies to each independently.** That
keeps the equity and crypto books legible as separate books, and it means running both risks
`risk_pct` twice in aggregate.

**This is the only package in the repo that holds a private key and sends a signed write.**
Everything else reads. That asymmetry is why it is a package rather than a module inside
`oracle/`: `oracle` fetches prices and must stay incapable of moving money, the same way
`llm/` is the only place a model gets called.

## Shape

Pure first, one thin impure edge:

| Module | Pure? | Job |
|---|---|---|
| `sizing.py` | yes | equity × risk → contracts, off the engine's own stop |
| `rounding.py` | yes | Hyperliquid's tick/lot rules, which silently reject orders |
| `shares.py` | yes | the equity grid — whole shares, and Rule 612's two price tiers |
| `guards.py` | yes | every reason an order must be refused |
| `plan.py` | yes | `Candidate` → `OrderPlan` (entry + TP + SL), on the venue's grid |
| `venues.py` | yes | which venues exist, and which networks spend real money |
| `wire.py` | yes | the Hyperliquid payload sent, and the reply parsed back |
| `alpaca_wire.py` | yes | the same, for Alpaca's OTOCO |
| `config.py` | mostly | `cfg/execution.yaml` + credentials from the environment |
| `broker.py` | no | signed writes and the market list, via the official SDK |
| `alpaca_broker.py` | no | the same, over Alpaca's REST API |
| `store.py` | no | append-only audit log under `data/execution/` |
| `session.py` | no | the assembled seam `oracle.setups_cli` talks to |
| `cli.py` | no | `execute` — pre-flight reporting; cannot place an order |

The grid is picked per market, not per process: `Market.grid` selects Hyperliquid's
significant-figure rules or the equity penny grid. They genuinely differ — the perp rule allows
three decimal places on a $29 stock, which Rule 612 does not permit and Alpaca rejects.

The market list comes off the broker rather than a separate fetch, so a coin it reports is by
construction a coin the SDK can resolve to an asset index.

## Two things that will bite

**HIP-3 markets must be requested by name.** The SDK loads only the core book unless
`perp_dexs` names the builders, so `xyz:GOLD` otherwise fails to resolve — on exactly the
non-crypto assets the roster talks about most. `oracle.execute.hyperliquid_dexs()` derives
them from `cfg/venue_map.yaml`.

**Which balance is the collateral depends on the account's mode**, and reading the wrong one
makes a fully funded account report `$0.00` — indistinguishable from an empty one. The venue
reports the mode at `info type=userAbstraction`, so it is detected, never configured.

| Mode | Collateral | Per-dex? |
|---|---|---|
| `unifiedAccount` / `portfolioMargin` | the **spot** balance of the collateral token; `clearinghouseState` reads 0 regardless of funding | no — one pool shared by every dex |
| manual / standard | the perps margin summary | yes — each dex has its own pool |

An unrecognised mode falls back to the perps balance, which fails closed: it reports 0 and
refuses to trade rather than sizing against something that may not be collateral.

**A listed market is not necessarily a traded one.** HIP-3 lets anyone with a HYPE stake
deploy a perp market *and operate its oracle*, so `cfg/venue_map.yaml` naming a symbol says
nothing about whether it has a book. Measured on mainnet 2026-07-27:

| Market | 24h volume | Open interest | Spread |
|---|---|---|---|
| `xyz:SP500` | $457M | $483M | 0.001% — tighter than core-book BTC |
| `xyz:URNM` | $133k | $1.0M | 0.255% |
| `xyz:DXY` | $0 | $0 | **nothing quoted on either side** |

`venue_map.yaml` routes DXY to that last one. The exposure is the **stop**, not the entry: an
entry is a resting limit that simply won't fill in a dead market, but a stop is a market
order that must fill at the worst moment, and positions are held ~21 days. So the floors
measure *durability*, not instantaneous depth. Applied to every market, not just HIP-3 ones —
a gate with an exemption list is a gate someone eventually routes around.

Enforced on mainnet only. Testnet books are mock (every HIP-3 market there fails `no_book`),
so enforcing would block everything while protecting nothing — but the verdict is still
computed and printed there, worded so it never claims to predict the real market.

## Safety

Testnet is the default. Mainnet requires an explicit `--network mainnet` *and* a typed
confirmation — a flag alone is one keystroke away from a real fill.

The sandbox is **not** a safety layer here (outbound HTTPS is unrestricted), so every
refusal lives in `guards.py` and is unit-tested.

## Credentials

`.env` only, never `cfg/` (which is committed):

```
HYPERLIQUID_ACCOUNT_ADDRESS=0x...   # the account being traded
HYPERLIQUID_SECRET_KEY=0x...        # an API/agent wallet key, NOT your main wallet
```

Generate an API wallet under **More → API** in the Hyperliquid UI. An agent wallet can
place and cancel orders but cannot withdraw, which is the whole reason to use one.
