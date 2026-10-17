"""Where does our structure engine disagree with LuxAlgo's Smart Money Concepts (SMC)?

Runs SMC — the reference ICT/SMC indicator — through PineTS on the same cached daily bars
``core.structure`` and ``core.imbalance`` read, and lines the two up: breaks of structure,
order blocks, fair value gaps (FVG), plus SMC's equal highs/lows, which we have no counterpart
for yet.

**Most disagreement is definitional, so read it against the control.** SMC's pivot is a
one-sided leg flip (``high[size] > ta.highest(size)``, internal size 5, swing size 50); ours is
a symmetric strict window of ``SWING_WIDTH`` each side. The control row — our width 2 against
our width 3 through the same matcher — is the disagreement pivot choice alone produces.
**Decision:** a break SMC sees that we miss for a reason other than the pivot definition
(``broke on another bar`` below) is a candidate defect in ``core.structure``; bring it back
rather than tuning past it.

MEASURED 2026-10-16, daily BTC/ETH/SOL/SPY, from bar 250:
- **Breaks.** With SMC's internal pivot at size 2, ours agrees about as well as our own width 2
  against width 3: Jaccard 45–63% against 38–59%. **Zero candidate defects.**
- **Breaks SMC sees and we miss (79):** 75 are "superseded". The pivot was one of our swings,
  but a newer swing replaced it as the live target before price closed through it, per the
  most-recent-only rule in ``structure.breaks``. These include every swing/CHoCH at a 50-bar
  level. In ``breaks`` major structure reaches us only through a higher timeframe; as a level,
  ``core.liquidity`` keeps it (below).
- **FVGs.** Ours are nearly a strict subset of SMC's; its body threshold is looser.
- **Order blocks** on matched breaks overlap SMC's on crypto but are disjoint on 5 of 10 for SPY.
- **Liquidity.** Of SMC's 14 swing-scale breaks, the bar before each: 12 were one of our
  ``core.liquidity`` pools — 7 inside the weekly dealing range (a partial or the boundary), 5
  the single nearest pool past it, which ``core.exits`` keeps as the one runner — and 2 were not
  pools. So the superseded majors ``breaks`` drops do reach the ladder, and the runner is half of
  that. SMC's equal highs/lows match our 2+-touch pools only 5 of 26: its pivots are one-sided
  at size 3 and its tolerance is ATR(200), so this is definition, not a miss.

**Trust comes from known-answer checks, not from PineTS.** Every SMC break is asserted against
the bars (close crosses the level on that bar, the pivot bar's extreme equals the level), and
SMC's FVG and order-block rules are re-implemented here and must reproduce what PineTS drew.
PineTS 0.11.1 bugs are worked around in the runtime copy of the source; each entry in
``PATCHES`` says which.

Cost: free, no LLM. Network on first run — the SMC source from mcp.luxalgo.com and
``npx pinets-cli`` (top-level pinned, transitive deps not) — then cached. Runtime-fetched Pine
executes through a JS transpiler, unsandboxed, as you, on the disk that holds ``.env`` and the
signing key — hence the pinned ``SMC_SHA256``. The SMC source is CC BY-NC-SA and this repo is
public: it is cached under ``data/`` only, never anywhere tracked.

    uv run python scripts/probe_smc_parity.py [source:symbol ...] [--refresh]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from core import imbalance, structure
from core.dealing_range import dealing_range
from core.imbalance import true_range
from core.liquidity import POOL_TOLERANCE_ATR, pools
from core.structure import BEARISH, BULLISH, SWING_HIGH, SWING_LOW
from oracle import cache
from oracle.resample import to_weekly

SCRATCH = Path(__file__).resolve().parents[1] / "data" / "scratch" / "smc"
MCP_URL = "https://mcp.luxalgo.com/mcp"
SMC_SLUG = "smart-money-concepts-smc"
# Third-party code runs from this; an upstream change must be read before it executes. To accept
# one: --refresh, diff data/scratch/smc/smc.pine against the old copy, then update this.
SMC_SHA256 = "a8046ad353c1b495e3c31e79365d7658a295be743ba56293e7d4c861813af48b"
MAX_RESPONSE_BYTES = 2_000_000
PINETS = ("npx", "--yes", "pinets-cli@0.1.15")
DEFAULT_ASSETS = ("coinbase:BTC-USD", "coinbase:ETH-USD", "coinbase:SOL-USD", "yahoo:SPY")

# SMC's ta.atr(200) feeds its order-block filter and equal highs/lows, and its swing pivot needs
# 50 bars; nothing is compared before this index.
WARMUP = 250
# Both engines read the same bar high/low, so a broken level matches exactly or not at all.
LEVEL_RTOL = 1e-9
# Both breaks are close-based on the same bars. Measured 2026-10-16 on the four default assets:
# slack 2 matched exactly what 0 did, so a matching level always broke on the same bar.
BREAK_SLACK = 0
# SMC's max_lines_count / max_labels_count; past it the oldest objects are evicted silently.
OBJECT_CAP = 500
OB_POOL = 20

BULL_COLOR, BEAR_COLOR = "#089981", "#f23645"
BOX_KINDS = {
    "#3179f5": ("ob", "internal", BULLISH), "#f77c80": ("ob", "internal", BEARISH),
    "#1848cc": ("ob", "swing", BULLISH), "#b22833": ("ob", "swing", BEARISH),
    "#00ff68": ("fvg", None, BULLISH), "#ff0008": ("fvg", None, BEARISH),
}

FVG_TUPLE = ("[lastClose, lastOpen, lastTime, currentHigh, currentLow, currentTime, last2High, "
             "last2Low] = request.security(syminfo.tickerid, fairValueGapsTimeframeInput, "
             "[close[1], open[1], time[1], high[0], low[0], time[0], high[2], low[2]],lookahead = "
             "barmerge.lookahead_on)")
# Plain assignments, not a helper returning the tuple: PineTS returns na for every element of a
# user function's tuple of history references.
FVG_SERIES = "\n    ".join(f"{name} = {expr}" for name, expr in (
    ("lastClose", "close[1]"), ("lastOpen", "open[1]"), ("lastTime", "time[1]"),
    ("currentHigh", "high[0]"), ("currentLow", "low[0]"), ("currentTime", "time[0]"),
    ("last2High", "high[2]"), ("last2Low", "low[2]")))
PATCHES = (
    ("showFairValueGapsInput          = input.bool(false",
     "showFairValueGapsInput          = input.bool(true"),
    ("showSwingOrderBlocksInput       = input.bool(false",
     "showSwingOrderBlocksInput       = input.bool(true"),
    ("internalOrderBlocksSizeInput    = input.int(    5,",
     f"internalOrderBlocksSizeInput    = input.int(    {OB_POOL},"),
    ("swingOrderBlocksSizeInput       = input.int(    5,",
     f"swingOrderBlocksSizeInput       = input.int(    {OB_POOL},"),
    # Equivalent only because the FVG timeframe input defaults to the chart's (''), which
    # _patched asserts.
    (FVG_TUPLE, FVG_SERIES),
    ("newTimeframe        = timeframe.change(fairValueGapsTimeframeInput)",
     "newTimeframe        = true"),
    # PineTS loses box fields stored on a user-defined type, so deleting a filled gap's boxes
    # crashes. Kept drawn instead: the boxes become every gap SMC ever created, and liveness
    # comes from smc_fvgs.
    ("            eachFairValueGap.topBox.delete()\n"
     "            eachFairValueGap.bottomBox.delete()\n", ""),
)
INTERNAL_CALL = "getCurrentStructure(5,false,true)"


class ParityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Event:
    kind: str           # BULLISH | BEARISH
    level: float
    pivot_index: int
    index: int          # the breaking bar
    tag: str = ""       # BOS | CHoCH, SMC only
    scope: str = ""     # internal | swing, SMC only


@dataclass(frozen=True, slots=True)
class Zone:
    kind: str
    top: float
    bottom: float
    index: int          # OB candle, or an FVG's middle candle
    scope: str = ""


# ── SMC source ──────────────────────────────────────────────────────────────

def smc_source(refresh: bool) -> str:
    path = SCRATCH / "smc.pine"
    if refresh or not path.exists():
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "library_get_source_code", "arguments": {"slug": SMC_SLUG}}}).encode()
        req = urllib.request.Request(MCP_URL, data=body, headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            if not resp.url.startswith(MCP_URL):
                raise ParityError(f"redirected away from {MCP_URL} to {resp.url}")
            raw = resp.read(MAX_RESPONSE_BYTES + 1).decode()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ParityError(f"response over {MAX_RESPONSE_BYTES} bytes")
        try:
            data = next(line[6:] for line in raw.splitlines() if line.startswith("data: "))
            payload = json.loads(json.loads(data)["result"]["content"][0]["text"])
        except (StopIteration, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise ParityError(f"unexpected response from {MCP_URL}: {raw[:300]!r}") from exc
        if not payload.get("available"):
            raise ParityError(f"{SMC_SLUG} source not served: {payload}")
        SCRATCH.mkdir(parents=True, exist_ok=True)
        path.write_text(payload["source"], encoding="utf-8")
    source = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != SMC_SHA256:
        raise ParityError(f"SMC source sha256 {digest} is not the pinned {SMC_SHA256}; "
                          "read the change before accepting it (see SMC_SHA256)")
    print(f"SMC source sha256 {digest[:16]}  ({path})")
    return source


def _patched(source: str, internal_size: int) -> str:
    if "input.timeframe('',       'Timeframe'" not in source:
        raise ParityError("FVG timeframe input no longer defaults to the chart's; "
                          "the FVG_SERIES patch is no longer equivalent")
    patches = (*PATCHES, (INTERNAL_CALL, f"getCurrentStructure({internal_size},false,true)"))
    for old, new in patches:
        if source.count(old) != 1:
            raise ParityError(f"SMC source changed upstream: {old[:60]!r} matched "
                              f"{source.count(old)} times, expected 1")
        source = source.replace(old, new)
    return source


# ── running it ──────────────────────────────────────────────────────────────

def _ms(day) -> int:
    return int(datetime.combine(day, time(), tzinfo=UTC).timestamp() * 1000)


def run_smc(pine: str, bars, tag: str) -> dict:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    bars_path, pine_path = SCRATCH / f"{tag}.bars.json", SCRATCH / f"{tag}.pine"
    out_path = SCRATCH / f"{tag}.out.json"
    bars_path.write_text(json.dumps([
        {"openTime": _ms(b.date), "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": 0, "closeTime": _ms(b.date) + 86_399_999}
        for b in bars]), encoding="utf-8")
    pine_path.write_text(pine, encoding="utf-8")
    cmd = [*PINETS, "run", str(pine_path), "--data", str(bars_path), "-n", str(len(bars)),
           "-f", "full", "-q", "-o", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise ParityError(f"pinets failed ({proc.returncode}):\n{proc.stderr or proc.stdout}")
    doc = json.loads(out_path.read_text(encoding="utf-8"))

    market = doc["marketData"]
    if len(market) != len(bars):
        raise ParityError(f"{tag}: pinets returned {len(market)} bars for {len(bars)}")
    for got, bar in zip(market, bars, strict=True):
        if got["openTime"] != _ms(bar.date):
            raise ParityError(f"{tag}: bar time drift at {bar.date}: {got['openTime']}")
    return doc


def _objects(doc: dict, name: str) -> list[dict]:
    data = doc["plots"][name]["data"]
    if len(data) != 1:
        raise ParityError(f"{name}: expected one end-state snapshot, got {len(data)}")
    objects = data[0]["value"]
    if len(objects) >= OBJECT_CAP - 10:
        print(f"  WARNING {name}: {len(objects)} objects, near the {OBJECT_CAP} eviction cap")
    return objects


# ── parsing SMC ─────────────────────────────────────────────────────────────

def parse_smc(doc: dict, bars) -> tuple[list[Event], list[Event], list[Zone], list[Zone]]:
    """(breaks, equal highs/lows, drawn OB boxes, drawn FVGs). Every break is checked
    against the bars it claims to have happened on."""
    at = {_ms(b.date): i for i, b in enumerate(bars)}
    lines = {o["id"]: o for o in _objects(doc, "__lines__")}
    breaks, equals = [], []
    for label in _objects(doc, "__labels__"):
        text = label["text"]
        if text not in ("BOS", "CHoCH", "EQH", "EQL"):
            continue
        line = lines.get(label["id"])
        if line is None or line["y2"] != label["y"]:
            raise ParityError(f"label {label['id']} {text} has no matching line: {line}")
        color = (line["color"] or "").lower()
        if text in ("EQH", "EQL"):
            equals.append(Event(kind=BEARISH if text == "EQH" else BULLISH, level=line["y2"],
                                pivot_index=at[line["x1"]], index=at[line["x2"]], tag=text))
            continue
        if line["y1"] != line["y2"]:
            raise ParityError(f"structure line {line['id']} is not horizontal: {line}")
        kind = {BULL_COLOR: BULLISH, BEAR_COLOR: BEARISH}[color]
        event = Event(kind=kind, level=line["y1"], pivot_index=at[line["x1"]],
                      index=at[line["x2"]], tag=text,
                      scope="internal" if line["style"] == "style_dashed" else "swing")
        _check_break(event, bars)
        breaks.append(event)

    obs, fvg_edges = [], {}
    for box in _objects(doc, "__boxes__"):
        if box["top"] is None:
            continue
        color = (box["bgcolor"] or "")[:7].lower()
        if color not in BOX_KINDS:
            raise ParityError(f"box {box['id']} has unmapped colour {box['bgcolor']}")
        what, scope, kind = BOX_KINDS[color]
        if what == "ob":
            obs.append(Zone(kind=kind, top=box["top"], bottom=box["bottom"],
                            index=at[box["left"]], scope=scope))
        else:
            fvg_edges.setdefault((kind, at[box["left"]]), []).append((box["top"], box["bottom"]))
    fvgs = []
    for (kind, index), halves in fvg_edges.items():
        if len(halves) != 2:
            raise ParityError(f"FVG at bar {index} drawn as {len(halves)} boxes, expected 2")
        # Each gap is two half-boxes, and a bearish one is drawn with top below bottom.
        edges = [edge for half in halves for edge in half]
        fvgs.append(Zone(kind=kind, top=max(edges), bottom=min(edges), index=index))
    return breaks, equals, obs, sorted(fvgs, key=lambda z: z.index)


def _check_break(event: Event, bars) -> None:
    bar, prev, pivot = bars[event.index], bars[event.index - 1], bars[event.pivot_index]
    if event.kind == BULLISH:
        crossed = bar.close > event.level >= prev.close
        anchored = pivot.high == event.level
    else:
        crossed = bar.close < event.level <= prev.close
        anchored = pivot.low == event.level
    if not (crossed and anchored):
        raise ParityError(f"SMC break does not hold on the bars: {event} "
                          f"crossed={crossed} anchored={anchored} on {bar.date}")


# ── SMC's rules, re-implemented as known answers ────────────────────────────

def smc_fvgs(bars) -> tuple[list[Zone], list[Zone]]:
    """(every FVG, the ones still live) under SMC's chart-timeframe rule."""
    found, live, cum = [], [], 0.0
    for i, bar in enumerate(bars):
        # Deletion runs before drawing on each bar, so a gap is first tested the bar after.
        live = [z for z in live if not (
            (z.kind == BULLISH and bar.low < z.bottom) or (z.kind == BEARISH and bar.high > z.top))]
        if i < 1:
            continue
        # Bar 1 already has a close[1], so its delta joins the running mean before any gap
        # can form; skipping it shifts every early threshold.
        mid = bars[i - 1]
        delta = (mid.close - mid.open) / (mid.open * 100)
        cum += abs(delta)
        threshold = cum / i * 2
        if i < 2:
            continue
        c1 = bars[i - 2]
        zone = None
        if bar.low > c1.high and mid.close > c1.high and delta > threshold:
            zone = Zone(kind=BULLISH, top=bar.low, bottom=c1.high, index=i - 1)
        elif bar.high < c1.low and mid.close < c1.low and -delta > threshold:
            zone = Zone(kind=BEARISH, top=bar.high, bottom=c1.low, index=i - 1)
        if zone:
            found.append(zone)
            live.insert(0, zone)
    return found, live


def _parsed_extremes(bars) -> tuple[list[float], list[float]]:
    """SMC's parsedHighs/parsedLows: a bar ranging over 2×ATR(200) has its high and low
    swapped, so it can't be picked as an order block's extreme."""
    highs, lows, atr = [], [], None
    trs = []
    for i, bar in enumerate(bars):
        tr = true_range(bar, bars[i - 1].close if i else None)
        trs.append(tr)
        if i == 199:
            atr = sum(trs) / 200
        elif atr is not None:
            atr = (atr * 199 + tr) / 200
        volatile = atr is not None and (bar.high - bar.low) >= 2 * atr
        highs.append(bar.low if volatile else bar.high)
        lows.append(bar.high if volatile else bar.low)
    return highs, lows


def smc_order_blocks(bars, breaks: list[Event]) -> dict[Event, Zone]:
    """SMC's order block for each of its breaks: the extreme parsed candle between the broken
    pivot and the breaking bar."""
    highs, lows = _parsed_extremes(bars)
    out = {}
    for event in breaks:
        span = range(event.pivot_index, event.index)
        if event.kind == BEARISH:
            pick = max(span, key=lambda i: (highs[i], -i))
        else:
            pick = min(span, key=lambda i: (lows[i], i))
        out[event] = Zone(kind=event.kind, top=highs[pick], bottom=lows[pick], index=pick,
                          scope=event.scope)
    return out


def smc_live(bars, blocks: dict[Event, Zone]) -> list[Zone]:
    """Order blocks SMC still holds at the last bar: not wicked through their far edge since
    the break, newest OB_POOL per scope."""
    by_scope: dict[str, list[tuple[int, Zone]]] = {}
    for event, zone in blocks.items():
        after = bars[event.index + 1:]
        dead = (any(b.low < zone.bottom for b in after) if zone.kind == BULLISH
                else any(b.high > zone.top for b in after))
        if not dead:
            by_scope.setdefault(zone.scope, []).append((event.index, zone))
    return [z for items in by_scope.values()
            for _, z in sorted(items, key=lambda t: -t[0])[:OB_POOL]]


# ── ours, and matching ──────────────────────────────────────────────────────

def ours(bars, width: int) -> list[Event]:
    return [Event(kind=b.kind, level=b.level, pivot_index=b.swing.index, index=b.index)
            for b in structure.breaks(bars, width=width)]


def match(left: list[Event], right: list[Event]):
    """Greedy one-to-one: same kind, same level, breaking bar within BREAK_SLACK."""
    pairs, unmatched = [], list(right)
    left_only = []
    for a in left:
        hit = next((b for b in unmatched if b.kind == a.kind
                    and abs(b.level - a.level) <= LEVEL_RTOL * abs(a.level)
                    and abs(b.index - a.index) <= BREAK_SLACK), None)
        if hit is None:
            left_only.append(a)
        else:
            pairs.append((a, hit))
            unmatched.remove(hit)
    return pairs, left_only, unmatched


def _after_warmup(events):
    return [e for e in events if e.index >= WARMUP]


def why_missed(event: Event, bars, ours_breaks: list[Event]) -> str:
    """Why our width-2 engine has no break matching an SMC one."""
    kind = SWING_HIGH if event.kind == BULLISH else SWING_LOW
    if not any(s.index == event.pivot_index and s.kind == kind for s in structure.swings(bars)):
        return "not our swing"
    same = [b for b in ours_breaks if b.pivot_index == event.pivot_index and b.kind == event.kind]
    if same:
        return f"broke on another bar ({bars[same[0].index].date})"
    return "superseded by a newer swing"


def _span(z: Zone) -> tuple:
    return z.kind, z.index, round(min(z.top, z.bottom), 6), round(max(z.top, z.bottom), 6)


def _overlaps(a: Zone, b: Zone) -> bool:
    return min(a.top, a.bottom) <= max(b.top, b.bottom) and min(b.top, b.bottom) <= max(a.top, a.bottom)


# ── report ──────────────────────────────────────────────────────────────────

def _row(label: str, left: list[Event], right: list[Event]) -> None:
    pairs, lo, ro = match(left, right)
    union = len(pairs) + len(lo) + len(ro)
    print(f"  {label:38} {len(left):4} {len(right):4} {len(pairs):5} {len(lo):5} {len(ro):5}"
          f"  {len(pairs) / union if union else 0:5.0%}")


def _pools_at(bars, index: int):
    """Live pools on ``bars`` as of ``index``, with the tolerance they were clustered at."""
    yardstick = imbalance.atr(bars[: index + 1], index)
    if yardstick is None:
        return (), 0.0
    tolerance = POOL_TOLERANCE_ATR * yardstick
    return pools(bars, as_of=bars[index].date, tolerance=tolerance), tolerance


def liquidity_reach(event: Event, series, weekly) -> str:
    """Where an SMC swing level stood in our liquidity model the bar before it broke."""
    before = event.index - 1
    side = SWING_HIGH if event.kind == BULLISH else SWING_LOW
    sign = 1 if event.kind == BULLISH else -1
    daily_pools, daily_tol = _pools_at(series.bars, before)
    week_end = max((i for i, w in enumerate(weekly) if w.date <= series.bars[before].date),
                   default=None)
    weekly_pools, weekly_tol = (_pools_at(weekly, week_end) if week_end is not None
                                else ((), 0.0))
    levels = [(p.price, tol) for found, tol in ((daily_pools, daily_tol),
                                                 (weekly_pools, weekly_tol))
              for p in found if p.kind == side]
    if not any(abs(price - event.level) <= tol for price, tol in levels):
        return "not a pool"
    span = dealing_range(weekly, as_of=series.bars[before].date,
                         price=series.bars[before].close)
    if span is None:
        return "no range"
    bound = span.high if sign > 0 else span.low
    if (event.level - bound) * sign <= 0:
        return "inside the range"
    past = sorted((price for price, _ in levels if (price - bound) * sign > 0),
                  key=lambda price: (price - bound) * sign)
    nearest = past[0]
    tol = max(t for _, t in levels)
    return "the runner" if abs(nearest - event.level) <= tol else "behind the runner"


def probe(ref: str, source: str, misses: list) -> None:
    src, symbol = ref.split(":", 1)
    series = cache.load(src, symbol)
    if series is None or len(series.bars) <= WARMUP:
        print(f"\n{ref}: not cached or under {WARMUP} bars, skipped")
        return
    bars = series.bars
    tag = symbol.replace("/", "_")
    default = run_smc(_patched(source, 5), bars, f"{tag}-int5")
    tight = run_smc(_patched(source, 2), bars, f"{tag}-int2")
    smc5, equals, drawn_obs, drawn_fvgs = parse_smc(default, bars)
    smc2, *_ = parse_smc(tight, bars)

    # Known answers: our copies of SMC's rules must reproduce what PineTS drew.
    all_fvgs, live_fvgs = smc_fvgs(bars)
    if [_span(z) for z in all_fvgs] != [_span(z) for z in drawn_fvgs]:
        raise ParityError(f"{ref}: FVG re-implementation disagrees with PineTS\n"
                          f"  ours  {all_fvgs}\n  drawn {drawn_fvgs}")
    blocks = smc_order_blocks(bars, smc5)
    expected = {(z.scope, z.kind, z.index) for z in smc_live(bars, blocks)}
    drawn = {(z.scope, z.kind, z.index) for z in drawn_obs}
    if expected != drawn:
        raise ParityError(f"{ref}: OB re-implementation disagrees with PineTS: "
                          f"only ours {sorted(expected - drawn)}, only drawn {sorted(drawn - expected)}")

    w2, w3 = ours(bars, 2), ours(bars, 3)
    print(f"\n{ref}: {len(bars)} bars {bars[0].date}..{bars[-1].date}, compared from "
          f"{bars[WARMUP].date} (bar {WARMUP})")
    print(f"  {'breaks':38} {'left':>4} {'rght':>4} {'match':>5} {'L-only':>5} {'R-only':>5}"
          f"  {'jacc':>5}")
    _row("ours w2 vs SMC int5+swing50", _after_warmup(w2), _after_warmup(smc5))
    _row("ours w2 vs SMC int2+swing50", _after_warmup(w2), _after_warmup(smc2))
    _row("control: ours w2 vs ours w3", _after_warmup(w2), _after_warmup(w3))
    tags = Counter((e.scope, e.tag) for e in _after_warmup(smc5))
    print("  SMC int5 events: " + "  ".join(f"{s}/{t}={n}" for (s, t), n in sorted(tags.items())))

    pairs, _, theirs_only = match(_after_warmup(w2), _after_warmup(smc5))
    reasons = [why_missed(e, bars, w2) for e in theirs_only]
    misses.extend((ref, bars, e, why) for e, why in zip(theirs_only, reasons, strict=True))
    print("  SMC-only by reason: " + "  ".join(
        f"{r}={n}" for r, n in Counter(why.split(" (")[0] for why in reasons).items()))

    # Order blocks for the same break, so candidate selection is the only difference.
    our_blocks = {(b.bos.index, b.kind): b for b in structure.order_blocks(bars)}
    shapes = Counter()
    for ours_event, smc_event in pairs:
        mine = our_blocks.get((ours_event.index, ours_event.kind))
        theirs = blocks[smc_event]
        if mine is None:
            shapes["ours: no candle"] += 1
        elif mine.index == theirs.index:
            shapes["same candle"] += 1
        elif _overlaps(Zone(mine.kind, mine.top, mine.bottom, mine.index), theirs):
            shapes["overlapping"] += 1
        else:
            shapes["disjoint"] += 1
    print(f"  OBs on {len(pairs)} matched breaks: " + "  ".join(
        f"{k}={v}" for k, v in shapes.most_common()))
    live_ours = [b for b in structure.order_blocks(bars) if structure.invalidated_on(b, bars) is None]
    live_wick = [b for b in structure.order_blocks(bars) if not (
        any(x.low < b.bottom for x in bars[b.bos.index + 1:]) if b.kind == BULLISH
        else any(x.high > b.top for x in bars[b.bos.index + 1:]))]
    print(f"  live OBs: ours {len(live_ours)} (our close rule), {len(live_wick)} (SMC wick rule); "
          f"SMC {len(expected)}")

    ours_fvgs = imbalance.fair_value_gaps(bars)
    ours_live = imbalance.live_gaps(bars)
    for kind in (BULLISH, BEARISH):
        mine = {g.middle_index for g in ours_fvgs if g.kind == kind and g.index >= WARMUP}
        theirs = {z.index for z in all_fvgs if z.kind == kind and z.index + 1 >= WARMUP}
        print(f"  FVG {kind:8} ours {len(mine):3}  SMC {len(theirs):3}  same candle "
              f"{len(mine & theirs):3}   live: ours "
              f"{sum(g.kind == kind for g in ours_live)}  SMC {sum(z.kind == kind for z in live_fvgs)}")

    weekly = to_weekly(series).bars
    reach = Counter(liquidity_reach(e, series, weekly)
                    for e in _after_warmup(smc5) if e.scope == "swing")
    print("  SMC swing levels in our liquidity, bar before the break: " + "  ".join(
        f"{k}={v}" for k, v in reach.most_common()))

    recent = [e for e in equals if e.index >= WARMUP]
    paired = 0
    for e in recent:
        found, tol = _pools_at(bars, e.index)
        side = SWING_HIGH if e.tag == "EQH" else SWING_LOW
        paired += any(p.kind == side and p.touches >= 2 and abs(p.price - e.level) <= tol
                      for p in found)
    print(f"  SMC equal highs/lows that are one of our 2+-touch pools: {paired}/{len(recent)}")
    print(f"  equal highs/lows (SMC only): {sum(e.tag == 'EQH' for e in recent)} EQH, "
          f"{sum(e.tag == 'EQL' for e in recent)} EQL" + "".join(
              f"\n    {e.tag} {e.level:<12.6g} {bars[e.pivot_index].date} & {bars[e.index].date}"
              for e in recent[-4:]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Line our structure engine up against SMC.")
    parser.add_argument("assets", nargs="*", default=list(DEFAULT_ASSETS))
    parser.add_argument("--refresh", action="store_true", help="re-fetch the SMC source")
    args = parser.parse_args()

    source = smc_source(args.refresh)
    misses: list = []
    for ref in args.assets:
        probe(ref, source, misses)

    candidates = [m for m in misses if m[3].startswith("broke on another bar")]
    print(f"\ncandidate defects — SMC breaks our swing on a bar we don't ({len(candidates)}):")
    for ref, bars, e, why in candidates[-10:]:
        print(f"  {ref:18} {e.kind:8} {e.scope}/{e.tag:5} level {e.level:<12.6g} "
              f"pivot {bars[e.pivot_index].date}  SMC {bars[e.index].date}  ours {why}")
    print("\nmost recent SMC-only breaks:")
    for ref, bars, e, why in sorted(misses, key=lambda m: m[1][m[2].index].date)[-10:]:
        print(f"  {ref:18} {e.kind:8} {e.scope}/{e.tag:5} level {e.level:<12.6g} "
              f"pivot {bars[e.pivot_index].date}  break {bars[e.index].date}  — {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
