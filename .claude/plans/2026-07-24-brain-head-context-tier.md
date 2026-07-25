# Brain Head — Context Tier + Retrieval

## Design

**Goal:** answer *"where is my roster on ETH, where do they disagree, and what changed?"*
That question is the reason this project exists and it has never once been answerable.

**Why now, and why this instead of more grading.** `architecture.md` §D specified two
extraction tiers: **Calls** → Signal head, **Context** → Brain head. Phase 2 built Calls only;
Context was deferred and never started. There is currently zero embedding/retrieval code in
the repo, despite §D flagging it as *"cheap now, painful to retrofit."*

Grading ran aground on exactly this gap. Tegan's read — *"week-to-month calls are general
direction biases, and people adjust as data comes in; it's hard to make a single call that
plays out exactly right"* — is a restatement of §D. Discrete gradeable calls are the wrong
model for what these sources produce. The Context tier is the right one.

The corpus is not too small; 3704 calls is plenty. It is too small for **per-person
statistical ranking**, which needs independent resolving events. Synthesis has no such
requirement.

### Settled decisions (this session)

1. **Structured stance + embedded evidence**, not pure RAG. Tegan's questions require
   *aggregation across people* ("5 bullish, 2 bearish, here's the split") which retrieval alone
   cannot do — RAG returns passages, not counts. But answers need real quotes, which needs
   retrieval. So: extract a **Stance** record per (person, asset, video), and separately embed
   narrative chunks as evidence. The two join on `transcript_ref`.
2. **Local embeddings, SQLite store.** Consistent with `c7226f7` routing extraction through
   `claude -p` to stay on the Max subscription rather than metered API billing.
   **No vector database.** 620 transcripts ≈ 15k chunks × 384 dims ≈ 23MB — brute-force cosine
   in numpy is sub-millisecond at that scale. `sqlite-vec` earns its dependency at 100k+, not
   here. SQLite holds metadata + vectors as blobs.
3. **Ask-anything CLI first.** `brain "where is my roster on ETH"`. The weekly digest composes
   from the same engine later and is nearly free once this exists.

### Stance schema — and the failure mode it's designed around

`Stance` is deliberately **permissive**, unlike `TradeThesis`. In Phase 2, `key_levels`
`min_length=1` combined with all-or-nothing batch validation meant one bad item destroyed a
whole video's extraction (13 such failures in the re-distill). Narrative stance has no
equivalent hard invariant, so:

> Only `asset`, `lean` and `rationale` are required, and **items validate individually** — a
> malformed stance is dropped and logged, never taking its siblings with it.

```
lean:      bullish | bearish | neutral | uncertain
conviction: low | med | high
horizon:   scalp | swing | position | macro | None   (narrative often doesn't say)
rationale: why they hold the view
watching:  what would change their mind        <- the field that captures "adjusts as
                                                  data comes in"; the whole point
```

`watching` is the highest-value field in the schema: it is the machine-readable form of the
thing Tegan identified as how these people actually operate. It also becomes the hook for a
future stance-change grader ("Cowen flipped bearish on alts") — a far more tractable unit than
per-call accuracy.

Ids are **content-addressed** exactly like `core.thesis.thesis_id` (`abe46ac`), so a re-extract
doesn't silently re-point anything keyed on them.

## Patterns to Mirror

- `packages/core/src/core/thesis.py` — schema shape, `SCHEMA_VERSION`, `Source`/`Extraction`
  sub-models, and `thesis_id`'s content-addressing (normalize whitespace/case, sha256[:12]).
- `packages/distill/src/distill/extract.py` — forced tool-use + retry loop; `client=None`
  injection so tests never hit the LLM.
- `packages/distill/src/distill/cli_backend.py` — the subscription-backed client. Note
  `create()` currently **ignores `tools`** and hardcodes `FLAT_THESIS_SCHEMA`; generalizing
  that is task 1. Keep its comment about Claude Code's strict JSON-Schema subset rejecting
  `discriminator` — the same constraint applies to any new schema.
- `packages/distill/src/distill/roster.py` + `cli.py` — resumable sweep, thread pool,
  run summary. **Default `--concurrency 3`**, not 6 (see memory: 6 caused silent exit-1s).
- `packages/distill/src/distill/store.py` — `DATA_ROOT` via `parents[N]`, idempotent writes.
- `packages/core/src/core/canon.py` — `resolve()` for mapping query tokens ("eth") to canonical
  assets, and for grouping stances by person.
- `packages/distill/src/distill/triage_cli.py:collapse_restatements` — folding to each person's
  *current* view is exactly what "where is my roster now" needs.

## Tasks

### 1. `packages/llm/` — extract and generalize the subscription client
- Move `cli_backend.py` out of `distill`; make the tool schema a constructor parameter instead
  of the hardcoded `FLAT_THESIS_SCHEMA`. Both `distill` and `brain` depend on it.
- `distill`'s 65 existing tests are the safety net — they must stay green unchanged.

### 2. `packages/core/src/core/stance.py` — the Context-tier schema (TDD)
- `Stance` + `StanceExtraction` pydantic models per above; `stance_id()` content-addressed on
  `(transcript_ref, asset, lean, rationale)`.
- Permissive optionals; **no field whose absence can fail a whole batch**.

### 3. `packages/brain/` scaffold + `extract.py` + `prompt.py` (TDD)
- Prompt targets *narrative stance*, explicitly NOT levels/entries — the opposite of
  `distill/prompt.py`. Must instruct: capture the lean, the reasoning, and what would flip
  them; return an empty list rather than inventing.
- **Per-item validation**: parse each stance independently, drop-and-log failures.

### 4. `brain-extract` CLI — the corpus pass
- Resumable sweep over the 620 retained transcripts (~6.5M input tokens, comparable to the
  last re-distill; runs on the Max subscription, so wall-clock not dollars). `--limit`,
  `--concurrency 3` default, `--force`.
- **Validate on ~10 transcripts and iterate the prompt BEFORE the full run.** Sampling first
  is what the Phase-2 re-distill didn't do.
- Writes `data/stances/{platform}/{source_id}.json`. Checkpoint: a usable stance corpus.

### 5. `brain/chunk.py` — deterministic chunking (TDD, pure)
- ~1800 chars with ~200 overlap, preferring sentence boundaries. Transcripts are flat text with
  no timestamps (ASR output, `Quote.timestamp` is always None), so time-based chunking isn't
  available. Content-addressed chunk ids.

### 6. `brain/embed.py` + `brain/store.py` — local embeddings, SQLite
- `fastembed` with `BAAI/bge-small-en-v1.5` (384-dim, ONNX, no torch dependency).
- SQLite: chunk text + denormalized `person`/`asset`/`published_at` for pre-filtering, vectors
  as blobs. Brute-force cosine in numpy.
- Embedding is behind a narrow interface so the model can be swapped without touching retrieval.

### 7. `brain/retrieve.py` — hybrid retrieval (TDD, pure merge logic)
- **Structured leg:** stances filtered by asset/person/date, folded to each person's *current*
  view (mirroring `collapse_restatements`), with the prior view kept so "what changed" is
  answerable.
- **Vector leg:** query embedding → top-k chunks, pre-filtered by the same facets.
- Pure merge/rank; all I/O injected.

### 8. `brain` query CLI — synthesis
- `brain "where is my roster on ETH"` → resolve assets in the question via `canon`, retrieve,
  synthesize through the shared client.
- Output: the split (n bullish / bearish / neutral), each person's current stance with date,
  explicit disagreements, what changed recently, and quotes cited to person + date + URL.
- `--asset` / `--since` / `--person` for explicit control when query parsing guesses wrong.

## Considerations / open questions

- **Attribution is still feed-level.** Technical Roundup = Cred + DonAlt as one voice; the
  Brain must label that so "the roster splits 5-2" isn't read as 7 independent people.
- **Roster holes to fix before trusting a consensus count:** Mark Newton CMT is in
  `watchlist.yaml` but yields **0 theses**; Checkmate/checkonchain isn't in the watchlist at all
  despite `roster.md` marking it activated; the 11 X-only names have no ingestion path. The
  on-chain/flow leg of the trifecta is currently hollow.
- **No freshness loop.** Ingest and distill are manual CLIs, so "this week" is only as current
  as the last hand-run sweep. Not in this slice, but every Brain answer is time-scoped and this
  is the next gap after it.
- **Chunk size is a guess.** 1800/200 is untested against real retrieval quality; worth a
  sanity pass once there are real queries to judge against.
- **Don't re-litigate grading here.** The price oracle stays; it's needed by the technical
  cross-ref track regardless. Stance-change grading is the natural re-entry point later.
