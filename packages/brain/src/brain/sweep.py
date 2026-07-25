from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from core.thesis import Source

from brain import stance_store as store_mod
from brain.extract import extract_stances as _extract_stances

# Repo root: src/brain/sweep.py -> src/brain -> src -> brain -> packages -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
TRANSCRIPTS_ROOT = _REPO_ROOT / "data" / "transcripts"

# Each unit of work is one LLM call, which blocks on network I/O (releasing the GIL)
# — threads are enough, no need for multiprocessing.
#
# 3, NOT 6 — this is load-bearing. The Phase-2 re-distill (same architecture, the
# sibling `distill` package) ran at the default 6 and 84 of 98 failures were silent
# `exit 1` with empty stderr: a concurrency/session-contention problem in this
# environment, not a rate limit and not a code bug. Dropping to 3 fixed 92 of 93
# retried failures outright. Do not "optimize" this back up to 6 without re-running
# that experiment first.
DEFAULT_MAX_WORKERS = 3

# When the Max subscription's usage cap is hit, EVERY remaining call fails instantly.
# On 2026-07-24 that turned into 466 doomed calls over 37 minutes and a summary that
# was 90% noise. 20 back-to-back failures is far more than transient flakiness (the
# worst real run was ~16% scattered) but small enough to cost seconds, not an hour.
DEFAULT_MAX_CONSECUTIVE_FAILURES = 20


@dataclass
class ExtractResult:
    person: str
    extracted: list[str] = field(default_factory=list)   # >= 1 stance
    empty: list[str] = field(default_factory=list)        # 0 stances, still processed
    skipped: list[str] = field(default_factory=list)      # already had a stance file
    # Never attempted: the circuit breaker tripped before reaching them. Tracked
    # separately from `failed` because these are RETRYABLE and cost nothing — folding
    # them into failures would overstate the damage of a cap-hit enormously.
    aborted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # (video_id, error) — one entry per DROPPED STANCE (not per video). A stance that
    # fails validation never silently vanishes; it is always recorded here alongside
    # the video that produced it and the validation error that killed it.
    dropped: list[tuple[str, str]] = field(default_factory=list)


def _source_from_sidecar(sidecar: dict) -> Source:
    return Source(
        person=sidecar.get("person", "unknown"),
        platform=sidecar["platform"],
        url=sidecar.get("url", ""),
        published_at=sidecar.get("published_at", ""),
        transcript_ref=f"{sidecar['platform']}/{sidecar['source_id']}",
    )


def _extract_one(sidecar_path: Path, *, force, model, extracted_at, extract, exists,
                 save_stances, abort=None):
    """Runs in a worker thread. Returns (person, vid, bucket, reason, dropped_errors) —
    bucket is one of 'skipped' | 'extracted' | 'empty' | 'aborted' | 'failed' (reason is
    None unless failed); dropped_errors is a list of validation-error strings, one per
    stance dropped from this video."""
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    platform, vid = sidecar["platform"], sidecar["source_id"]
    source = _source_from_sidecar(sidecar)

    if not force and exists(platform, vid):
        return source.person, vid, "skipped", None, []
    # Checked after the skip (skips are free and still worth recording) but before any
    # LLM call, so a tripped breaker costs nothing for the remaining queue.
    if abort is not None and abort.is_set():
        return source.person, vid, "aborted", None, []
    text = sidecar_path.with_suffix(".txt").read_text(encoding="utf-8")
    try:
        result = extract(text, source, model=model, extracted_at=extracted_at)
    except Exception as exc:  # noqa: BLE001 - log-and-continue per spec
        print(f"[brain] {platform}/{vid}: {exc!r}", file=sys.stderr)
        return source.person, vid, "failed", str(exc), []
    save_stances(platform, vid, result.stances)
    dropped_errors = [d.error for d in result.dropped]
    for err in dropped_errors:
        print(f"[brain] {platform}/{vid}: dropped stance: {err}", file=sys.stderr)
    bucket = "extracted" if result.stances else "empty"
    return source.person, vid, bucket, None, dropped_errors


def extract_all(
    *,
    root: Path | None = None,
    transcripts_root: Path | None = None,
    extracted_at: str,
    model: str = "claude-sonnet-5",
    force: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    limit: int | None = None,
    extract=None,
    exists=None,
    save_stances=None,
) -> list[ExtractResult]:
    # `root` (test convenience) points at a dir holding both transcripts/ and stances/.
    if root is not None:
        transcripts_root = root / "transcripts"
        stances_root = root / "stances"
    else:
        transcripts_root = transcripts_root or TRANSCRIPTS_ROOT
        stances_root = store_mod.DATA_ROOT

    extract = extract or _extract_stances
    exists = exists or (lambda platform, vid: store_mod.exists(platform, vid, stances_root))
    save_stances = save_stances or (
        lambda platform, vid, stances: store_mod.save_stances(
            platform, vid, stances, model=model, extracted_at=extracted_at, root=stances_root)
    )

    sidecar_paths = sorted(transcripts_root.glob("*/*.json"))
    if limit is not None:
        # Applied to the sorted list *before* submitting work, so a given `--limit N`
        # deterministically picks the same N transcripts on every run.
        sidecar_paths = sidecar_paths[:limit]

    # Each transcript is an independent unit of work — concurrency is safe: distinct
    # files, no shared mutable state touched inside a worker. Results are only
    # aggregated into by_person back on the main thread (via as_completed below), so
    # there's no cross-thread mutation to guard with a lock.
    by_person: dict[str, ExtractResult] = {}
    abort = threading.Event()
    # Only the main thread touches this (as_completed runs here), so no lock is needed.
    consecutive_failures = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_extract_one, p, force=force, model=model, extracted_at=extracted_at,
                       extract=extract, exists=exists, save_stances=save_stances, abort=abort)
            for p in sidecar_paths
        ]
        for future in as_completed(futures):
            person, vid, bucket, reason, dropped_errors = future.result()
            result = by_person.setdefault(person, ExtractResult(person=person))
            if bucket == "failed":
                result.failed.append((vid, reason))
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures and not abort.is_set():
                    abort.set()
                    print(f"[brain] CIRCUIT BREAKER: {consecutive_failures} consecutive "
                          f"failures — aborting the rest of the sweep. Last error: {reason}",
                          file=sys.stderr)
            else:
                getattr(result, bucket).append(vid)
                # An 'aborted' result is not evidence of health, so it must not reset
                # the counter — otherwise the breaker could un-trip itself.
                if bucket != "aborted":
                    consecutive_failures = 0
            for err in dropped_errors:
                result.dropped.append((vid, err))
    return list(by_person.values())


def format_summary(results: list[ExtractResult]) -> str:
    lines: list[str] = []
    for r in results:
        lines.append(
            f"{r.person}: {len(r.extracted)} extracted, {len(r.empty)} empty, "
            f"{len(r.skipped)} skipped, {len(r.failed)} failed, "
            f"{len(r.aborted)} aborted, {len(r.dropped)} dropped"
        )
        for vid, reason in r.failed:
            lines.append(f"    ! {vid}: {reason}")
        for vid, error in r.dropped:
            lines.append(f"    ~ {vid}: {error}")
    totals = {
        "extracted": sum(len(r.extracted) for r in results),
        "empty": sum(len(r.empty) for r in results),
        "skipped": sum(len(r.skipped) for r in results),
        "failed": sum(len(r.failed) for r in results),
        "aborted": sum(len(r.aborted) for r in results),
        "dropped": sum(len(r.dropped) for r in results),
    }
    lines.append(
        f"TOTAL: {totals['extracted']} extracted, {totals['empty']} empty, "
        f"{totals['skipped']} skipped, {totals['failed']} failed, "
        f"{totals['aborted']} aborted, {totals['dropped']} dropped"
    )
    if totals["aborted"]:
        lines.append(
            f"CIRCUIT BREAKER TRIPPED — {totals['aborted']} transcripts were never "
            "attempted. They cost nothing and are fully retryable: re-run brain-extract "
            "once the cause clears (a usage cap resets on its own)."
        )
    return "\n".join(lines)
