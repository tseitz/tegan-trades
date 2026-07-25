# brain

The **Context tier** (`architecture.md` §D), as opposed to `distill`'s Calls tier.

`distill` extracts discrete gradeable calls — "long ETH off 2400, wrong below 2200".
`brain` extracts *narrative stance* — the lean, the reasoning behind it, and what would
change the speaker's mind. Stance is the right unit for the question this project exists
to answer:

> where is my roster on ETH, where do they disagree, and what changed?

Answering that needs both **aggregation across people** ("5 bullish, 2 bearish") and
**real quotes**. Retrieval alone gives passages but not counts; structure alone gives
counts but not evidence. So there are two legs joined on `transcript_ref`:

- a **structured leg** — one `core.stance.Stance` per (person, asset, video)
- an **evidence leg** — narrative chunks embedded locally and searched by cosine

## Layout

| Module | Role |
|---|---|
| `prompt.py` | narrative-stance system prompt (explicitly *not* levels/entries) |
| `schema.py` | flat JSON schema handed to the `claude -p` backend |
| `extract.py` | transcript → stances, validated **per item** |

## Why per-item validation

Phase 2's `TradeThesis.key_levels` had `min_length=1` and the batch validated
all-or-nothing, so one bad item destroyed a whole video's extraction — 13 such failures
in the re-distill. Stance has no equivalent hard invariant, so `core.stance.parse_stances`
validates each item independently and reports what it dropped. A malformed stance never
takes its siblings with it, and it is never dropped *silently*.
