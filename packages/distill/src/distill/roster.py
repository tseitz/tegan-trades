from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core.thesis import Source
from distill import store as store_mod
from distill.extract import extract_theses as _extract_theses

# Repo root: src/distill/roster.py -> ... -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
TRANSCRIPTS_ROOT = _REPO_ROOT / "data" / "transcripts"


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


def distill_all(
    *,
    root: Path | None = None,
    transcripts_root: Path | None = None,
    distilled_at: str,
    model: str = "claude-sonnet-5",
    force: bool = False,
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

    by_person: dict[str, DistillResult] = {}
    for sidecar_path in sorted(transcripts_root.glob("*/*.json")):
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        platform, vid = sidecar["platform"], sidecar["source_id"]
        source = _source_from_sidecar(sidecar)
        result = by_person.setdefault(source.person, DistillResult(person=source.person))

        if not force and exists(platform, vid):
            result.skipped.append(vid)
            continue
        text = sidecar_path.with_suffix(".txt").read_text(encoding="utf-8")
        try:
            theses = extract(text, source, model=model, extracted_at=distilled_at)
        except Exception as exc:  # noqa: BLE001 - log-and-continue per spec
            print(f"[distill] {platform}/{vid}: {exc!r}", file=sys.stderr)
            result.failed.append((vid, str(exc)))
            continue
        save_theses(platform, vid, theses)
        (result.distilled if theses else result.empty).append(vid)
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
