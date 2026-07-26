# Improvements & Known Gaps

The backlog. Things found while building that shouldn't derail the thing being built.

**Rules for this file:** an entry earns its place by carrying *evidence* — a measurement, a
count, a real example — not a hunch. If it's just "we could do X better", leave it out. Record
what we know now so a future session doesn't have to re-derive it. Delete entries when done.

Status: `DECIDED` (agreed, not executed) · `OPEN` (real gap, no decision) · `WATCHING` (may not
be a problem; revisit if it bites).

---

## 1. Levels are not the product — sentiment and trust are · `PARTLY DONE`

**Done:** `min_length=1` is dropped, the prompt now says levels are optional and forbids
inventing one, and `TradeThesis` is distinguished from a lean by its `invalidation` alone.

**Residual: the existing corpus was extracted under the old prompt.** All 3,427 stored theses
were produced by a model that had to supply a level, so the fabricated ones are still in
`data/theses/`. The fix only applies to what gets extracted from here. Options: live with it and
let the corpus turn over naturally, or re-distill — which is a full-corpus LLM pass, so read §9
first. Do NOT re-distill just for this; the levels were never the product.

---

## 1b. Original rationale, kept for context

**The call (2026-07-25):** we went too hard, too fast at extracting exact price levels from
videos. What the roster is actually *for* is **sentiment** — direction and conviction — plus a
**trust score** of how right each person has generally been. Real levels can come from
elsewhere. If someone happens to state a level, take it; never force it.

**What to change**
- Drop `TradeThesis.key_levels` `min_length=1` (`core/thesis.py:38`). It forces the model to
  emit levels whether or not the speaker gave any.
- `core/levels.py` stays, demoted from load-bearing to opportunistic. Its abstention path is
  already the designed fallback, so nothing breaks.
- Phase 4 leans on structural targets, which is already the fallback.

**Evidence**
- **388 of 1,621** live readings (24%) had levels *only behind* entry — those are invalidations
  and stops stuffed into `key_levels` to satisfy the schema, not targets.
- 13 of the 98 re-distill failures were exactly this constraint (`min_length=1`) rejecting a
  whole video's extraction because one call had no explicit level.

**Consequences worth noting**
- This *validates* `core/grade.py`'s direction-and-horizon-only design — it was already grading
  the right thing.
- It buries **3.3b (level-aware grading) permanently.** Don't resurrect it.
- It raises the priority of the trust score, which is the roster-scoring work in
  `oracle/score_cli.py` — currently honest but underpowered (see §4).

---

## 2. Rip out the fixed horizon constants · `DECIDED` (needs a replacement first)

**The call (2026-07-25):** the 7/30/180/365-day horizons are unvalidated guesses and should go.

**They cannot simply be deleted** — they're load-bearing in two places:
- `core.grade.Horizons` — how long a call is measured over
- `core.setups.StaleAfter` — how long a stated view stays actionable

**Candidate replacement: event-based, not clock-based.** A call is live until the same person
restates or reverses on the same asset. `triage_cli.collapse_restatements` already computes
exactly that grouping, and `core/stance.py` already tracks lean changes. No constants, entirely
data-driven, and it answers the real question ("is this still their view?") instead of a proxy.

**Evidence the current constants can't be rescued**
- Restatement cadence is near-identical across timeframe labels (swing 11d, position 14d, scalp
  28d) — it measures publishing schedule, not view horizon.
- A dense per-label horizon sweep found no distinct optimum per label.
- The whole horizon sweep sits *inside* the bootstrap noise floor (Kendall tau p05 +0.44), so
  the constants are unfalsifiable with current sample size.

---

## 3. Take another lap on ICT — mine TraderMayne's courses · `OPEN`

**Why:** the current spec in `Trading/_Structure.md` is reverse-engineered from a **truncated
"3 Things" list** (announced three, listed two) plus conversation. TraderMayne is the primary
source for this method *and publishes explicit courses on it*. Mining those would replace
inference with the actual system.

**Specifically unresolved in the spec today**
- The missing third "thing" (premium/discount was inferred, not stated)
- Whether the FVG middle candle must be displacement, and how displacement is really defined
- How the dealing range is bounded (2-bar pivot on weekly is a guess)
- The 15m entry trigger — "failed breakdown, reclaim" needs real definition, and it's the whole
  of slice 2's layer 3

**Practical note that will cost time if missed:** the ingestion spine handles YouTube already,
but **the distill prompt is built for trade theses and correctly returns EMPTY on methodology
videos** (observed: "How To Use SMT Divergence" → 0 theses, which was the right answer). Mining
courses needs a *separate methodology-extraction pass* with its own prompt and schema — it is
not a `distill-roster` run.

**Output:** update `Trading/_Structure.md`, which is the spec `core/structure.py` implements.

---

## 4. Nothing is validated against revealed preference · `OPEN` — highest leverage

There are now **four scoring systems** and **zero closed loops**:

| Scorer | Where | Validated against |
|---|---|---|
| Intrinsic thesis rank | `core/rank.py` | nothing |
| Backtest / skill edge | `core/grade.py`, `core/score.py` | market only, no preference signal |
| Brain retrieval | `brain/retrieve.py` | nothing (and it barely discriminates — §8) |
| Setup candidates | `core/setups.py` | nothing |

Mining `data/triage/decisions.jsonl` (approve vs skip) against the rankers was agreed during
Phase 3 and has never happened, because triage has been run **once**, promoting 7.

**This is why "7 candidates from 3,427 theses" is unanswerable** — admirably selective or badly
miscalibrated, and nothing in the system can distinguish them.

**Unblocked by:** using `setups` and `distill-triage` for real, a few sessions. Costs no tokens,
needs no new code, and produces the only ground truth available.

### First real session · 2026-07-25 — 7 candidates decided, and neither half is usable yet

`data/setups/decisions.jsonl` holds 10 rows over **7 distinct candidates** (ZEC and NEAR were
each revised). Two defects to fix before the mining pass is worth writing.

**a. Half the rows carry no ranker value — historical only, no code fix needed.** The sidecar
gained `score` / `proximity` / `inside_zone` / `agreement` / `newest_at` / `people` partway
through the session (first row with them: 18:15Z, minutes after `2141c35` landed). The five
earlier rows — including three of the five approvals (GOOGL, SPX, ETH) — have none. §4 correlates
*decision against score*; those rows cannot participate. `decision_record` already writes all six
fields unconditionally, so this cannot recur — don't "fix" it. Backfilling the old rows is **not
clean**: the corpus moved mid-session (ZEC's `newest_at` 07-15 → 07-24, agreement 2 → 3, score
0.665 → 0.790 between 18:02Z and 23:47Z), so a re-run yields today's score, not the decision-time
score. If backfilled, mark it `recomputed_at` — never as captured live.

**b. Zero `rejected` rows — the only verdict designed to calibrate.** Final tally is 5 approved,
1 `later`, 1 `archived`. Per `setups_cli.py:67-91`, `later` is reversible and `archived` is
*explicitly not a judgment*, so neither is a negative label. `rejected` is the one that carries a
reason (`trade_quality` → setups scorer, `view_wrong` → roster trust), and there are none. A
ranker cannot be validated against five positives and no negatives.

Note the two rows spelled `skipped` are **legacy vocabulary**, predating the four-way split; they
are honoured as permanent (`_PERMANENT`) so old passes don't resurface. Don't read them as a
current verdict.

**Next:** nothing to build — accumulate sessions until `rejected` has real rows, then correlate.
Do not start the correlation before then. Note this makes §4 gated on **decision volume**, which
is in turn gated on candidate supply (§5) and thesis freshness (§6) — those are the buildable
work that accelerates it.

---

## 5. `trend_state` is noisy — two swings decide everything · `OPEN`

`core.structure.trend_state` reads only the **last two swing highs and last two swing lows**.
One unusual swing flips the verdict.

**Evidence:** BTC weekly reads `ranging` as of 2026-07-24, and since ranging permits neither
direction, **BTC — 28.5% of the corpus — yields zero candidates.** That may be correct, but it
rests on four data points.

Candidate fix: score the structure *sequence* (how many recent swings agree) rather than a
two-point comparison, so the state degrades gradually instead of flipping.

---

## 6. No freshness loop · `OPEN`

The machinery is batch-historical; the use case is real-time. Nothing runs on a schedule —
every Brain answer and every setups run is only as current as the last hand-run sweep.

**Evidence:** `stale` is **2,872 of 3,427** rejections (84%) in the live setups run. Partly an
artifact of scanning two years of corpus at one as-of date, but the underlying gap is real: the
question worth answering is "price is approaching this level *now*", which needs a scheduled
ingest → distill → setups pipeline.

---

## 7. Stop sanity should be ATR-relative, not a minimum width · `WATCHING`

A minimum stop width was considered and declined, correctly — score saturation already caps the
ranking damage (RR saturates at 3.0, so 15.75 and 4.67 contribute identically).

The real concern is different: **GOOGL's 5.51 stop is roughly 1 ATR**, which ordinary noise
takes out. RR without survival probability is a half-metric. The right form is `stop >= k * ATR`,
not an absolute width. `Context` already carries ATR, so it's cheap.

Revisit if narrow zones keep topping the list.

---

## 8. Evidence-leg retrieval doesn't discriminate · `OPEN`

Brain retrieval scores compress into **0.72–0.81** — cosine barely separates anything, because
every chunk is "a person talking about markets in ASR speech." Queries return Discord-giveaway
chatter above real analysis.

**Not a coverage problem** — all 666 transcripts are indexed (18,108 chunks). Adding corpus does
not fix it. Hypothesis: chunk granularity plus a missing lexical/BM25 leg.

**Dead end, do not re-test:** `query_embed` is identical to `embed` for this model, so the bge
query-prefix theory is disproven.

---

## 9. Audit extraction efficiency before considering the API · `OPEN`

**First, a correction to how this was originally framed.** "The direct API is 8–15× cheaper
(~$26 vs ~$183 per pass)" is true *per token* and misleading as a decision rule. `claude -p`
runs on the existing **$100/month Max subscription — marginal cost zero**. API tokens are
**incremental cash on top of that**. So switching billing paths only wins where volume genuinely
exceeds what the subscription carries. A full 666-transcript pass might; a freshness loop of
5–20 new videos a day almost certainly does not.

**What matters on *both* paths is waste**, because burning allowance still risks cap hits — and
a usage cap silently killing an entire sweep has already happened once (`8365729`).

**The audit, before any billing change or bulk pass:**
- **Where does the ~90% harness overhead actually go?** It's the headline number and nobody has
  broken it down. Some may be avoidable inside `claude -p`.
- **We send whole transcripts, and the corpus is 5.26% signal.** Extractive pre-filtering before
  the LLM call would cut input dramatically on *either* path, and it's the single biggest lever.
  Must stay extractive — abstractive summarizing would destroy `asset_heard`, `watching` and
  citation integrity.
- **Is the system prompt cached across calls?** It's identical every time.
- **Do retries and re-distills resend transcripts unnecessarily?**
- **Measure the real daily volume of a freshness loop (§6)** before pricing anything.

**Decision rule:** stay on the subscription unless the measured volume can't fit in it. Optimize
the waste regardless — it pays off either way.

---

## 10. `domain` is per-thesis and inconsistent per asset · `WATCHING`

`SPX` appears in the corpus as `crypto`, `macro`, *and* `stock` across different theses, so
`tier_for` can label the same asset differently depending on which thesis surfaces.

Contained for now: `tier_for` gates on domain specifically to stop the SPX/memecoin rank
collision leaking in. A per-asset domain consensus in `core/canon.py` would be steadier.

---

## 11. Agreement is date-blind · `OPEN`

`agreement_signal` counts distinct people and saturates at 3, so seven voices score 1.0 whether
they all spoke this week or one spoke six months ago.

**Evidence (live, 2026-07-25):** the ETH long candidate's seven supporters span **2026-01-20 to
2026-07-22** — Magic Lines' view is **186 days old** and counted equally with TraderMayne's from
three days prior. ZEC's second supporter is two months stale.

Not a staleness-gate failure: the old view is a `macro` thesis and legitimately survives a
360-day horizon. The question is whether a macro bull from January is the same evidence as a
swing bull from last week. Arguably cross-horizon agreement *is* meaningful confluence — but
right now it's indistinguishable, which is the actual problem.

Candidate fix: weight each voice by recency (`core.rank.recency_signal` already exists) rather
than counting heads. Interacts with §2 — if horizons go event-based, "current view" becomes
better defined and this may partly solve itself.

Dates are now shown per supporter in the queue and the vault note, so this is at least visible
rather than hidden.

---

## 12. Slice 2 needs the oracle at sub-daily granularity · `OPEN`

Layers 2–3 of `Trading/_Structure.md` (1H approach, 15m trigger) require a `date` → `datetime`
refactor through `Bar`, `PriceSeries`, `cache` (granularity in the key), all three sources, and
`core/grade.py`. **Done halfway it corrupts silently** — `PriceSeries.__post_init__` dedupes on
`bar.date`, so 24 hourly bars for one day collapse to 1 with no error.

Note the granularity needed is **900s (15m)**, not just 1H/4H. Coinbase supports it; its cap is
`MAX_CANDLES = 300` (the Phase 4 plan's "720" is wrong).

---

## 13. The Claude Code sandbox strips the Webshare proxy · `RESOLVED` (workaround) — keep this written down

**Symptom:** `ingest-roster` returned `0 ingested, 662 skipped, 60 stale, 10 failed`, with every
missing video failing as `ChunkedEncodingError: IncompleteRead(N read, M more expected)` or
`RetryError: too many 429 error responses`. Corpus frozen at 2026-07-23.

**Root cause: the Claude Code sandbox silently bypasses `session.proxies`**, so every transcript
fetch egressed from the *local* IP — the one YouTube blocked during the checkonchain backfill on
2026-07-24. The Webshare account was never at fault and rotation works fine.

The one-line proof — same code, same credentials, only the sandbox differs:

| | Sandboxed | Unsandboxed |
|---|---|---|
| Direct (no proxy) | 97.88.98.212 | 97.88.98.212 |
| Proxied, call 1 | 97.88.98.212 | **189.50.230.176** |
| Proxied, call 2 | 97.88.98.212 | **24.152.70.248** |

Sandboxed, proxied == direct: the proxy is not applied at all. Unsandboxed, two consecutive calls
return two different residential IPs: the proxy is applied *and* rotating.

**Workaround (verified):** run any transcript-fetching command with the sandbox disabled. After
doing so, `ingest-roster` returned **4 ingested, 662 skipped, 60 stale, 6 failed** and all 6
residual failures are the permanently-dead set below.

**Permanent fix — `sandbox.excludedCommands: ["uv *"]` in `.claude/settings.json`, plus a patch to
the global direnv hook. Both are required; either alone does nothing.**

`allowedDomains` was tried and **removed as dead config — it cannot work here.** The sandbox
exports `HTTPS_PROXY=http://srt:...@localhost:63350`, a local filtering proxy. When the code sets
`session.proxies` to Webshare, requests emits `CONNECT www.youtube.com:443` to `p.webshare.io:80`;
that is intercepted, and the sandbox proxy **terminates and re-originates** the connection from
the local IP. Webshare is structurally cut out of the path, so allowlisting `p.webshare.io` only
grants permission to *fetch* it, never to *tunnel through* it.

Excluding the command from the sandbox is therefore the only mechanism that restores the proxy.
It is scoped to `uv` rather than `ingest-roster` because commands are invoked as
`uv run ingest-roster` — the first token is `uv`, so a binary-name entry would never match.
**Consequence, accepted deliberately: every `uv run ...` in this repo now runs unsandboxed.**

#### Attempt 1 (`excludedCommands: ["uv"]`) failed — and why · 2026-07-25

After a restart the probe still returned `direct == proxied`. Two independent defects:

1. **A global `PreToolUse` Bash hook rewrote every command**, prefixing
   `eval "$(direnv export bash 2>/dev/null)" && `. The first token the sandbox matched on was
   therefore always `eval`, never `uv`. Proof: `ps -o args= -p $$` returned
   `(eval):1: operation not permitted: ps` — zsh's error prefix for code run under `eval`.
2. **Entries are command globs, not binary names.** The docs' own example is `"docker *"`. A bare
   `"uv"` would not match `uv run ingest-roster` even without the hook.

Control that isolated defect 1: `mkdir /Users/tseitz/.claude/sandbox-probe-dir` — a bare, exact
first-token match against the **global** `excludedCommands` entry `"mkdir"` — was still denied. So
exclusion was inert for every command, not just `uv`. **Always run a control against an entry you
did not add**; it separates "my config is wrong" from "the mechanism is broken".

**Attempt 2 — `VERIFIED WORKING 2026-07-25`.** The direnv hook in `~/.claude/settings.json` now
emits `{}` (no rewrite) when the command matches `^\s*uv\s`, and the project entry is `"uv *"`.
Harmless here — this repo has no `.envrc`, so `uv` never needed direnv.

Probe result in a fresh session, sandbox ON, no `dangerouslyDisableSandbox`:
`direct 97.88.98.212` vs `proxied 190.233.209.115`. The proxy survives. `uv run ingest-roster`
no longer needs the sandbox escape hatch.

### Second sandbox gap: the vault is a symlink · `FIXED, VERIFIED 2026-07-25`

Writing to `~/vault/Trading/Trade Logs/Setups.md` failed with `Operation not permitted` even
though `~/vault/Trading` was in `allowWrite`. `~/vault` is a **symlink** to
`/Users/tseitz/Obsidian/Main Vault`, and macOS seatbelt matches the **resolved** path — so the
symlink entry granted nothing. `.claude/settings.local.json` now lists both the symlink paths and
the resolved `~/Obsidian/Main Vault/...` ones. Any future vault path must be added in resolved
form. Confirmed working: `touch` succeeded through **both** the resolved and the symlink path.

The probe (proxied must differ from direct):

    uv run python -c "
    from ingestion.env import load_env; load_env()
    from ingestion.youtube import _proxy_config
    import requests
    px=_proxy_config().to_requests_dict()
    def ip(p):
        s=requests.Session()
        if p: s.proxies.update(px)
        return s.get('https://api.ipify.org', timeout=(10,20)).text.strip()
    print('direct', ip(False)); print('proxied', ip(True))"

### Why it cost hours to find — three layers of masking

The true error is `IpBlocked`. Nothing ever printed it:

| Layer | Behavior | Symptom produced |
|---|---|---|
| `WebshareProxyConfig.prevent_keeping_connections_alive` -> `True` (`proxies.py:181`) sets `Connection: close` (`_api.py:41`) | YouTube's block page arrives as a truncated chunked body | `ChunkedEncodingError: IncompleteRead` |
| urllib3 `retries_when_blocked` adapter | retries 429 on the **same** connection = same blocked IP | `RetryError: too many 429s` |
| `youtube._TRANSIENT` catches `RequestException` | treats both as flaky, retries 4x with backoff | ~20 wasted attempts, misleading final error |

Byte counts differed on every attempt (1188, 2591, 3880, 6745...), which is what sold the
"proxy truncates mid-stream" theory.

**Disproven leads — do not re-test:**
- *`&variant=gemini` on new uploads.* Present on `JY_wY8XXjYU`, absent on others failing identically.
- *Video-specific / newest-only.* Videos **already in the corpus** (`UIv9IQ4uXEA`, `Tv6DJTNobJ4`)
  failed identically. This is what collapsed the video-specific theory — always test a known-good
  control before believing "the new items are special".
- *Library out of date.* `youtube-transcript-api` 1.2.4 **is** current PyPI latest.
- *Webshare plan/bandwidth.* Rotation demonstrably works outside the sandbox.
- *Proxy can't handle large/chunked bodies.* 837KB chunked+gzip succeeded 4/4 with keep-alive.

**Do NOT "fix" this by overriding `prevent_keeping_connections_alive -> False`.** It unmasks the
real error, but the library sets it deliberately (`proxies.py:39`: without it "your IP won't be
rotated"), so it trades a masked failure for broken rotation once egress is correct.

**Worth building anyway:** a preflight that probes the exit IP across 2-3 fresh sessions and
aborts loudly when they're identical (proxy not applied) or block-flagged. The `TranscriptBlocked`
abort path already exists and is the right destination — it just never fires, because the block
never arrives as `RequestBlocked`. That turns this entire investigation into a 5-second error.

### Genuinely dead, independent of all the above

Captions disabled: `MvD7fQQ0szE` `Nlw-PZhoViQ` `S_obDkmaf8I` `duXvzmQVZ1Q` `ufwa9Ld47Jo`.
Deleted: `_IRMBuen60Y`. Correctly skipped, permanent — these are the 6 residual failures.


## 14. X/Twitter ingestion is decided but entirely unbuilt · `DECIDED` (zero code)

`docs/phase-0-findings.md` chose **Grok `x_search`** over the official X API (~$1–5/mo vs
$200/mo). `cfg/watchlist.yaml` already encodes the intent: **17 channels marked `access: grok`**
and an `x_grok_digest:` list of 6 handles at line 283.

**Nothing reads that key.** `grep -rniE "grok|x_search|xai" packages/` returns zero hits;
`packages/ingestion/src/ingestion/` contains only `youtube.py`.

**What it costs today:** 6 roster voices are X-only and therefore invisible — `QuantMeta`,
`0xfhd_`, `thiccyth0t`, `GiganticRebirth`, `LomahCrypto`, plus `JustDeauIt`'s X feed (his
YouTube *is* ingested). Tom Lee is recorded at `watchlist.yaml:122` as X-only-plus-guest-spots,
so he's uncovered too.

**Design note before building:** posts are ~2 orders of magnitude shorter than transcripts, so
the per-item LLM economics are inverted — batching many posts into one `distill` call is the
obvious shape, and the current one-call-per-document `distill_all` loop does not fit it.
Interacts with §9 (the extractive pre-filter) — X needs no pre-filter at all.
