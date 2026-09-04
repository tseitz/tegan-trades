from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.env import load_env

from brain.extract import DEFAULT_MODEL
from brain.sweep import (
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_WORKERS,
    extract_all,
    format_summary,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def extract_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain-extract",
        description="Extract narrative stances from every un-extracted transcript.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true",
                        help="re-extract transcripts that already have a stance file")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_MAX_WORKERS,
                        help="number of transcripts to extract in parallel")
    parser.add_argument("--limit", type=int, default=None,
                        help="process at most N transcripts (sampling before a full run)")
    parser.add_argument("--max-consecutive-failures", type=int,
                        default=DEFAULT_MAX_CONSECUTIVE_FAILURES,
                        help="abort the sweep after this many back-to-back failures "
                             "(a usage cap fails every remaining call instantly)")
    args = parser.parse_args(argv)
    load_env()
    results = extract_all(extracted_at=_now(), model=args.model, force=args.force,
                          max_workers=args.concurrency, limit=args.limit,
                          max_consecutive_failures=args.max_consecutive_failures)
    print(format_summary(results))
    return 0
