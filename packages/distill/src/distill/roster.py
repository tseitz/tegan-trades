from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from core.thesis import Source
from distill import store as store_mod
from distill.extract import extract_theses as _extract_theses

# Repo root: src/distill/roster.py -> ... -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
TRANSCRIPTS_ROOT = _REPO_ROOT / "data" / "transcripts"

# Each unit of work is one `claude -p` subprocess call, which blocks on network I/O
# (releasing the GIL) — threads are enough, no need for multiprocessing.
DEFAULT_MAX_WORKERS = 6


@dataclass
class DistillResult:
    person: str
    distilled: list[str] = field(default_factory=list)   # >= 1 thesis
    empty: list[str] = field(default_factory=list)       # 0 theses (still processed)
    skipped: list[str] = field(default_factory=list)     # already had a thesis file
    failed: list[tuple[str, str]] = field(default_factory=list)


def _source_from_sidecar(sidecar: dict) -> Source:
    return Source(
        person=sidecar.get("person", "unknown"),
        platform=sidecar["platform"],
        url=sidecar.get("url", ""),
        published_at=sidecar.get("published_at", ""),
        transcript_ref=f"{sidecar['platform']}/{sidecar['source_id']}",
    )


def _distill_one(sidecar_path: Path, *, force, model, distilled_at, extract, exists, save_theses):
    """Runs in a worker thread. Returns (person, vid, bucket, reason) — bucket is one
    of 'skipped' | 'distilled' | 'empty' | 'failed' (reason is None unless failed)."""
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    platform, vid = sidecar["platform"], sidecar["source_id"]
    source = _source_from_sidecar(sidecar)

    if not force and exists(platform, vid):
        return source.person, vid, "skipped", None
    text = sidecar_path.with_suffix(".txt").read_text(encoding="utf-8")
    try:
        theses = extract(text, source, model=model, extracted_at=distilled_at)
    except Exception as exc:  # noqa: BLE001 - log-and-continue per spec
        print(f"[distill] {platform}/{vid}: {exc!r}", file=sys.stderr)
        return source.person, vid, "failed", str(exc)
    save_theses(platform, vid, theses)
    return source.person, vid, ("distilled" if theses else "empty"), None


def distill_all(
    *,
    root: Path | None = None,
    transcripts_root: Path | None = None,
    distilled_at: str,
    model: str = "claude-sonnet-5",
    force: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
    extract=None,
    exists=None,
    save_theses=None,
) -> list[DistillResult]:
    # `root` (test convenience) points at a dir holding both transcripts/ and theses/.
    if root is not None:
        transcripts_root = root / "transcripts"
        theses_root = root / "theses"
    else:
        transcripts_root = transcripts_root or TRANSCRIPTS_ROOT
        theses_root = store_mod.DATA_ROOT

    extract = extract or _extract_theses
    exists = exists or (lambda platform, vid: store_mod.exists(platform, vid, theses_root))
    save_theses = save_theses or (
        lambda platform, vid, theses: store_mod.save_theses(
            platform, vid, theses, model=model, distilled_at=distilled_at, root=theses_root)
    )

    sidecar_paths = sorted(transcripts_root.glob("*/*.json"))
    # Each transcript is an independent unit of work — concurrency is safe: distinct
    # files, no shared mutable state touched inside a worker. Results are only
    # aggregated into by_person back on the main thread (via as_completed below), so
    # there's no cross-thread mutation to guard with a lock.
    by_person: dict[str, DistillResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_distill_one, p, force=force, model=model, distilled_at=distilled_at,
                       extract=extract, exists=exists, save_theses=save_theses)
            for p in sidecar_paths
        ]
        for future in as_completed(futures):
            person, vid, bucket, reason = future.result()
            result = by_person.setdefault(person, DistillResult(person=person))
            if bucket == "failed":
                result.failed.append((vid, reason))
            else:
                getattr(result, bucket).append(vid)
    return list(by_person.values())


def format_summary(results: list[DistillResult]) -> str:
    lines: list[str] = []
    for r in results:
        lines.append(
            f"{r.person}: {len(r.distilled)} distilled, {len(r.empty)} empty, "
            f"{len(r.skipped)} skipped, {len(r.failed)} failed"
        )
        for vid, reason in r.failed:
            lines.append(f"    ! {vid}: {reason}")
    totals = {
        "distilled": sum(len(r.distilled) for r in results),
        "empty": sum(len(r.empty) for r in results),
        "skipped": sum(len(r.skipped) for r in results),
        "failed": sum(len(r.failed) for r in results),
    }
    lines.append(
        f"TOTAL: {totals['distilled']} distilled, {totals['empty']} empty, "
        f"{totals['skipped']} skipped, {totals['failed']} failed"
    )
    return "\n".join(lines)
