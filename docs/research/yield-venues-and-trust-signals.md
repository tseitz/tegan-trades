# Yield venues and trust signals for the assets we hold

Research for [#49](https://github.com/tseitz/tegan-trades/issues/49), part of the map in
[#46](https://github.com/tseitz/tegan-trades/issues/46). Every number below was measured on
**2026-09-14 UTC** against the source that owns it — a public RPC node, or the API named beside
it. Nothing here was taken from a secondary write-up, and every claim carries the command that
reproduces it. Prices and APRs move; the *shapes* are what this document is for.

No key material was read. All on-chain reads went through public RPC endpoints
(`ethereum-rpc.publicnode.com`, `base-rpc.publicnode.com`, `api.mainnet-beta.solana.com`), not
through this repo's Alchemy key.

---

## 0. What the repo can already see, and the one thing it cannot

`packages/oracle/src/oracle/wallet.py` reads Alchemy's
[`/assets/tokens/by-address`](https://www.alchemy.com/docs/data/token-api/token-api-endpoints/token-api-endpoints/get-tokens-by-address)
across five EVM networks plus Solana, and `portfolios.write_positions` already persists the
**contract address of each holding in the `figi` field**. That is the fact question 3 turns on:
the identifier a yield lookup needs is already in `data/portfolios/crypto.yaml`, written nightly
by `wallet-sync`, and nothing new has to be collected to get it.

The current file carries:

| ticker | `figi` (contract) | chain |
|---|---|---|
| AERO | `0x940181a94a35a4569e4529a3cdfb74e38fd98631` | Base |
| ETH | *(none — native)* | Ethereum |
| METH | `0xd5f7838f5c461feff7fe49ea5ebaf7728bb0adfa` | Ethereum |
| SOL | *(none — native)* | Solana |
| STKAAVE | `0x4da27a545c0c5b758a6ba100e3a049001de870f5` | Ethereum |
| WETH | `0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2` | Ethereum |

**The thing it cannot see: a Solana native stake account is not a token.** Alchemy's endpoint
returns SPL token balances and the native lamport balance. A delegated stake is a separate
account *owned by the Stake program*
(`Stake11111111111111111111111111111111111111`), holding lamports of its own — so no
token-balance endpoint can return it, on Alchemy or anywhere else.

This is not hypothetical. The Solana address in `crypto.yaml`
(`DEs2iLbuF34RpeLaSXyt2s5EGq5Htdaw4und4CXUQNEM`) **has 11 delegated stake accounts holding
2.3400 SOL**, against a liquid balance of 0.4096 SOL. Every one of them is invisible to this
repo today.

```bash
curl -s -X POST https://api.mainnet-beta.solana.com -H 'Content-Type: application/json' -d '{
 "jsonrpc":"2.0","id":1,"method":"getProgramAccounts",
 "params":["Stake11111111111111111111111111111111111111",
  {"encoding":"jsonParsed","filters":[{"memcmp":{"offset":44,
    "bytes":"DEs2iLbuF34RpeLaSXyt2s5EGq5Htdaw4und4CXUQNEM"}}]}]}'
```

Offset 44 is the `withdrawer` authority in the stake account layout — 4 bytes of enum
discriminant, then `rent_exempt_reserve` (8), then `staker` (32) at offset 12, then `withdrawer`
(32) at offset 44. Confirmed empirically: every account returned has our address as
`meta.authorized.withdrawer`. The stake authority on all eleven is
`stWirqFCf2Uts1JBL1Jsd3r6VBWhgnpdPxCTe1MFjrq`, i.e. they were delegated through a wallet UI that
keeps staking rights while leaving withdrawal with the owner.
([`getProgramAccounts` docs](https://solana.com/docs/rpc/http/getprogramaccounts),
[staking overview](https://solana.com/docs/references/staking))

Note in passing: `crypto.yaml` records `SOL: 8.91519844` from the 2026-09-11 sync, which does
not reconcile with 0.4096 liquid + 2.3400 staked today. Worth a probe before anyone builds on
that row; this document does not diagnose it.

---

## 1. Yield venues per asset, on chains already reachable

Reachable chains are the six in `wallet.NATIVE` / `DEFAULT_*_NETWORKS`: Ethereum, Base,
Arbitrum, Optimism, Polygon, Solana.

### AERO (Base) — the best venue is not in any API

Locking AERO produces **veAERO**, an ERC-721 vote-escrow NFT, for up to **four years**. The
locker then receives three separate income streams, per the protocol's own specification:

1. **Rebase** from `Minter` via `RewardsDistributor`, proportional to locked AERO;
2. **Trading fees** from the pools they voted for, via `FeesVotingReward` — "the fees
   relinquished by LP depositors depositing their LP token in to the gauge";
3. **Bribes/incentives** via `BribeVotingReward` — "externally deposited rewards of whitelisted
   tokens used to incentivize users to vote for a given pool".

([SPECIFICATION.md](https://github.com/aerodrome-finance/contracts/blob/main/SPECIFICATION.md),
[README.md](https://github.com/aerodrome-finance/contracts/blob/main/README.md))

Verified on Base against the `VotingEscrow` at
`0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4`:

```
VotingEscrow.token()       -> 0x940181a94A35A4569E4529A3CDfB74e38FD98631  (= AERO, = our figi)
VotingEscrow.name()        -> veNFT
VotingEscrow.supply()      -> 1,048,974,403 AERO locked
AERO.totalSupply()         -> 1,978,450,301 AERO
Minter.weekly()            -> 8,969,150 AERO / week
```

So **53.0% of AERO supply is already locked**, and weekly emission is 0.453% of supply
(≈26.5% annualised if sustained). Holding AERO unlocked is a decision to be diluted by that
figure while half the supply is not; that asymmetry is the whole argument for the lock, and it
is arithmetic on two on-chain reads, not a projection.

**The catch for this repo: no free API serves a veAERO APR.** DefiLlama's yields API has zero
single-sided `aerodrome-*` pools — locking is not modelled as a pool. `api.aerodrome.finance`
does not resolve. The number is computable on-chain (sum `FeesVotingReward` +
`BribeVotingReward` claimable across voted gauges, via the protocol's `LpSugar` reader), but
that is real work, not an API call.

What *is* served, for AERO left liquid (all Base, from DefiLlama `/pools`):

| project | symbol | TVL | APY | split | pool id |
|---|---|---|---|---|---|
| extra-finance-leverage-farming | AERO | $6.72M | 2.35% | all base | `590bc66e-6cbf-467c-baf8-93f98b2679e4` |
| moonwell-lending | AERO | $2.28M | 1.38% | 1.28 base / 0.10 reward | `52fdf254-e837-4f8f-955f-993c3fb31f91` |
| aerodrome-v1 | USDC-AERO LP | $33.9M | 25.08% | all reward, `ilRisk: yes` | `d32f9c01-47d1-4077-8c73-8b91b08d1e91` |
| aerodrome-slipstream | WETH-AERO LP (CL200) | $2.90M | 50.34% | 28.94 base / 21.40 reward, `ilRisk: yes` | `3aebe700-db0b-49e2-82f6-564acdfae434` |

The LP rows are a different asset — they convert a directional AERO position into a two-sided
one with impermanent loss. DefiLlama flags that as `ilRisk: "yes"` and `exposure: "multi"`, both
machine-readable, so a treasury view can separate "same exposure, now earning" from "different
exposure entirely" without judgement.

### ETH and WETH — the deepest and dullest menu

Two distinct moves, and they are not the same risk.

**(a) Stake it.** Liquid staking turns ETH into a receipt token that accrues value. All
Ethereum, from DefiLlama `/pools`:

| project | symbol | TVL | APY | pool id |
|---|---|---|---|---|
| lido | stETH | $23.9B | 2.24% | `747c1d2a-c668-4682-b9f9-296708a3dd90` |
| ether.fi-stake | weETH | $5.33B | 2.33% | `46bd2bdf-6d92-4066-b482-e885ee172264` |
| rocket-pool | rETH | $1.30B | 2.17% | `d4b3c522-6127-4b89-bedf-83641cdcd2eb` |
| meth-protocol | mETH | $606M | 2.19% | `b9f2f00a-ba96-4589-a171-dde979a23d87` |
| coinbase-wrapped-staked-eth | cbETH | $475M | 2.35% | `0f45d730-b279-4629-8e11-ccb5cc3038b4` |
| stakewise-v3 | osETH | $395M | 2.26% | `4d01599c-69ae-41a3-bae1-5fab896f04c8` |

The spread across the whole field is ~0.2 percentage points. Venue choice here is almost
entirely a trust question, not a yield question — which is exactly why question 4 matters more
than question 2 for this asset.

**(b) Lend it.** WETH supply markets, reachable chains only, TVL > $20M, non-zero APY:

| chain | project | TVL | APY | split | pool id |
|---|---|---|---|---|---|
| Ethereum | aave-v3 | $754M | 1.44% | all base | `e880e828-ca59-4ec6-8d4f-27182a4dc23d` |
| Ethereum | sparklend | $274M | 1.47% | all base | `24195b31-d749-445f-bf9e-b65aa025ebdd` |
| Ethereum | aave-v3 (Umbrella) | $26.3M | 5.05% | **1.44 base / 3.61 reward** | `82969010-fb6e-4af6-a72c-55c8da9c78cf` |
| Base | aave-v3 | $28.3M | 1.77% | all base | `23405eee-97e7-4b8e-8625-19c3a36047e8` |
| Arbitrum | aave-v3 | $31.6M | 0.93% | all base | `e302de4d-952e-4e18-9749-0a9dc86e98bc` |
| Polygon | aave-v3 | $24.6M | 0.27% | all base | `2b9bf1c6-a018-4e93-a32f-7cf6ccd311fc` |
| Ethereum | compound-v3 | $44.4M | 1.29% | all base | `85c57261-b75b-4447-a115-d79b1a7de8ed` |

The Aave Umbrella row is the instructive one: 5.05% headline, of which **3.61 points are
`apyReward`** — incentive, not interest — and the position is slashable (see §4 on what that
split buys you). Its `underlyingTokens` is `0x0bfc9d54Fc184518A81162F8fB99c2eACa081202`, which
reads on-chain as `waEthWETH`, and its `rewardTokens` is
`0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8` = `aEthWETH`.

Also live on Base: Aerodrome WETH pairs (LP, two-sided), Moonwell, Morpho Blue, Euler v2.

### mETH — **already earning**, and the rate is verifiable two ways

mETH is Mantle LSP's receipt token: "a reward accumulating and permissionless ERC-20 token".
It is a *value-accruing* LST — the unit count never changes, the redemption rate climbs.
([mantle-lsp/contracts README](https://github.com/mantle-lsp/contracts))

```
Staking(0xe3cBd06D7dadB3F4e6557bAb7EdD924CD1489E8f).mETHToETH(1e18) -> 1.0987437390767083 ETH
```

DefiLlama's chart for the same pool reports `pricePerShare: 1.09874` at the same moment, and its
value a year earlier was 1.07528 — a **realised 2.182% over the trailing year**, against a
quoted `apy` of 2.193%. The two independent sources agree to five significant figures, which
makes this the template for auditing any APR claim in this repo: *the exchange rate is the
truth, the quoted APR is a derivative of it.*

```bash
curl -s https://yields.llama.fi/chart/b9f2f00a-ba96-4589-a171-dde979a23d87
```

Caveat from the protocol's own docs: mETH now runs a **LiquidityBuffer**
(`0x006fad88c35d973a87e451cf8d000c7e83dad409`) which "Stores and manages idle ETH", can
"Authorize external position managers to interact with blue-chip DeFi protocols" and "Extract
the generated yield" — with `PositionManagerAAVE`
(`0xb484207115CDec6B24F02da5Ff02b8d9adbc11BC`) "Authorized by the LiquidityBuffer to interact
with AAVE". **So mETH's risk surface is no longer only Ethereum validation; part of it is Aave
counterparty risk.** `cmETH` is a *different* token carrying restaking risk on top; we do not
hold it.
([docs.mantle.xyz/meth staking contracts](https://docs.mantle.xyz/meth/components/smart-contracts/staking-meth))

Second-order venues for mETH exist but are thin: `dolomite` mETH ($3.76M, 0% APY),
`uniswap-v3 WETH-METH` ($1.69M, 0.0001%). There is no meaningful place to put mETH to work
beyond the staking it already represents.

### SOL — **already earning**, natively, and invisibly

The 2.3400 SOL in 11 stake accounts (§0) is delegated and accruing. Network-wide inflation right
now:

```bash
curl -s -X POST https://api.mainnet-beta.solana.com -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"getInflationRate"}'
# -> {"epoch":1034,"total":0.036492746728610595,"validator":0.036492746728610595,"foundation":0.0}
```

3.649% total, all of it to validators. A delegator receives that less the validator's commission,
plus MEV and priority-fee sharing where the validator passes it on — which is why the liquid
staking tokens below clear 4.6-5.5% rather than 3.6%.

Liquid staking alternatives for the 0.4096 SOL sitting liquid, all Solana, all `exposure:
single` (DefiLlama `/pools`):

| project | symbol | TVL | APY | pool id |
|---|---|---|---|---|
| jito-liquid-staking | JitoSOL | $1.02B | 4.94% | `0e7d0722-9054-4907-8593-567b353c0900` |
| jupiter-staked-sol | jupSOL | $515M | 5.50% | `52bd72a7-9e81-4112-abb4-71673e8de9bf` |
| marinade-liquid-staking | mSOL | $229M | 6.12% | `b3f93865-5ec8-4662-90a0-11808e0aa2bd` |
| helius-staked-sol | hSOL | $92.1M | 5.27% | `d7e101d6-8e6c-4348-9c5f-62398872a301` |
| blazestake | bSOL | $90.9M | 4.86% | `387d6732-59f0-4ae0-8a88-aba75a5cbe4a` |

And lending: `kamino-lend` SOL 4.46% ($26.3M, `525b2dab-ea6a-4cbc-a07f-84ce561d1f83`),
`jupiter-lend` WSOL 4.01% ($110M, `86d5dc3c-682f-4227-b1c9-7e51c6e60cda`).

A liquid staking token *is* an SPL token, so unlike a native stake account it would show up in
`wallet.py` today. The wallet currently holds none — I enumerated all 57 non-zero SPL token
accounts and found no LST among them, only memecoins.

### stkAAVE — **already earning ~2.20%**, and no free API says so

`0x4da27a545c0c5b758a6ba100e3a049001de870f5` is definitively staked AAVE, by its own
declaration:

```
name()          -> "Staked Aave"
symbol()        -> "stkAAVE"
STAKED_TOKEN()  -> 0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9   (AAVE)
REWARD_TOKEN()  -> 0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9   (AAVE)
totalSupply()   -> 2,486,468.748 stkAAVE
assets(self)    -> emissionPerSecond = 1,736,111,111,111,111 wei AAVE/s
                   lastUpdateTimestamp = 1789349699 (2026-09-14)
DISTRIBUTION_END() -> 4857034299 (year 2123 — emissions are not near expiry)
COOLDOWN_SECONDS() -> 172800  (2 days)
UNSTAKE_WINDOW()   -> 172800  (2 days)
```

54,750 AAVE/year against 2,486,469 staked = **2.2019% APR, paid in AAVE**.

Two things to carry forward:

- **The cooldown measured on-chain (2 days) is not the figure the docs discuss.** Aave's current
  documentation describes "a 20-day cooldown period and 2-day withdrawal window" — but that text
  is about **Umbrella**, the replacement system, not the legacy stkAAVE contract. Read the
  contract, not the docs, and re-read it before acting; governance can change it.
- **Slashing is being wound down, which is why the yield is what it is.** Aave's docs:
  "stkAAVE and stkABPT will remain active during the transition, with slashing disabled once
  Umbrella reaches sufficient scale." Umbrella instead stakes aTokens and GHO, and slashing
  there "is triggered automatically by UmbrellaCore when a deficit in the corresponding Aave
  pool exceeds the configured offset." ([aave.com/docs/aave-v3/umbrella](https://aave.com/docs/aave-v3/umbrella))
  A reward that exists to price slashing risk, with the slashing removed, is a reward on a
  schedule that governance can end. Treat 2.20% as a governance parameter, not a market rate.

**DefiLlama's yields API does not carry stkAAVE.** I searched all 16,772 pools: there is no
`aave-*` project with a stkAAVE pool, and the only `STKAAVE` symbols present are two tiny
Uniswap v4 STKAAVE-AAVE LP pairs. The only source for this number is the contract.

---

## 2. A free API for current APR: DefiLlama yields, with limits

**`GET https://yields.llama.fi/pools`** — verified working, no key, no auth.

- Returned HTTP 200, 11,522,450 bytes, **16,772 pools** on 2026-09-14.
- Response is `{"status":"success","data":[...]}`.
- Cloudflare-cached (`cf-cache-status: HIT`, `expires` ~35 minutes out). One fetch per nightly
  run is more than enough; it is an 11.5 MB download, so do not poll it.
- No rate-limit headers are advertised.

Per-pool fields that matter, using Lido stETH as the shape:

```json
{"chain":"Ethereum","project":"lido","symbol":"STETH","tvlUsd":23908962564,
 "apyBase":2.235,"apyReward":null,"apy":2.235,"rewardTokens":null,
 "pool":"747c1d2a-c668-4682-b9f9-296708a3dd90",
 "apyPct1D":-0.081,"apyPct7D":0.002,"apyPct30D":0.053,
 "stablecoin":false,"ilRisk":"no","exposure":"single",
 "predictions":{"predictedClass":"Stable/Up","predictedProbability":72,"binnedConfidence":2},
 "poolMeta":null,"mu":3.42826,"sigma":0.05387,"count":1563,"outlier":false,
 "underlyingTokens":["0x0000000000000000000000000000000000000000"],
 "apyMean30d":2.23403,"apyBaseInception":null}
```

**The identifiers.** A pool is keyed by an opaque UUID in `pool` (e.g.
`b9f2f00a-ba96-4589-a171-dde979a23d87` for mETH). `project` is a DefiLlama protocol slug that
joins to `/protocols` (§4). `chain` is a display name, and **it is not Alchemy's network name**:

| Alchemy network | DefiLlama `chain` | pools |
|---|---|---|
| `eth-mainnet` | `Ethereum` | 5147 |
| `base-mainnet` | `Base` | 2995 |
| `solana-mainnet` | `Solana` | 2665 |
| `arb-mainnet` | `Arbitrum` | 935 |
| `matic-mainnet` | `Polygon` | 594 |
| `opt-mainnet` | **`OP Mainnet`** | 426 |

`Optimism` and `Matic` return **zero** pools. That is the same class of trap as
`solana-mainnet` vs `sol-mainnet` already recorded in `wallet.py`: a name that looks right and
silently yields nothing. Any mapping between the two vocabularies needs a test that fails when a
chain goes to zero, not a lookup that quietly returns an empty list.

**`GET https://yields.llama.fi/chart/{pool}`** — also free. Verified: 1,002 daily points for the
mETH pool, back to 2023-12-18, each carrying `tvlUsd`, `apy`, `apyBase`, `apyReward`, and
`pricePerShare`. This is how you audit a quoted APR against realised return (§1, mETH).

**What is NOT free any more** — both returned `402 Upgrade to the paid API plan`:

- `https://yields.llama.fi/lsdRates` (would have been the direct liquid-staking rate feed)
- `https://yields.llama.fi/poolsBorrow`

**Alternatives checked and rejected:**

- **CoinGecko** `/coins/{platform}/contract/{address}` works with no key and is genuinely useful
  for classification (§3) — but the free tier is hard rate-limited. My second call in the same
  minute returned `HTTP 429` with `retry-after: 31`. Unusable for a fan-out over a portfolio
  without a demo key.
- **Sanctum** `extra-api.sanctum.so/v1/apy/latest` (Solana LST rates) returns **`0.0` with an
  empty `errs` object** for both ticker and mint identifiers. A silently wrong answer is worse
  than an error; do not use it.
- **`api.aerodrome.finance`** does not resolve. No public REST for Aerodrome.
- **`aave-api-v2.aave.com`** returns a 301 to nowhere useful. Aave's data path is the contracts
  and the subgraph.
- **Jito's** `www.jito.network/api/...` is behind Cloudflare bot protection (403).

**Conclusion for question 2.** DefiLlama `/pools` + `/chart/{pool}` is the only free, general,
machine-readable APR feed that actually answers. It covers ETH/WETH lending and staking, SOL
liquid staking and lending, and AERO lending and LPs. It does **not** cover the two positions we
already hold — veAERO locking and stkAAVE — nor native Solana staking. Those three need
contract reads.

---

## 3. Recognising an existing yield position from holdings alone

**Yes for both named cases, and the strongest identification is on-chain, not from any API.**

### stkAAVE → staked AAVE: definitive

The contract *tells you*. `STAKED_TOKEN()` (selector `0x312f6b83`) on
`0x4da27a545c0c5b758a6ba100e3a049001de870f5` returns
`0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9`, the AAVE token on Ethereum mainnet. `REWARD_TOKEN()`
returns the same address. This is not an inference from a symbol string — it is the staking
contract naming its own underlying, and the same read works across every Aave `StakedTokenV3`
deployment.

### mETH → staked ETH: definitive

`0xd5F7838F5C461fefF7FE49ea5ebaF7728bB0ADfa` reports `name()` and `symbol()` both `"mETH"`,
and it is listed as `METH` in the protocol's own deployment table alongside
`Staking = 0xe3cBd06D7dadB3F4e6557bAb7EdD924CD1489E8f`
([mantle-lsp/contracts](https://github.com/mantle-lsp/contracts)). The token itself exposes no
link back to the staking contract — `stakingContract()` reverts — so the pairing is a fact you
record once, not one you discover. Given the pair, `Staking.mETHToETH(1e18)` proves the
relationship *and* quantifies it: 1 mETH = 1.0987 ETH today, so 9.87% of the position is
accumulated reward.

### The general problem: no free registry maps a receipt contract to a pool

This is the honest limitation. DefiLlama's `/pools` gives `underlyingTokens` but **not the
receipt token's address** — the mETH pool's `underlyingTokens` is the zero address (meaning ETH),
not `0xd5F7...`. So you cannot go contract → pool through that API. Three partial paths exist:

1. **`https://coins.llama.fi/prices/current/{chain}:{address}`** — free, no key, takes a contract
   address directly and returns `{symbol, decimals, price, confidence, timestamp}`. Verified for
   all three of our ERC-20s in one call. It is contract-keyed and it confirms identity, but it
   says nothing about yield.

2. **CoinGecko `/coins/{platform}/contract/{address}`** — free, no key, and it returns a
   `categories` array that *does* classify:
   - `0xd5f7838f...` → `id: "mantle-staked-ether"`, categories include **`"Liquid Staking
     Tokens"`, `"Liquid Staked ETH"`, `"Liquid Staking"`**
   - `0x4da27a54...` → `id: "staked-aave"`, categories include **`"Liquid Staking Tokens"`,
     `"Aave Tokens"`, `"Decentralized Finance (DeFi)"`**

   This is the only machine-readable *classification by contract address* I found. Two caveats:
   the taxonomy is editorial (stkAAVE is not a liquid staking token in any technical sense — it
   is a slashable governance stake — yet it carries that label), and the rate limit (§2) makes
   a per-holding sweep impractical without a demo key.

3. **A hand-kept map in `cfg/`.** Contract address → `{protocol, underlying ticker, DefiLlama
   pool id, rate-read}`. The portfolio has six rows. The set of yield-bearing tokens a person
   actually holds is small and changes slowly, and this is the only path that is correct by
   construction rather than by a third party's editorial judgement. It also mirrors what the repo
   already does for ambiguous tickers with `prefer:` and `cfg/oracle_map.yaml` — a statement about
   an instrument, which the existing code already treats as the only identity claim worth
   trusting.

### What symbol-matching would cost you

Do not match on symbol. `METH` in DefiLlama's pool set also names a Raydium `METH-WETH` pair on
Solana at 432% APY and a `SOMETHING-SPX` pool caught by substring; `STKAAVE` names two Uniswap v4
LP pairs, one at 19.9% APY. Either would hand the treasury view a confident wrong number — the
same failure mode `wallet._fold` already refuses on the holdings side, and it deserves the same
refusal here.

---

## 4. Data available for a trust judgement

Reporting only. This does not propose a gate.

### Machine-readable, free, verified today

All from `https://api.llama.fi/protocols` (8,248 protocols, 8.8 MB) unless noted. Coverage
percentages are measured over the whole set, because the gaps are the point — a signal present
for a third of protocols cannot be a required input.

| Signal | Field | Coverage | Notes |
|---|---|---|---|
| Audit count/tier | `audits` | 35.5% | A string code, not a count: `"0"` 5308, `"2"` 2785, `"3"` 90, `"1"` 51, `"5"` 1. Semantics are **not documented** — `DefiLlama/defillama-server` and `defillama-app` both 404 on GitHub now, and `DefiLlama-Adapters`' README says only "send a mail to metadata@defillama.com". Treat as an opaque ordinal. |
| Audit links | `audit_links` | 34.7% | URLs to the auditor or a security page. Aave v3 → `["https://aave.com/security"]`; Jito → two links including a direct Neodyme PDF. |
| **Fork lineage** | `forkedFromIds` | **25.1%** | Array of DefiLlama protocol ids. **Aerodrome V1 → `["1407"]` = Solidly.** Aerodrome Slipstream → `["2198"]` = Uniswap V3. Velodrome V2 also → Solidly. 20+ protocols share the Solidly parent. Resolve ids against the same `/protocols` payload. The dedicated `/forks` endpoint is now **402 paid**; this field is the free substitute and it is sufficient. |
| Listing date | `listedAt` | 79.7% | Unix seconds. **This is when DefiLlama listed it, not when it deployed.** Aerodrome V1 = 1693274059 (2023-08-29); mETH Protocol = 1701703365 (2023-12-04). Lido has *no* `listedAt` at all, which tells you the field cannot carry the age question alone. |
| **Real age** | first non-zero point in `/protocol/{slug}` `tvl[]` | complete | Free, and it is the honest age signal. Aave v3 returns **1,647 daily points** starting 2022-03-14. A protocol cannot fake having had TVL for four years. |
| TVL history | `/protocol/{slug}` → `tvl[]`, `chainTvls`, `tokens`, `tokensInUsd` | complete | Daily series. 29 MB for aave-v3 — fetch per protocol on demand, never in bulk. |
| Pool-level TVL + APY history | `/chart/{pool}` | complete | 1,002 daily points for mETH, with `pricePerShare`. Lets you see whether an APY has ever been real. |
| Incident record | `hallmarks` | 6.9% | `[[unix, "label"]]`. Aave v3 carries `[1776470400, "KelpDAO hack"]`; Lido carries `"stETH depeg"`, `"FTX collapse"`, `"UST depeg"`. Curated, sparse, but high-signal when present. |
| Hack database | `https://api.llama.fi/hacks` | 1,268 entries | Free. Fields: `date, name, classification, technique, amount, chain, bridgeHack, targetType, returnedFunds, defillamaId`. **528 of 1,268 carry a `defillamaId`**, so joining a hack to a protocol record works for ~42% of incidents and needs a name fallback otherwise. |
| Dead / rugged / deprecated | `rugged` 1.4%, `deprecated` 8.1%, `deadFrom` 7.3%, `warningBanners` 0.6% | — | Only in `https://api.llama.fi/config` (8,252 protocols), not in `/protocols`. Low coverage but unambiguous when set — these are the cheapest disqualifiers available. |
| Open source / code | `github` 19.1%, `openSource` 0.7% | — | `openSource` is effectively unpopulated. `github` is an org-name array (Lido → `["lidofinance"]`), usable as a pointer, not as a measure. |
| Oracle dependency | `oracles` 2.9%, `oraclesBreakdown` | — | Too sparse to rely on. |
| Governance | `governanceID` 2.2%, `treasury` 3.9% | — | Snapshot space id where present (Lido → `snapshot:lido-snapshot.eth`). |
| Category | `category` | **100%** | `Lending`, `Liquid Staking`, `Dexs`, … The only universally present classifier. |
| Prior names | `previousNames` 2.3% | — | A rebrand is a thing a reader wants to know about. |

### Machine-readable from the yields API itself

| Signal | Field | Why it matters |
|---|---|---|
| **Incentive vs interest** | `apyBase` / `apyReward` / `rewardTokens` | The single most useful free trust input, and it is on every pool. Aave Umbrella WETH is 5.05% of which 3.61 is `apyReward` — an emission that governance can stop — against 1.44 of actual interest. A yield that is mostly reward is a yield with an end date. |
| Emission token identity | `rewardTokens` | Contract addresses. Tells you whether you are being paid in the thing you hold or in a governance token you would have to sell. |
| Yield stability | `apyMean30d`, `mu`, `sigma`, `count`, `apyPct1D/7D/30D` | `count` is how many daily observations exist — Lido stETH has 1,563, i.e. four years of record. A pool with `count` in the tens has no history to judge. |
| Outlier flag | `outlier` | DefiLlama's own "this number is not believable" bit. |
| Risk shape | `ilRisk`, `exposure`, `stablecoin` | Separates "same exposure, now earning" from "different asset entirely". |
| Forward guess | `predictions.{predictedClass, predictedProbability, binnedConfidence}` | DefiLlama's model, not a measurement. Noted for completeness; I would not weight it. |

### Machine-readable on-chain

| Signal | How | Note |
|---|---|---|
| Deployment age | first transaction / contract creation block | The unfakeable age. Costs an indexer or an explorer API. |
| Amount at stake | `totalSupply()` on the escrow/stake token | veAERO: 53.0% of AERO supply locked. stkAAVE: 2.49M of 16M AAVE. "How much does everyone else trust this" as a number. |
| Emission schedule | `emissionPerSecond` + `DISTRIBUTION_END()` (Aave), `Minter.weekly()` (Aerodrome) | **This is the one place a real incentive schedule is free and exact.** DefiLlama's `/emissions` and `/emission/{protocol}` unlock endpoints are now **402 paid**. |
| Exit friction | `COOLDOWN_SECONDS()`, `UNSTAKE_WINDOW()`, lock expiry | stkAAVE: 2 days + 2 days. veAERO: up to 4 years. Not trust exactly, but the cost of being wrong about it. |
| Upgradeability | EIP-1967 implementation slot, proxy admin | mETH and stkAAVE are both transparent proxies. An upgradeable contract's audit describes code that can be replaced. Readable via `eth_getStorageAt`; nobody serves it as a field. |

### NOT machine-readable

- **Audit reports themselves.** PDFs and prose. `audit_links` gives you a URL; nothing gives you
  findings, severity counts, or whether they were fixed. Code4rena, Sherlock, Spearbit and
  Immunefi have no free, stable, cross-protocol API. Whether an audit covers the *deployed*
  version is not knowable from any feed.
- **Whether a fork's diff is benign.** `forkedFromIds` tells you Aerodrome descends from Solidly.
  It cannot tell you what changed. The map's framing — "a validated fork with Lindy behind its
  parent" — needs a human or a diff review at exactly this point; the lineage edge is free, the
  validation is not.
- **Governance risk.** Admin key holders, multisig thresholds, timelock lengths, who can change
  an emission rate. Readable per-contract with effort (Aerodrome publishes
  [PERMISSIONS.md](https://github.com/aerodrome-finance/contracts/blob/main/PERMISSIONS.md));
  no cross-protocol feed exists.
- **Team identity and track record.** Nothing.
- **Insurance / backstop coverage.** No free feed.
- **DefiLlama's own `audits` semantics.** Worth repeating: the tier codes are undocumented and
  the repos that defined them are no longer public.

---

## Re-running any of this

```bash
# All DefiLlama yields, free, ~11.5 MB
curl -s https://yields.llama.fi/pools -o pools.json

# One pool's daily history incl. pricePerShare
curl -s https://yields.llama.fi/chart/b9f2f00a-ba96-4589-a171-dde979a23d87

# Protocol metadata incl. forkedFromIds / audits / listedAt / hallmarks
curl -s https://api.llama.fi/protocols -o protocols.json
curl -s https://api.llama.fi/config    -o config.json     # adds rugged/deprecated/deadFrom
curl -s https://api.llama.fi/hacks     -o hacks.json

# Identity + price by contract address, free, no key
curl -s 'https://coins.llama.fi/prices/current/ethereum:0x4da27a545c0c5B758a6BA100e3a049001de870f5'

# Classification by contract address (rate-limited to ~1 call / 30s without a key)
curl -s 'https://api.coingecko.com/api/v3/coins/ethereum/contract/0xd5f7838f5c461feff7fe49ea5ebaf7728bb0adfa'

# On-chain reads (foundry `cast`; public RPCs, no key)
ETH_RPC_URL=https://ethereum-rpc.publicnode.com cast call \
  0x4da27a545c0c5B758a6BA100e3a049001de870f5 "STAKED_TOKEN()(address)"
ETH_RPC_URL=https://ethereum-rpc.publicnode.com cast call \
  0xe3cBd06D7dadB3F4e6557bAb7EdD924CD1489E8f "mETHToETH(uint256)(uint256)" 1000000000000000000
ETH_RPC_URL=https://base-rpc.publicnode.com cast call \
  0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4 "supply()(uint256)"
```

`scripts/probe_alchemy_wallet.py` remains the right place to check chain reachability before
assuming any of the above is fetchable for a given address.
