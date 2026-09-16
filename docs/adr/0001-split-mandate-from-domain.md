# Split Mandate from Domain

`domain` in `data/portfolios/*.yaml` did two unrelated jobs: it picked the price source (`crypto` vs `stock`) and — via `oracle/benchmarks.py`'s domain-keyed lookup — it picked what the account was measured against. Every non-crypto account (retirement, savings, robinhood) landed on the same two-entry table and got `^GSPC`, regardless of whether beating the S&P was ever the point.

We introduce **Mandate** as the purpose an account serves — what it's measured against and how much risk it may take — and leave **Domain** to keep doing exactly what it already does (asset class, price routing). One mandate per portfolio file, always: if a single broker login ever needs two purposes (e.g., a SoFi crypto sleeve that goes risk-on while SoFi savings stays conservative), that's a new file, split via the `plaid_accounts:` narrowing the sync already supports — not two mandates sharing a file.

## Schema

```yaml
mandate:
  name: retirement              # free label, one per file — not an enum, no hidden risk/domain meaning
  benchmarks:                   # 1-3 entries, tagged union:
    - type: symbol
      key: sp500                #   benchmarks.py resolves the key to a real series
    - type: held_flat            #   this mandate's own current holdings, never rebalanced —
                                  #   the "should I just stop trading" baseline
  horizon: macro                 # scalp | swing | position | macro — absorbed from the old top-level field
  risk_posture: conservative     # conservative | moderate | aggressive
```

`leads_with` (levels vs. sentiment) is deliberately **not** a field — it's derived from `risk_posture` (`conservative` → levels lead, `moderate`/`aggressive` → sentiment leads). One less value to keep in sync with the account's actual risk stance.

**"Levels lead" does not mean levels only.** On a levels-led mandate the chart has the final say, and a Lean may *strengthen* a call it agrees with — it can never invent one or cancel one. [ADR-0002](0002-levels-led-verdicts-for-silent-roster.md) is where that asymmetry is specified; an earlier draft of this ADR said "levels only", which contradicted it.

**A benchmark measures the whole pot.** Every entry in `benchmarks:` is compared against the mandate's entire value — there is no field scoping one to a subset. Robinhood carrying both `sp500` and `btc` therefore asks two *do-nothing-instead* questions ("would all-in SPY have beaten my whole Robinhood pot?", "would all-in BTC?"), not two per-sleeve skill questions. Grading a sleeve against its own asset class would re-introduce domain-as-purpose, which is exactly what this ADR removes — and it gets ambiguous the moment a pot holds `HODL`, a stock-shaped instrument holding bitcoin.

## Considered and rejected

- **Multiple mandates per file.** Rejected — every mixed-purpose case anyone could name (SoFi's hypothetical risk-on crypto sleeve) is already solvable by splitting into a second file with `plaid_accounts:` narrowing, a mechanism that already exists for exactly this. Adding a second axis to the schema for a case the existing mechanism already covers was pure surface area.
- **A single benchmark field.** Rejected after realizing retirement's own goal is explicitly to beat *itself, held flat* (already on the map) as well as the S&P — two genuinely different questions ("is the market beating me" vs. "is my own trading beating just sitting still") that both matter at once. Capped at 1–3 to stop the comparison from turning into noise.
- **Deriving the savings benchmark live** (from SoFi's Plaid data, or a fetched treasury-yield series). Rejected on fact, not preference: Plaid is restricted to the `investments` product by design, so a savings account's real APY is architecturally unreachable without opening a new product scope; and `cfg/oracle_map.yaml` already flags the nearest available treasury series (`US02Y`) as unpriceable on the existing price pipe. A hand-maintained `flat_rate` benchmark is the honest v1.
- **Storing `leads_with` explicitly.** Rejected — it's a pure function of `risk_posture` with a rule simple enough to hold in code (conservative → levels lead; everything else → sentiment leads), so storing it separately would just be a second value that could drift from the first.
- **Scoping a benchmark to one sleeve of a mandate** (an `applies_to: crypto|stock` field). Rejected — the stick follows the *purpose*, not the asset class. Retirement is measured against the S&P no matter what it holds, so a rule keyed on asset class is already wrong for one of the four pots. Whole-pot comparison needs no new field and stays consistent with `held_flat`, which has always measured the whole pot. Per-sleeve skill attribution is a real but separate question, tracked as its own ticket.
