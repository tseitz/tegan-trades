from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from core.env import load_env

from ingestion import verify, x_roster
from ingestion.roster import (
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_MAX_VIDEOS,
    ChannelTarget,
    RunAborted,
    format_summary,
    ingest_channel,
    ingest_roster,
    load_watchlist,
)
from ingestion.store import DATA_ROOT
from ingestion.x_search import (
    SearchNotRun,
    cost_usd,
    group_by_author_day,
    harvest,
    search,
)


def roster_main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="ingest-roster",
                                     description="Ingest transcripts for all active roster channels.")
    parser.parse_args(argv)
    watchlist = load_watchlist()
    results = ingest_roster(watchlist)
    print(format_summary(results))
    return 0


def channel_main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="ingest-channel",
                                     description="Ingest transcripts for a single YouTube channel.")
    parser.add_argument("channel", help="@handle, channel id (UC...), or full channel URL")
    parser.add_argument("--person", default="ad-hoc", help="label stored in metadata.person")
    parser.add_argument("--max-videos", type=int, default=DEFAULT_MAX_VIDEOS)
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    args = parser.parse_args(argv)

    target = ChannelTarget(
        person=args.person,
        channel=args.channel,
        max_videos=args.max_videos,
        max_age_days=args.max_age_days,
    )
    try:
        result = ingest_channel(target)
    except RunAborted as exc:
        result = exc.result
    print(format_summary([result]))
    return 0


# The raw responses are kept, not just the documents they produced. They are the only copy of
# the annotations, the usage counters, and the model's own narration — all three were needed to
# diagnose the silent tool-suppression failure once already, and a window cannot be replayed
# from the documents alone.
RAW_ROOT = Path(__file__).resolve().parents[4] / "data" / "raw" / "x"


def x_main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(
        prog="ingest-x",
        description="Pull X posts for the roster's digest handles via xAI x_search.")
    resume_from, truncated = x_roster.resume_window(datetime.now(UTC).date())
    parser.add_argument("--from", dest="from_date", default=resume_from,
                        help=f"window start, YYYY-MM-DD (default: resume from the last "
                             f"captured day, currently {resume_from})")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="window end, YYYY-MM-DD (default: today)")
    # Charts are ON by default. Measured: ~$0.07 per chart actually viewed, and image tokens
    # are billed per image read — so enabling it for someone who rarely charts costs nothing on
    # their text posts. It roughly doubles the bill (~$10 -> ~$25/mo) and buys the most precise
    # price levels anywhere in the system: `SOL 1h — 83.609, 81.612, 79.614, ...` off a
    # screenshot, against a corpus where §1 records that spoken levels were largely fabricated.
    parser.add_argument("--no-images", dest="images", action="store_false", default=True,
                        help="skip chart reading (~60%% cheaper, loses the best level data)")
    parser.add_argument("--dry-run", action="store_true",
                        help="search and report, but write nothing")
    args = parser.parse_args(argv)
    to_date = args.to_date or datetime.now(UTC).date().isoformat()

    if truncated and args.from_date == resume_from:
        print(f"  ! gap since the last capture exceeds "
              f"{x_roster.MAX_AUTO_LOOKBACK_DAYS} days — starting at {resume_from}. "
              f"Days before that are NOT recoverable by a later run; pass --from to widen.",
              file=sys.stderr)

    watchlist = load_watchlist()
    handles = x_roster.search_handles(watchlist)
    if not handles:
        print("no handles in watchlist.x_grok_digest — nothing to search", file=sys.stderr)
        return 1

    # Config gaps first, while they cost nothing. A handle nobody owns produces posts that
    # cannot be credited, and the run should say so before spending the call rather than
    # silently dropping them afterwards.
    for handle in x_roster.unattributable(watchlist):
        print(f"  ! {handle}: in the digest but no person declares it — posts will be dropped",
              file=sys.stderr)
    for handle in x_roster.undigested(watchlist):
        print(f"  · {handle}: declared on a person but not in the digest — not searched")

    print(f"searching {len(handles)} handles, {args.from_date}..{to_date}"
          f"{' with charts' if args.images else ''}")
    response = search(handles, args.from_date, to_date, images=args.images)

    # The raw response is written even on a dry run. A dry run withholds *corpus* writes; the
    # raw file is diagnostic, and the run most worth diagnosing is the one that just failed.
    stamp = f"{args.from_date}_{to_date}{'_img' if args.images else ''}"
    raw_path = RAW_ROOT / f"{stamp}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(response, indent=2), encoding="utf-8")

    try:
        result = harvest(response, allowed=handles)
    except SearchNotRun as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"  raw response kept at {raw_path}", file=sys.stderr)
        return 1

    orphans = x_roster.dropped_unattributable(result.posts, watchlist)
    # Printed in a fixed, greppable shape: this is the only step in the nightly cycle that
    # spends real money, and the run summary reads this line rather than trying to work the
    # figure out afterwards from files on disk.
    print(f"[ingest-x] cost: ${cost_usd(response):.4f} (xAI, real money)")
    print(f"{result.tool_calls} x_search calls -> {len(result.posts)} verified posts")
    if result.dropped:
        print("  dropped: " + ", ".join(f"{k}={v}" for k, v in result.dropped.most_common()))
    if orphans:
        print(f"  unattributable: {len(orphans)} "
              f"({', '.join(sorted({p.handle for p in orphans}))})")

    if args.dry_run:
        for (handle, day), posts in sorted(group_by_author_day(result.posts).items()):
            print(f"  {handle} {day}: {len(posts)} posts")
        return 0

    written = x_roster.store_posts(result.posts, watchlist, root=DATA_ROOT)
    print(f"wrote {len(written)} documents")
    for path in written:
        print(f"  {path.name}")
    return 0


def verify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify-roster",
        description="Check what cfg/watchlist.yaml claims against what is actually there. "
                    "Free — no API key, no proxy. Exits non-zero if anything disagrees.")
    parser.add_argument("--limit", type=int, default=5,
                        help="videos to request per tab when probing (default: 5)")
    args = parser.parse_args(argv)

    report = verify.verify_roster(load_watchlist(), limit=args.limit)
    print(report.format())
    return report.exit_code
