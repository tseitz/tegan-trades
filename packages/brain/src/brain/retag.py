"""Re-judge every stored stance's ``horizon`` against ``brain.prompt.HORIZON_RULES``.

Before those rules existed the extractor had no day ranges, and ``swing`` became the default for
any chart read — weekly and cycle views included (``scripts/probe_horizon_tags.py``). This reads
only the stored rationale and ``watching``, never the transcript, so it is one cheap call per
batch rather than a re-extraction.

``stance_id`` hashes neither horizon nor conviction, so a re-tag moves no id. The first tag ever
seen is kept in ``ext["horizon_v1"]`` and never overwritten, which is what makes a second run, or
a revert, safe.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from core.env import load_env
from core.thesis import Timeframe

from brain.extract import DEFAULT_MODEL
from brain.prompt import HORIZON_RULES
from brain.stance_store import DATA_ROOT

RETAG_VERSION = "2026-11-10"
BATCH_SIZE = 40
DEFAULT_WORKERS = 3
MAX_CONSECUTIVE_FAILURES = 3
HORIZONS = get_args(Timeframe)

SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "horizon": {"type": ["string", "null"]},
                },
                "required": ["n", "horizon"],
            },
        },
    },
    "required": ["tags"],
}

SYSTEM = f"""You assign a horizon to market commentators' stances. Each stance is numbered and
has an asset, a lean, the reason the speaker holds it, and sometimes what would change their
mind. Return one tag per number, in a `tags` list.

{HORIZON_RULES}

Use exactly one of scalp, swing, position, macro — or null when the stance gives nothing to
judge by. Judge each stance on its own text."""


@dataclass(frozen=True)
class Batch:
    """Stances from whole files only, so no two batches ever write the same file."""
    files: tuple[Path, ...]
    items: tuple[tuple[Path, int, dict], ...]  # (file, index in its stances list, stance dict)


def pending(root: Path = DATA_ROOT) -> list[tuple[Path, int, dict]]:
    out = []
    for path in sorted(root.glob("*/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for i, s in enumerate(doc.get("stances", [])):
            if (s.get("ext") or {}).get("horizon_retag") != RETAG_VERSION:
                out.append((path, i, s))
    return out


def batches(items, size: int = BATCH_SIZE) -> list[Batch]:
    by_file: dict[Path, list] = {}
    for item in items:
        by_file.setdefault(item[0], []).append(item)
    out, files, current = [], [], []
    for path, group in by_file.items():
        if current and len(current) + len(group) > size:
            out.append(Batch(tuple(files), tuple(current)))
            files, current = [], []
        files.append(path)
        current.extend(group)
    if current:
        out.append(Batch(tuple(files), tuple(current)))
    return out


def _flat(text: str) -> str:
    """One line per stance: a newline in model-written text could forge a numbered line."""
    return " ".join(str(text).split())


def build_user(batch: Batch) -> str:
    lines = []
    for n, (_, _, s) in enumerate(batch.items):
        watching = f" | would change mind: {_flat(s['watching'])}" if s.get("watching") else ""
        lines.append(f"{n}. {s['asset']} {s['lean']}: {_flat(s['rationale'])}{watching}")
    return "\n".join(lines)


def parse_tags(payload: dict, size: int) -> dict[int, str | None]:
    """Per-item tolerance: a bad number or value costs that stance its re-tag, not the batch."""
    out: dict[int, str | None] = {}
    for tag in payload.get("tags") or []:
        n, horizon = tag.get("n"), tag.get("horizon")
        if not isinstance(n, int) or not 0 <= n < size or n in out:
            continue
        if horizon is not None and horizon not in HORIZONS:
            continue
        out[n] = horizon
    return out


def apply(batch: Batch, tags: dict[int, str | None], *, write: bool) -> list[tuple]:
    """Write the batch's files with new tags; returns ``(old, new)`` per re-tagged stance."""
    changes = []
    updates: dict[Path, dict[int, tuple[str, str | None]]] = {}
    for n, (path, i, s) in enumerate(batch.items):
        if n in tags:
            updates.setdefault(path, {})[i] = (s["id"], tags[n])
            changes.append((s.get("horizon"), tags[n]))
    if not write:
        return changes
    for path, by_index in updates.items():
        doc = json.loads(path.read_text(encoding="utf-8"))
        for i, (stance_id, horizon) in by_index.items():
            s = doc["stances"][i]
            if s.get("id") != stance_id:
                raise RuntimeError(f"{path}: stance {i} changed since the scan — re-run")
            ext = dict(s.get("ext") or {})
            ext.setdefault("horizon_v1", s.get("horizon"))
            ext["horizon_retag"] = RETAG_VERSION
            s["ext"], s["horizon"] = ext, horizon
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return changes


def retag_batch(batch: Batch, *, client, model: str, write: bool) -> list[tuple]:
    message = client.messages.create(
        model=model, max_tokens=4096, system=SYSTEM,
        messages=[{"role": "user", "content": build_user(batch)}])
    payload = next(b.input for b in message.content if getattr(b, "type", None) == "tool_use")
    return apply(batch, parse_tags(payload, len(batch.items)), write=write)


def run(*, client, model: str = DEFAULT_MODEL, root: Path = DATA_ROOT, limit: int | None = None,
        workers: int = DEFAULT_WORKERS, write: bool = True, batch_size: int = BATCH_SIZE,
        max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES) -> tuple[Counter, int]:
    """Returns the old→new cross-tab and how many batches failed."""
    todo = batches(pending(root), batch_size)[:limit]
    tab: Counter = Counter()
    failed = consecutive = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(retag_batch, b, client=client, model=model, write=write)
                   for b in todo]
        for done, future in enumerate(as_completed(futures), 1):
            try:
                tab.update(future.result())
                consecutive = 0
            except Exception as exc:  # noqa: BLE001 - one batch failing must not lose the rest
                failed += 1
                consecutive += 1
                print(f"[retag] batch failed: {exc}", flush=True)
                if consecutive >= max_consecutive_failures:
                    print(f"[retag] CIRCUIT BREAKER: {consecutive} consecutive failures — "
                          "likely a usage cap; re-run to resume", flush=True)
                    for f in futures:
                        f.cancel()
                    break
            if done % 10 == 0:
                print(f"[retag] {done}/{len(todo)} batches", flush=True)
    return tab, failed


def format_tab(tab: Counter) -> str:
    rows = [f"  {old!s:<9} -> {new!s:<9} {n}" for (old, new), n in sorted(
        tab.items(), key=lambda kv: (-kv[1], str(kv[0])))]
    moved = sum(n for (old, new), n in tab.items() if old != new)
    return "\n".join([f"re-tagged {sum(tab.values())}, changed {moved}", *rows])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain-retag-horizon",
        description="Re-judge every stored stance's horizon from its rationale. Resumable.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None, help="process at most N batches")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--dry-run", action="store_true",
                        help="call the model and print the cross-tab, write nothing")
    args = parser.parse_args(argv)
    load_env()
    from llm.claude_code import ClaudeCodeClient
    client = ClaudeCodeClient(json_schema=SCHEMA)
    tab, failed = run(client=client, model=args.model, limit=args.limit,
                      workers=args.concurrency, write=not args.dry_run)
    print(format_tab(tab))
    if failed:
        print(f"{failed} batch(es) failed — re-run to resume")
    if not args.dry_run and args.limit is None and (left := len(pending())):
        print(f"{left} stance(s) still untagged — the model skipped or returned an invalid "
              "value for them; re-run to retry")
        return 1
    return 1 if failed else 0
