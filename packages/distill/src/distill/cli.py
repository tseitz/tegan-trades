from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from distill.extract import DEFAULT_MODEL, extract_theses
from distill.roster import (
    TRANSCRIPTS_ROOT, distill_all, format_summary, _source_from_sidecar,
)
from distill.store import save_theses


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def roster_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="distill-roster",
        description="Distill structured theses from every un-distilled transcript.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true",
                        help="re-distill transcripts that already have a thesis file")
    args = parser.parse_args(argv)
    results = distill_all(distilled_at=_now(), model=args.model, force=args.force)
    print(format_summary(results))
    return 0


def transcript_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="distill-transcript",
        description="Distill a single transcript by video id (spot check).")
    parser.add_argument("video_id")
    parser.add_argument("--platform", default="youtube")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    sidecar_path = TRANSCRIPTS_ROOT / args.platform / f"{args.video_id}.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    text = sidecar_path.with_suffix(".txt").read_text(encoding="utf-8")
    source = _source_from_sidecar(sidecar)

    now = _now()
    theses = extract_theses(text, source, model=args.model, extracted_at=now)
    save_theses(args.platform, args.video_id, theses, model=args.model, distilled_at=now)
    print(f"{args.video_id}: {len(theses)} theses")
    return 0
