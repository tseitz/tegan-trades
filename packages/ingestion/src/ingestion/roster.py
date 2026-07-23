from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ingestion import channel as channel_mod
from ingestion.store import DATA_ROOT
from ingestion.store import exists as _store_exists
from ingestion.youtube import TranscriptBlocked
from ingestion.youtube import fetch_transcript as _fetch_transcript
from ingestion.youtube import ingest_video as _ingest_video

DEFAULT_MAX_VIDEOS = 50
DEFAULT_MAX_AGE_DAYS = 730

# Repo root: src/ingestion/roster.py -> ... -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_WATCHLIST = _REPO_ROOT / "config" / "watchlist.yaml"


@dataclass(frozen=True)
class ChannelTarget:
    person: str
    channel: str
    max_videos: int
    max_age_days: int


def load_watchlist(path: Path = DEFAULT_WATCHLIST) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def active_targets(watchlist: dict) -> list[ChannelTarget]:
    targets: list[ChannelTarget] = []
    for person in watchlist.get("people", []):
        if person.get("status") != "active":
            continue
        backfill = person.get("backfill") or {}
        max_videos = backfill.get("max_videos", DEFAULT_MAX_VIDEOS)
        max_age_days = backfill.get("max_age_days", DEFAULT_MAX_AGE_DAYS)
        for ch in person.get("channels", []):
            if ch.get("platform") == "youtube" and ch.get("access") == "ok":
                targets.append(ChannelTarget(
                    person=person["name"],
                    channel=ch["id"],
                    max_videos=max_videos,
                    max_age_days=max_age_days,
                ))
    return targets


@dataclass
class ChannelResult:
    person: str
    channel: str
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


class RunAborted(Exception):
    """Stop the whole ingest run early (e.g. a YouTube IP block).

    Carries the partial ChannelResult accumulated before the abort so the
    caller can still report what was ingested up to that point.
    """

    def __init__(self, result: ChannelResult):
        super().__init__("ingestion run aborted")
        self.result = result


def _default_save(root: Path):
    def save_video(video_id: str, metadata: dict, text: str) -> None:
        _ingest_video(video_id, metadata, text=text, root=root)
    return save_video


def ingest_channel(
    target: ChannelTarget,
    *,
    today: date | None = None,
    root: Path = DATA_ROOT,
    resolve=None,
    hydrate=None,
    fetch_transcript=None,
    exists=None,
    save_video=None,
) -> ChannelResult:
    today = today or date.today()
    resolve = resolve or channel_mod.resolve_recent
    hydrate = hydrate or channel_mod.hydrate
    fetch_transcript = fetch_transcript or _fetch_transcript
    exists = exists or (lambda platform, vid: _store_exists(platform, vid, root))
    save_video = save_video or _default_save(root)

    result = ChannelResult(person=target.person, channel=target.channel)
    for stub in resolve(target.channel, target.max_videos):
        vid = stub.video_id
        if exists("youtube", vid):
            result.skipped.append(vid)
            continue
        try:
            meta = hydrate(vid)
        except Exception as exc:  # noqa: BLE001 - log-and-continue per spec
            print(f"[ingest_channel] {target.channel}/{vid} metadata: {exc!r}", file=sys.stderr)
            result.failed.append((vid, f"metadata: {exc}"))
            continue
        if not meta.published_at:
            result.failed.append((vid, "missing published_at"))
            continue
        if not channel_mod.is_recent_enough(meta.published_at, target.max_age_days, today=today):
            result.stale.append(vid)
            continue
        try:
            text = fetch_transcript(vid)
        except TranscriptBlocked as exc:
            # Systemic IP block — every later fetch will fail too. Record this
            # video, then abort the run rather than hammering the blocked endpoint.
            print(
                f"[ingest_channel] {target.channel}/{vid} BLOCKED by YouTube "
                f"(IP ban on transcript endpoint) — aborting run: {exc!r}",
                file=sys.stderr,
            )
            result.failed.append((vid, f"transcript: {exc} [BLOCKED — run aborted]"))
            raise RunAborted(result) from exc
        except Exception as exc:  # noqa: BLE001 - log-and-continue per spec
            print(f"[ingest_channel] {target.channel}/{vid} transcript: {exc!r}", file=sys.stderr)
            result.failed.append((vid, f"transcript: {exc}"))
            continue
        metadata = {
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": meta.title,
            "published_at": meta.published_at,
            "duration": meta.duration,
            "channel_id": meta.channel_id,
            "person": target.person,
            "was_live": meta.was_live,
        }
        save_video(vid, metadata, text)
        result.ingested.append(vid)
    return result


def ingest_roster(
    watchlist: dict,
    *,
    root: Path = DATA_ROOT,
    today: date | None = None,
    _ingest_channel=None,
) -> list[ChannelResult]:
    run = _ingest_channel or ingest_channel
    results: list[ChannelResult] = []
    for target in active_targets(watchlist):
        try:
            results.append(run(target, root=root, today=today))
        except RunAborted as exc:
            results.append(exc.result)
            print(
                "[ingest_roster] YouTube IP block detected — stopping sweep; "
                "remaining channels not processed. Retry from a residential IP "
                "or configure a proxy.",
                file=sys.stderr,
            )
            break
    return results


def format_summary(results: list[ChannelResult]) -> str:
    lines: list[str] = []
    for r in results:
        lines.append(
            f"{r.person} ({r.channel}): "
            f"{len(r.ingested)} ingested, {len(r.skipped)} skipped, "
            f"{len(r.stale)} stale, {len(r.failed)} failed"
        )
        for vid, reason in r.failed:
            lines.append(f"    ! {vid}: {reason}")
    totals = {
        "ingested": sum(len(r.ingested) for r in results),
        "skipped": sum(len(r.skipped) for r in results),
        "stale": sum(len(r.stale) for r in results),
        "failed": sum(len(r.failed) for r in results),
    }
    lines.append(
        f"TOTAL: {totals['ingested']} ingested, {totals['skipped']} skipped, "
        f"{totals['stale']} stale, {totals['failed']} failed"
    )
    return "\n".join(lines)
