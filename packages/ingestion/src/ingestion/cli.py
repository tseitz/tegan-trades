from __future__ import annotations

import argparse

from ingestion.env import load_env
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
