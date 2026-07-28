# execution

Turns an approved `Candidate` into a resting bracket order on Hyperliquid.

**This is the only package in the repo that holds a private key and sends a signed write.**
Everything else reads. That asymmetry is why it is a package rather than a module inside
`oracle/`: `oracle` fetches prices and must stay incapable of moving money, the same way
`llm/` is the only place a model gets called.

## Shape

Pure first, one thin impure edge:

| Module | Pure? | Job |
|---|---|---|
| `sizing.py` | yes | equity × risk → contracts, off the engine's own stop |
| `rounding.py` | yes | the venue's tick/lot rules, which silently reject orders |
| `guards.py` | yes | every reason an order must be refused |
| `plan.py` | yes | `Candidate` → `OrderPlan` (entry + TP + SL) |
| `wire.py` | yes | the exact payload sent, and the reply parsed back |
| `config.py` | mostly | `cfg/execution.yaml` + credentials from the environment |
| `broker.py` | no | signed writes and the market list, via the official SDK |
| `store.py` | no | append-only audit log under `data/execution/` |
| `session.py` | no | the assembled seam `oracle.setups_cli` talks to |
| `cli.py` | no | `execute` — pre-flight reporting; cannot place an order |

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
