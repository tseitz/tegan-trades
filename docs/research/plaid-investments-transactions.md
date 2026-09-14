# Does Plaid's `investments` product return transactions and a cost basis?

Research for [#47](https://github.com/tseitz/tegan-trades/issues/47), part of the [#46](https://github.com/tseitz/tegan-trades/issues/46) rescope map. Written 2026-09-13.

**Sources.** Plaid's own API reference (`plaid.com/docs`), Plaid's published OpenAPI spec
([`plaid/plaid-openapi` `2020-09-14.yml`](https://raw.githubusercontent.com/plaid/plaid-openapi/master/2020-09-14.yml)),
and live reads against `production.plaid.com` using this repo's existing credentials and access
tokens. Everything labelled **MEASURED** is a live call made while writing this; everything else
is quoted or paraphrased from the docs with a link. Anything I concluded rather than read is
labelled **INFERRED**.

---

## Short answer

**Yes on both counts, and you are already being handed a cost basis you throw away.**

- `/investments/transactions/get` exists, carries `date`, `quantity`, `price`, `amount`, `fees`,
  `type`/`subtype` and `security_id` — everything scorekeeping needs.
- A per-holding `cost_basis` comes back from `/investments/holdings/get` today, and
  `oracle/plaid.py` already reads it into `Row.cost`. Per-lot detail (`tax_lots`) is also in the
  response, and the repo ignores it.
- Calling the transactions endpoint does **not** widen the token. It stays inside the
  `investments` consent scope. It *does* add a second monthly billing subscription per Item.
- **The one real gap is M1.** M1 serves a whole-position `cost_basis` but **zero tax lots**, so
  M1's acquisition dates have to come from transactions — which is exactly the thing not yet
  proven to work for M1.

---

## 1. The transactions endpoint and its row shape

**Endpoint:** `POST /investments/transactions/get`
([API reference](https://plaid.com/docs/api/products/investments/#investmentstransactionsget)).

Request ([`InvestmentsTransactionsGetRequest`](https://raw.githubusercontent.com/plaid/plaid-openapi/master/2020-09-14.yml)):

| Field | Required | Notes |
|---|---|---|
| `access_token` | yes | the same token `plaid-sync` already holds |
| `start_date` | yes | `YYYY-MM-DD` |
| `end_date` | yes | `YYYY-MM-DD` |
| `options.account_ids` | no | narrows a multi-account Item |
| `options.count` | no | default `100`, min `1`, **max `500`** |
| `options.offset` | no | default `0` |
| `options.async_update` | no | default `false`; only relevant for an Item *not* initialised with `investments` |

The response is `{ item, accounts, securities, investment_transactions, total_investment_transactions, request_id }`.
`total_investment_transactions` is the count available in the date range, so paging is
`offset` until you have that many. `securities` is the same shape the holdings call returns, so
`security_id` joins the two.

An `InvestmentTransaction` row (spec `InvestmentTransaction`, all fields below are `required`
unless noted):

| Field | Type | Documented meaning |
|---|---|---|
| `investment_transaction_id` | string | unique across all Plaid transactions, case sensitive |
| `account_id` | string | account the transaction posted against |
| `security_id` | string, nullable | joins to the `securities` array |
| `date` | date | "the ISO 8601 posting date for the transaction. This is typically the **settlement** date" |
| `transaction_datetime` | date-time, nullable, *not required* | when the order was **initiated**; "returned for select financial institutions" |
| `name` | string | "the institution's description of the transaction" |
| `quantity` | double | "positive for buy transactions; negative for sell transactions" |
| `amount` | double | "positive values when cash is debited, e.g. purchases of stock; negative values when cash is credited" |
| `price` | double | "the price of the security at which this transaction occurred" |
| `fees` | double, nullable | "the combined value of all fees applied to this transaction" |
| `type` | enum | see below |
| `subtype` | enum | see below |
| `iso_currency_code` / `unofficial_currency_code` | string, nullable | exactly one is non-null; crypto uses the unofficial one |

`cancel_transaction_id` is in the spec but marked `deprecated: true` and
`x-hidden-from-docs: true` — "a legacy field formerly used internally by Plaid". Do not build on it.

**There is no `side` field.** Side is carried by `type`/`subtype` plus the sign of `quantity`.

`type` enum (6 values, spec `InvestmentTransactionType`): `buy`, `sell`, `cancel`, `cash`,
`fee`, `transfer`. `transfer` is the interesting one for scorekeeping — "activity which modifies
a position, but not through buy/sell activity e.g. options exercise, portfolio transfer". An
ACATS-in position arrives as `transfer`, with no purchase price.

`subtype` enum (48 values, spec `InvestmentTransactionSubtype`) includes `buy`, `sell`,
`buy to cover`, `sell short`, `dividend`, `dividend reinvestment`, `rebalance`, `merger`,
`spin off`, `split`, `contribution`, `withdrawal`, `deposit`, `management fee`, `account fee`,
`long-term capital gain`, and so on. `rebalance`, `split` and `merger` are the ones that will
break a naive replay of a position.

`/investments/refresh` also exists — an on-demand extraction, billed **per request at a flat
fee** rather than by subscription
([billing](https://plaid.com/docs/account/billing/)). A nightly cron does not need it.

## 2. How far back history reaches

Two statements, and they agree:

- API reference, on `start_date`: *"Plaid returns all investment transaction history stored for
  the Item (**up to 2 years prior to the initial linking of the Item**)."*
  ([spec](https://raw.githubusercontent.com/plaid/plaid-openapi/master/2020-09-14.yml), `InvestmentsTransactionsGetRequest.start_date`)
- Product overview: the endpoint *"provides up to 24 months of investment transactions data."*
  ([plaid.com/docs/investments](https://plaid.com/docs/investments/))

**The window is anchored at link time, not at call time.** That is the load-bearing detail and
it is easy to misread. Two years measured from when the Item was created — it does not slide
backwards as time passes, and re-linking is what moves it. The three Items in this repo were
linked recently, so their floor is roughly *now minus two years* and will stay put.

**Does it vary by institution?** The docs state no per-institution transaction-history window,
and I found no page that gives one. What the docs *do* say is that product support varies by
institution and that the published coverage table is stale: *"This table is not updated in real
time; for the most up to date data, use `/institutions/get`"*
([institutions](https://plaid.com/docs/institutions/)). **INFERRED:** the practical window is
"whatever the institution actually stored and hands over, capped at 24 months" — Plaid can only
return what the broker serves. The documented 24 months is a ceiling, not a floor, and the only
way to learn an institution's real depth is to ask it. There is no documented way to ask without
calling the endpoint.

## 3. Is a cost basis returned directly?

**Yes, at two levels, and the repo already reads the coarser one.**

`Holding.cost_basis` — *"the total cost basis of the holding (e.g., the total amount spent to
acquire all assets currently in the holding)"*, nullable, and a `required` key so it is always
present even when null. `oracle/plaid.py:207` already reads it and divides by quantity to get
`Row.cost` (an average price).

`Holding.tax_lots[]` — *"per-lot acquisition data for this holding. An empty array indicates the
institution does not provide lot-level data."* Each `HoldingTaxLot` carries `institution_lot_id`,
`original_purchase_datetime`, `quantity`, `purchase_price`, `cost_basis`, `current_value`, and
`position_type` (`LONG`/`SHORT`). Every field is nullable; all are `required` keys.

This is the ideal shape for "did my swing trading beat holding flat" — a lot is a dated entry at
a known price — and it needs no transactions call at all.

**MEASURED, live `/investments/holdings/get`, 2026-09-13, production:**

| Item | holdings | with `cost_basis` | with non-empty `tax_lots` |
|---|---|---|---|
| M1 Finance (`retirement`) | 78 | **78** | **0** |
| Robinhood | 3 | 2 | 2 |
| SoFi | 3 | **0** | 0 |

The `tax_lots` key was present on all 84 holdings; it is the *contents* that vary. Robinhood's
lots are real and detailed — one holding came back with **28 lots**, each carrying
`original_purchase_datetime`, `purchase_price`, `quantity`, `cost_basis` and `current_value`
(`institution_lot_id` was null). M1 returns the array empty on every one of its 78 positions.
SoFi reports neither a basis nor lots.

So: **Robinhood needs no transactions endpoint for scorekeeping. M1 does.** That inverts the
usual assumption — M1 is the retirement book, the mandate the map cares most about, and it is
the one serving the least.

## 4. Does requesting transactions widen the token?

**No. The repo's read-only invariant survives.** Three independent lines of evidence:

**(a) Transactions are inside the `investments` consent scope already.** Plaid's Data
Transparency Messaging guide maps the `investments` product to the scopes "Account and balance
info, Contact info, Investments", and defines the Investments scope as *"info about investment
accounts, such as securities details, quantity, price, and **transactions**"*
([data transparency messaging](https://plaid.com/docs/link/data-transparency-messaging-migration-guide/)).
The consent the user already gave at link time covers the transaction data.

**(b) Money movement is a different product entirely.** The `Products` enum lists `investments`
and `investments_auth` separately from `auth`, `transfer`, `payment_initiation`,
`standing_orders` and `pay_by_bank`. Moving money needs one of those, and none of them is being
requested. *"The API will only return data for products that users have consented to through
Link"*, and calling outside the consented set returns `ADDITIONAL_CONSENT_REQUIRED` — a refusal,
not a silent widening
([same page](https://plaid.com/docs/link/data-transparency-messaging-migration-guide/)).

**(c) MEASURED — the live Items say so.** `/investments/holdings/get` returns an `item` object;
for all three tokens it reads:

```
products:           ['investments']
billed_products:    ['investments']
consented_products: ['investments']
```

`consented_products` is defined as the products *"the user has consented to for the Item via
Data Transparency Messaging"*. One entry. `transfer` and `payment_initiation` are not in the
Item's `available_products` either.

**What DOES change is billing.** Plaid splits Investments into two subscriptions. Initialising
Link with `investments` adds only **Investments Holdings**; **Investments Transactions** is added
by the first call to `/investments/transactions/get`, and *"calling `/investments/transactions/get`
on an Item adds both the Investments Transactions and Investments Holdings subscriptions"*
([billing](https://plaid.com/docs/account/billing/),
[initializing products](https://plaid.com/docs/link/initializing-products/)). Both are monthly
per-Item subscriptions, not per-call. The subscription attaches to the **Item the call names**, so
one call against the retirement Item costs one subscription, not three — and only M1 needs it,
since Robinhood already serves lots and SoFi serves nothing either way.

**The subscription cannot be cancelled.** *"Once a subscription fee product has been added to an
Item, it is not possible to end the subscription and leave the Item in place. The Item must be
deleted"* via `/item/remove` ([billing](https://plaid.com/docs/account/billing/)) — which means
re-linking M1 through `plaid-link` and re-consenting. Plaid also bills it *"even if no API calls
are made for the Item"*, and mid-month creation or removal is not pro-rated.

**No published price.** Plaid states *"a price list is not available in the documentation"* and
puts rates behind Production access ([billing](https://plaid.com/docs/account/billing/),
[pricing](https://plaid.com/pricing/)). The live rate for this account is visible only in its own
dashboard.

Also documented and relevant: even though the subscription is not added at link time, Plaid
*"will still pre-fetch investment transaction history"* during initialisation
([initializing products](https://plaid.com/docs/link/initializing-products/)). **INFERRED:** the
history is likely sitting there already for the existing Items, so the first call should not need
`async_update` or a `HISTORICAL_UPDATE` webhook — which matters, because this repo runs on a
droplet with no public URL for webhooks.

**Open, and it is a cost question rather than a technical one:** whether the free Trial plan this
repo signed up under includes the Investments Transactions subscription at all. The docs describe
the two subscriptions but I found no page stating the Trial plan's product set. That is a
dashboard question, not a docs question.

## 5. M1 Finance specifically

**MEASURED — `/institutions/get_by_id`, production, 2026-09-13:**

```
ins_116960  M1 Finance  -> assets, auth, balance, cra_lend_score, cra_plaid_credit_score,
                           identity, identity_match, investments, liabilities, pay_by_bank,
                           processor_payments, signal, transactions, transactions_refresh, transfer
ins_54      Robinhood   -> assets, auth, balance, cra_lend_score, cra_plaid_credit_score,
                           identity, identity_match, investments, liabilities,
                           processor_payments, signal, transactions, transactions_refresh
```

M1 lists `investments`. This re-confirms what `scripts/probe_plaid_coverage.py` found and
contradicts Plaid's public marketing page for M1 — which still lists only Assets, Auth and
Balance. The docs themselves say the coverage table *"is not updated in real time"* and to use
`/institutions/get` instead, so the API is the authority and the page is not. (The `plaid.com/institutions/m1-finance/`
URL 404s; the live one is `plaid.com/institutions/m-1-finance/`.) **The probe's existing verdict
stands and needs no correction.**

**But the institution `products` array is one flag for the whole Investments product.** There is
no `investments_holdings` / `investments_transactions` split in the `Products` enum, so
`investments: yes` for M1 does **not** promise that M1 serves transactions. I could not find any
documented per-institution signal that distinguishes them.

Two things point the wrong way for M1:

- `transactions` in that list is the **banking** Transactions product — a different product with
  a different schema. It says nothing about investment transactions.
- **MEASURED:** M1 returns an empty `tax_lots` array on all 78 positions while Robinhood returns
  populated ones. **INFERRED:** an institution that does not surface lot-level acquisition data
  is a plausible candidate for also not surfacing a full trade ledger — same underlying source
  data. Not proof, but it lowers the prior.

**The decisive test is one call, and it costs a subscription.** Call
`/investments/transactions/get` against `PLAID_ACCESS_TOKEN_RETIREMENT` with
`start_date` = two years back, `end_date` = today, `options.count` = 500, and read
`total_investment_transactions`. Zero means M1 serves no investment transactions and the
retirement mandate needs a different cost-basis source. That call is irreversible in billing
terms — it adds the Investments Transactions subscription to that Item — so it is a decision to
take deliberately, not a probe to run casually. **Not run as part of this research.**

## 6. If it turns out to be holdings-only: where else a cost basis comes from

Ranked by how little new machinery each needs.

1. **What you already have.** `Holding.cost_basis` is present on 78/78 M1 positions today and is
   already parsed into `Row.cost`. It is an *average* entry price with no dates, so it answers
   "am I up on this position" but not "when did I buy". For a benchmark that needs a start date,
   the file's own history is the substitute — `data/portfolios/*.yaml` is rewritten nightly by
   `plaid-sync`, so a snapshot series exists going forward even though it cannot be
   back-filled. **INFERRED:** cheapest real path to "beat holding flat" from today onward is to
   keep that series rather than to reconstruct the past.
2. **Robinhood's `tax_lots`, for the Robinhood mandate.** Already returned, already dated,
   already priced per lot. Zero new API surface — `rows_from()` just discards it.
3. **The broker's own export.** M1 offers account statements and a year-end 1099-B; Robinhood
   the same. A 1099-B is authoritative on realised basis and dates. It is a manual annual file
   drop, not a feed — fine for a one-time back-fill, useless nightly.
4. **Alpaca's activity feed, where positions live at Alpaca.** `packages/execution` already calls
   `GET /v2/account/activities/FILL` (`alpaca_broker.py:403`); a FILL carries `transaction_time`,
   `price`, `qty`, `side`, `symbol` and `order_id`
   ([Alpaca docs](https://docs.alpaca.markets/reference/getaccountactivitiesbyactivitytype-1)),
   and Alpaca's position object carries `avg_entry_price`. This only covers what the repo itself
   traded through Alpaca — it says nothing about M1 or Robinhood holdings.
5. **This repo's own journal.** `packages/execution` records what it placed and
   `book --reconcile` settles how trades finished. Perfect fidelity, but only for trades this
   system placed, which is none of the retirement book.

**Not available:** M1 has no public API and Robinhood's official API is crypto-only — already
recorded in `CLAUDE.md`, and nothing found here changes it. Plaid remains the sanctioned path.

---

## What this leaves open

- Whether M1 serves investment transactions at all. One call answers it; that call adds a
  permanent monthly subscription to the Item. **Needs a yes before it is made.**
- Whether the Trial plan includes the Investments Transactions subscription. Dashboard question.
- Nothing here required a code change, and none was made.
