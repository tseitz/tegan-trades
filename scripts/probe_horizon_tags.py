"""Do stance horizons put a view in the right term — near (scalp, swing) or long (position, macro)?

FINDINGS, 2026-11-10, 10,153 stances

3,336 rationales name exactly one side of the timeframe split. On those, the tagged term and the
rationale's own words disagree on 1,214 (36.4%), and the error is one-directional: 1,124 are
tagged near (1,115 of them `swing`) while the rationale cites weekly / monthly / cycle structure.
Long-tagged views citing intraday words are only 90, and mostly regex false hits ("intraday
reversals mask a multi-month downtrend").

Hand-read of 20 disagreements: ~7 clearly mis-termed (`swing` on "years of back order", a central
bank pivot, a cycle fractal), ~4 regex false hits, ~9 genuinely ambiguous — a swing *trade* read
off a weekly or quarterly *chart*. `swing` is the extractor's default, and the prompt defines no
day ranges (`brain/prompt.py`, the `horizon` field), so "swing trade on a weekly breaker" and
"the weekly is bearish" land in the same tag.

Current people per term on digest assets (non-stale, newest per person per term): BTC near 12 /
long 18, ETH 12/12, SPX 11/16, GOLD 9/16, MSTR 4/11, HYPE 2/8. Single-author crosswinds: BTC 4,
ETH 4, NVDA 3, SPX 3, GOLD 2, MSTR 2, SOL 0, HYPE 0.
"""
from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from brain.stance_store import load_all_stances
from core.canon import load_registry, resolve_asset, resolve_person
from core.term import is_stale

ROOT = Path(__file__).resolve().parents[1]
TERM = {"scalp": "near", "swing": "near", "position": "long", "macro": "long", None: "unset"}

INTRADAY = re.compile(r"\b(\d{1,2}\s?-?(?:h|hr|hour|min|m)\b|hourly|intraday|daily|today|this week|"
                      r"next few days|short[- ]term|scalp)", re.I)
HTF = re.compile(r"\b(weekly|monthly|quarterly|yearly|cycle|multi-?year|years?|long[- ]term|"
                 r"next decade|macro)\b", re.I)
DIGEST_ASSETS = ["BTC", "ETH", "SOL", "SPX", "GOLD", "MSTR", "HYPE", "NVDA"]


def timeframe_term(text: str) -> str | None:
    """The term a rationale's own words point to, or None when it names both or neither."""
    near, long_ = bool(INTRADAY.search(text)), bool(HTF.search(text))
    if near == long_:
        return None
    return "near" if near else "long"


def transcript_lines(ref: str, pattern: re.Pattern, limit: int = 2) -> list[str]:
    path = ROOT / ref
    txt = path.with_suffix(".txt")
    body = txt.read_text() if txt.exists() else (path.read_text() if path.exists() else "")
    hits = [m.start() for m in pattern.finditer(body)][:limit]
    return [" ".join(body[max(0, h - 140):h + 140].split()) for h in hits]


def main() -> None:
    stances = load_all_stances()
    registry = load_registry(ROOT / "cfg")
    today = datetime.now(UTC).date()

    tab = Counter()
    disagree = []
    for s in stances:
        said = timeframe_term(s.rationale)
        tagged = TERM[s.horizon]
        if said is None or tagged == "unset":
            continue
        tab[(tagged, said)] += 1
        if tagged != said:
            disagree.append(s)

    bearing = sum(tab.values())
    wrong = sum(v for (t, w), v in tab.items() if t != w)
    print(f"{len(stances)} stances; {bearing} name exactly one timeframe side")
    for (t, w), v in sorted(tab.items()):
        print(f"  tagged {t:<5} rationale says {w:<5} {v}")
    print(f"  disagreement {wrong}/{bearing} = {wrong / bearing:.1%}")
    by_h = Counter(s.horizon for s in disagree)
    print(f"  disagreements by tagged horizon: {dict(by_h)}")

    random.seed(7)
    print("\n── 20 disagreements, rationale + transcript context ──")
    for s in random.sample(disagree, min(20, len(disagree))):
        pat = INTRADAY if TERM[s.horizon] == "long" else HTF
        print(f"\n[{s.horizon}] {s.source.person} {s.asset} {s.lean} {s.source.published_at}")
        print(f"  R: {s.rationale[:260]}")
        for line in transcript_lines(s.source.transcript_ref, pat):
            print(f"  T: …{line}…")


    latest: dict[tuple, object] = {}
    for s in stances:
        a = resolve_asset(s.asset, registry)[0]
        p = resolve_person(s.source.person, registry)[0]
        if is_stale(s.source.published_at, s.horizon, today):
            continue
        key = (a, p, TERM[s.horizon])
        if key not in latest or (s.source.published_at or "") > (latest[key].source.published_at or ""):
            latest[key] = s
    print("\n── current people per term, crosswinds (single-author) ──")
    for asset in DIGEST_ASSETS:
        per = defaultdict(set)
        leans = defaultdict(dict)
        for (a, p, t), s in latest.items():
            if a != asset:
                continue
            per[t].add(p)
            leans[p][t] = s.lean
        cw = [p for p, d in leans.items() if p not in registry.members
              and {d.get("near"), d.get("long")} == {"bullish", "bearish"}]
        counts = {t: len(v) for t, v in sorted(per.items())}
        print(f"  {asset:<5} {counts}  crosswinds={cw}")


if __name__ == "__main__":
    main()
