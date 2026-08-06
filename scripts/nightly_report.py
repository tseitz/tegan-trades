#!/usr/bin/env python3
"""What the nightly cycle has actually been doing — trends, not last night.

`data/logs/nightly/<stamp>.log` answers "what happened last night" and is rotated away after
30. It cannot answer the questions that matter once the cycle has ten steps in it: which one
is eating the wall clock, whether distill is drifting upward as the corpus grows, how often
ingest-x fails, whether the corpus is still growing at all. Those need history, so
`nightly.sh` appends one JSON row per run to `history.jsonl` and this reads it.

    uv run python scripts/nightly_report.py
    uv run python scripts/nightly_report.py --last 14
    uv run python scripts/nightly_report.py --backfill   # seed from existing .log files

**Timings are reported as median and max, never mean.** Two runs in this corpus report
`ingest-roster (10354s)` and `(15016s)` for about four minutes of real work — the laptop was
in DarkWake and the job was frozen, not slow (see `nightly.sh`'s gate comment). A mean folds
those in and makes every step look catastrophic; a median ignores them, and the max still
shows you they happened.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG_DIR = REPO / "data" / "logs" / "nightly"
HISTORY = LOG_DIR / "history.jsonl"

# `  ok    distill-roster (188s)` / `  FAIL  ingest-x (301s) rc=1` / `  WARN  ... — reason`
_STEP = re.compile(r"^\s+(ok|FAIL|WARN|skip)\s+([a-z-]+)\s+\((\d+)s\)")
_XAI = re.compile(r"xAI \(real money\):\s+\$([0-9.]+)")
_CLAUDE = re.compile(r"claude \(Max allowance\): \$([0-9.]+) over (\d+) calls")
_CANDIDATES = re.compile(r"candidates:\s+(\d+)")
_FUNDING = re.compile(r"funding observations:\s+(\d+)")
_EXIT = re.compile(r"exit:\s+(\d+)")


def parse_log(path: Path) -> dict | None:
    """Reconstruct a history row from a pre-existing nightly log.

    Best-effort by design: these logs were written to be read by a person, so a field that was
    never printed is simply absent rather than guessed at. Used only by --backfill.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if "───── summary ─────" not in text:
        return None
    summary = text.split("───── summary ─────", 1)[1]

    steps = [
        {"name": m.group(2), "status": m.group(1).lower(), "seconds": int(m.group(3))}
        for m in (_STEP.match(line) for line in summary.splitlines())
        if m
    ]
    if not steps:
        return None

    def grab(rx, cast=float, group=1, default=0):
        m = rx.search(summary)
        return cast(m.group(group)) if m else default

    stamp = path.stem  # 20260805-0748
    try:
        run = datetime.strptime(stamp, "%Y%m%d-%H%M").replace(tzinfo=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        run = stamp

    return {
        "run": run,
        "exit": grab(_EXIT, int),
        "duration_s": sum(int(s["seconds"]) for s in steps),
        "steps": steps,
        "cost": {
            "xai": grab(_XAI),
            "claude": grab(_CLAUDE),
            "claude_calls": grab(_CLAUDE, int, group=2),
        },
        "output": {
            "candidates": grab(_CANDIDATES, int),
            "funding": grab(_FUNDING, int),
            "brain_extracted": 0,
            "brain_indexed": 0,
        },
        "backfilled": True,
    }


def backfill() -> int:
    """Seed history.jsonl from the .log files already on disk.

    Without this the report is empty until 30 nights have passed, which is exactly when you
    stop caring. Existing rows are preserved and matched on `run`, so this is safe to re-run.
    """
    existing = set()
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line)["run"])
            except (json.JSONDecodeError, KeyError):
                continue

    rows = [r for r in (parse_log(p) for p in sorted(LOG_DIR.glob("*.log"))) if r]
    fresh = [r for r in rows if r["run"] not in existing]
    if fresh:
        with HISTORY.open("a", encoding="utf-8") as fh:
            for row in sorted(fresh, key=lambda r: r["run"]):
                fh.write(json.dumps(row) + "\n")
    return len(fresh)


def load(last: int | None = None) -> list[dict]:
    if not HISTORY.exists():
        return []
    rows = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.sort(key=lambda r: r.get("run", ""))
    return rows[-last:] if last else rows


def _fmt_secs(s: float) -> str:
    return f"{s:.0f}s" if s < 90 else f"{s / 60:.1f}m"


def report(rows: list[dict]) -> str:
    if not rows:
        return ("No history yet. Seed it from the logs already on disk:\n"
                "  uv run python scripts/nightly_report.py --backfill")

    out: list[str] = []
    out.append(f"═══ {len(rows)} runs · {rows[0]['run'][:10]} → {rows[-1]['run'][:10]} ═══\n")

    out.append("RUNS")
    out.append(f"  {'date':12} {'exit':>4}  {'wall':>7}  {'xAI':>7}  {'claude':>7}  "
               f"{'cands':>5}  steps")
    for r in rows[-12:]:
        bad = [s["name"] for s in r.get("steps", []) if s["status"] in ("fail", "warn")]
        note = ("  " + ", ".join(bad)) if bad else ""
        out.append(
            f"  {r['run'][:10]:12} {r.get('exit', '?'):>4}  "
            f"{_fmt_secs(r.get('duration_s', 0)):>7}  "
            f"${r.get('cost', {}).get('xai', 0):>6.2f}  "
            f"${r.get('cost', {}).get('claude', 0):>6.2f}  "
            f"{r.get('output', {}).get('candidates', 0):>5}"
            f"{note}")

    # Per-step timing. Sorted by median descending — the top row is where the wall clock goes,
    # which is the question "what could we tighten" reduces to.
    by_step: dict[str, list[int]] = {}
    fails: dict[str, int] = {}
    for r in rows:
        for s in r.get("steps", []):
            by_step.setdefault(s["name"], []).append(s["seconds"])
            if s["status"] in ("fail", "warn"):
                fails[s["name"]] = fails.get(s["name"], 0) + 1

    out.append("\nSTEP TIMINGS  (median — mean is useless here, see module docstring)")
    out.append(f"  {'step':16} {'n':>3}  {'median':>7}  {'max':>8}  {'fails':>5}")
    for name, secs in sorted(by_step.items(), key=lambda kv: -statistics.median(kv[1])):
        out.append(f"  {name:16} {len(secs):>3}  {_fmt_secs(statistics.median(secs)):>7}  "
                   f"{_fmt_secs(max(secs)):>8}  {fails.get(name, 0):>5}")

    total = sum(statistics.median(s) for s in by_step.values())
    out.append(f"  {'─' * 46}")
    out.append(f"  {'typical night':16} {'':>3}  {_fmt_secs(total):>7}")

    # Reliability, stated as a count rather than a rate: with a sample this small a percentage
    # implies a precision the data does not have.
    clean = sum(1 for r in rows if r.get("exit") == 0)
    out.append(f"\nRELIABILITY   {clean}/{len(rows)} runs exited clean")
    if fails:
        out.append("  failures by step: "
                   + ", ".join(f"{k}×{v}" for k, v in sorted(fails.items(), key=lambda kv: -kv[1])))

    spend = sum(r.get("cost", {}).get("xai", 0) for r in rows)
    allowance = sum(r.get("cost", {}).get("claude", 0) for r in rows)
    out.append(f"\nSPEND         xAI ${spend:.2f} real · claude ${allowance:.2f} allowance "
               f"over {len(rows)} runs")
    out.append(f"              ~${spend / len(rows):.2f}/night real, "
               f"~${spend / len(rows) * 30:.2f}/month at this rate")
    out.append("  NOTE: xAI figures count successful nightly calls only — manual `ingest-x`\n"
               "        runs and timed-out attempts that still billed are not in here.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="nightly_report",
        description="Trends across nightly runs: what is slow, what fails, what it costs.")
    ap.add_argument("--last", type=int, default=None, help="only the last N runs")
    ap.add_argument("--backfill", action="store_true",
                    help="seed history.jsonl from existing .log files, then report")
    args = ap.parse_args()

    if args.backfill:
        n = backfill()
        print(f"backfilled {n} run(s) from {LOG_DIR.name}/\n")

    print(report(load(args.last)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
