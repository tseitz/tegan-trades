# Exposure: Net Worth by Underlying Asset

[ADR-0007](0007-net-worth-reader.md) made net worth the one cross-Mandate figure, and "the one deliberate, narrow exception to 'percentages never cross a Mandate boundary'". The ask now is to see that same figure broken down by what it is actually exposed to. ETH held in the crypto wallet, a Bitwise ETF in retirement, and an ETH-treasury stock are one bet. Splitting them by Mandate hides how big that bet is. The answer is what decides a trim: "I'm heavy ETH across everything, so sell it in crypto, where the trade is cheap".

## Decision

Settled 2026-10-17. **The exception widens from "the net worth total" to "net worth and its breakdown by underlying asset".** Every other percentage still stays inside its Mandate.

- **The math lives in `core.networth`**, beside `of()`, and takes its total from `of()`. A second module that sums across Mandates would end that module's claim to be the only place that does. Sharing one valuation rule means Exposure and net worth cannot disagree about which holdings count.
- **A Holding's group comes from three sources, applied in order:**
  - its `cfg/assets.yaml` alias, which already gives WETH→ETH
  - its `cfg/oracle_map.yaml` `wraps:` entry, which already gives ETHB→ETH and HODL→BTC
  - a new `cfg/exposure.yaml` `underlying:` map, for instruments that follow one asset but are neither a spelling of it nor a fund wrapping it, such as PURR→HYPE and BMNR→ETH

  The two older files each mean something stronger than "same exposure". An alias reprices the holding, and a wrap replaces its roster fold. So the third file exists, and it may not repeat a key from the second.
- **Computed live, not recorded.** The page and `networth --by-asset` compute it on request from the current price cache. It is never written to the ledger.

## Not done

- **No history.** A trim decision needs tonight's split, not its trend. Unlike the total, this can be added later from per-book rows, if the per-holding detail is ever stored.
- **No netting of shorts.** No Mandate holds one.
