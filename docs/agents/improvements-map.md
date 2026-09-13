# `docs/IMPROVEMENTS.md` § → GitHub issue

`docs/IMPROVEMENTS.md` was the backlog until 2026-09-13, when all 43 entries became GitHub
issues. Comments across `packages/`, `scripts/`, `cfg/` and `.claude/plans/` still cite entries
by their old `§n`. This table resolves them.

The old numbers are frozen — nothing will ever add to this table, so it cannot drift. Closing an
issue does not invalidate a row; a closed issue stays readable on GitHub, which a deleted file
section did not.

| Old | Issue | Title |
| --- | --- | --- |
| §1 | [#1](https://github.com/tseitz/tegan-trades/issues/1) | Levels are not the product — sentiment and trust are |
| §2 | [#2](https://github.com/tseitz/tegan-trades/issues/2) | Rip out the fixed horizon constants |
| §3 | [#3](https://github.com/tseitz/tegan-trades/issues/3) | Take another lap on ICT — mine TraderMayne's courses |
| §4 | [#4](https://github.com/tseitz/tegan-trades/issues/4) | Revealed preference is the only ground truth |
| §4b | [#5](https://github.com/tseitz/tegan-trades/issues/5) | The decision sidecars are irreplaceable and unbacked |
| §6b | [#6](https://github.com/tseitz/tegan-trades/issues/6) | `brain/report.py` keeps its own staleness cliff |
| §6d | [#7](https://github.com/tseitz/tegan-trades/issues/7) | An unreachable channel reports as an up-to-date one |
| §6f | [#8](https://github.com/tseitz/tegan-trades/issues/8) | Assets that never reach the gates |
| §6h | [#9](https://github.com/tseitz/tegan-trades/issues/9) | The roster's channel metadata is unverified and drifts |
| §8 | [#10](https://github.com/tseitz/tegan-trades/issues/10) | Evidence-leg retrieval doesn't discriminate on asset queries |
| §9 | [#11](https://github.com/tseitz/tegan-trades/issues/11) | Audit extraction efficiency before considering the API |
| §11 | [#12](https://github.com/tseitz/tegan-trades/issues/12) | Agreement is date-blind |
| §12 | [#13](https://github.com/tseitz/tegan-trades/issues/13) | Slice 2 needs the oracle at sub-daily granularity |
| §14 | [#14](https://github.com/tseitz/tegan-trades/issues/14) | X/Twitter ingestion is decided but entirely unbuilt |
| §15 | [#15](https://github.com/tseitz/tegan-trades/issues/15) | No concept of moving averages as levels |
| §18 | [#16](https://github.com/tseitz/tegan-trades/issues/16) | `collapse` picks the group's *oldest* target by construction |
| §19 | [#17](https://github.com/tseitz/tegan-trades/issues/17) | Reachability |
| §21 | [#18](https://github.com/tseitz/tegan-trades/issues/18) | Funding is a real cost and nothing in the scorer sees it |
| §22 | [#19](https://github.com/tseitz/tegan-trades/issues/19) | Lighter's funding history feed does not reconcile with its snapshot feed |
| §24 | [#20](https://github.com/tseitz/tegan-trades/issues/20) | Mirror the funding log the way decisions are mirrored |
| §25 | [#21](https://github.com/tseitz/tegan-trades/issues/21) | Route each order to the venue that is actually cheapest |
| §27 | [#22](https://github.com/tseitz/tegan-trades/issues/22) | The daily leg vetoes a weekly it should only time |
| §29 | [#23](https://github.com/tseitz/tegan-trades/issues/23) | A bare currency cannot be priced off a pair that inverts it |
| §30 | [#24](https://github.com/tseitz/tegan-trades/issues/24) | A crypto short's only venue is one whose terms exclude us |
| §31 | [#25](https://github.com/tseitz/tegan-trades/issues/25) | A venue mapping can be verified from price, and should be |
| §32 | [#26](https://github.com/tseitz/tegan-trades/issues/26) | A bare ticker that resolves is not the right instrument |
| §36 | [#27](https://github.com/tseitz/tegan-trades/issues/27) | Two perp guards do not transfer to equities |
| §39 | [#28](https://github.com/tseitz/tegan-trades/issues/28) | Refuse a fill when the open has eaten the stop |
| §40 | [#29](https://github.com/tseitz/tegan-trades/issues/29) | Nothing sums — sizing is per-trade and the account is not |
| §41 | [#30](https://github.com/tseitz/tegan-trades/issues/30) | Most of the roster's Dow conviction is in the key with no route |
| §42 | [#31](https://github.com/tseitz/tegan-trades/issues/31) | RUT's Lighter and Aster rows need a re-probe on the new basis |
| §43 | [#32](https://github.com/tseitz/tegan-trades/issues/32) | `crossing` is the last unpriced routing term, and it decides real calls |
| §44 | [#33](https://github.com/tseitz/tegan-trades/issues/33) | Nothing compares the instruments an asset could be expressed as |
| §45 | [#34](https://github.com/tseitz/tegan-trades/issues/34) | A short `assetCtxs` silently shortens the mark sweep |
| §47 | [#35](https://github.com/tseitz/tegan-trades/issues/35) | The circuit breaker cannot bound its own overshoot |
| §48 | [#36](https://github.com/tseitz/tegan-trades/issues/36) | The entry and the target are on two different clocks |
| §49 | [#37](https://github.com/tseitz/tegan-trades/issues/37) | Exit levels are structural edges, not liquidity pools |
| §50 | [#38](https://github.com/tseitz/tegan-trades/issues/38) | Return an approval to the queue only when a real venue rejected it |
| §51 | [#39](https://github.com/tseitz/tegan-trades/issues/39) | Say `unmapped` when the map is silent, and curate what Alpaca lists |
| §52 | [#40](https://github.com/tseitz/tegan-trades/issues/40) | Offer to free room instead of refusing when the book is full |
| §53 | [#41](https://github.com/tseitz/tegan-trades/issues/41) | Revisit Dune Analytics once there's a manual-query use case, not a nightly one |
| §54 | [#42](https://github.com/tseitz/tegan-trades/issues/42) | Give `brain` a sentiment layer once the alt-signal numbers exist to feed it |
| §55 | [#43](https://github.com/tseitz/tegan-trades/issues/43) | Reach the signal sources the probe names as unreachable |

The pinned [Where to start](https://github.com/tseitz/tegan-trades/issues/44) issue holds the
ranked priority view the file opened with.

## References this table cannot resolve

- **Sub-parts of an entry** — `§4a` `§4c` `§4d` `§19c` `§19d`. These name a lettered point inside
  an entry's prose, not an entry. Read the parent issue (#4, #17) and find the point.
- **Entries deleted before the import** — `§5` `§7` `§20` `§28` `§35`. They were already dead
  links; numbers were never reused. Git history has them: `git log -- docs/IMPROVEMENTS.md`.

## Not this tracker

`§` does not always mean an entry. `architecture.md §C` / `§D` are sections of the design doc,
`ToS §1.5` is Hyperliquid's terms of service, and `_Structure.md § the entry trigger` is a named
heading. Check what precedes the `§` before assuming.
