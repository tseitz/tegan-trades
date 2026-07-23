# Phase 1 — Ingestion Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A manual CLI batch job that reads `config/watchlist.yaml`, resolves each active YouTube channel to its recent videos (uploads **and** livestreams), and ingests transcripts + rich metadata into the ore store, skipping anything already captured.

**Architecture:** Two-stage resolve per channel. **Enumerate** (yt-dlp flat playlist on `/videos` + `/streams`, newest-first, count-capped) gives ordered video stubs. **Hydrate** (yt-dlp full extract, run only for videos not already stored) supplies reliable `published_at` / `was_live` / `channel_id`, then the 2-year age cutoff and transcript fetch run. Everything wraps the Phase 0 primitives in `store.py` and `youtube.py`. Dependency injection (callables passed into the batch loop) keeps every unit testable without network.

**Tech Stack:** Python 3.12, uv, yt-dlp (channel/video extraction), youtube-transcript-api (captions), pyyaml (watchlist), pytest (TDD). All new network-touching code is exercised through injected fakes in unit tests plus one `@integration` real-channel spike.

**Design spec:** `~/vault/Claude/Projects/tegan-trades/phase-1-spec.md` (approved 2026-07-22).

---

## Working Directory & Sandbox Notes

- All work happens in `/Users/tseitz/code/projects/tegan-trades/packages/ingestion`.
- `~/code/projects` is **not** in the sandbox write-allowlist. Every `Bash` call that writes files, runs `uv`, or touches git needs `dangerouslyDisableSandbox: true`.
- The shell cwd resets to the vault between `Bash` calls — always `cd` with an absolute path inside each command.
- Run tests with `uv run pytest` from the package dir. Unit tests must pass offline; the `@integration` test hits YouTube and is run explicitly with `-m integration`.

---

## File Structure

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/ingestion/youtube.py` | Add `ingest_video(video_id, metadata, *, text, root)`; refactor `ingest(url)` to reuse it |
| Create | `src/ingestion/channel.py` | Channel-URL normalization; flat tab enumeration; per-video hydration; age filter |
| Create | `src/ingestion/roster.py` | Read watchlist → active YouTube targets; per-channel batch loop; `ChannelResult` + run summary |
| Create | `src/ingestion/cli.py` | `ingest-roster` / `ingest-channel` entry points (argparse) |
| Modify | `pyproject.toml` | Register the two console scripts |
| Create | `tests/test_channel.py` | Unit tests for `channel.py` (yt-dlp mocked via injected callables) |
| Create | `tests/test_roster.py` | Unit tests for `roster.py` (watchlist fixture; injected fakes; `tmp_path` store) |
| Create | `tests/test_cli.py` | Unit tests for arg parsing / dispatch |
| Modify | `tests/test_youtube.py` | Add tests for `ingest_video` metadata persistence |

**Data shapes** (defined in `channel.py`, imported elsewhere — names are fixed contracts, do not rename between tasks):

```python
# channel.py
@dataclass(frozen=True)
class VideoStub:      # from STAGE 1 enumeration
    video_id: str
    title: str
    tab: str          # "videos" | "streams"

@dataclass(frozen=True)
class VideoMeta:      # from STAGE 2 hydration
    video_id: str
    title: str
    published_at: str | None   # ISO "YYYY-MM-DD", or None when yt-dlp gives no date
    duration: int | None
    channel_id: str | None
    was_live: bool
```

```python
# roster.py
@dataclass(frozen=True)
class ChannelTarget:
    person: str
    channel: str        # youtube id/handle from watchlist (e.g. "@benjaminjcowen" or "UC...")
    max_videos: int     # default 50, overridable per person
    max_age_days: int   # default 730, overridable per person

@dataclass
class ChannelResult:
    person: str
    channel: str
    ingested: list[str]           # video ids saved this run
    skipped: list[str]            # already in store
    stale: list[str]              # older than the age cutoff
    failed: list[tuple[str, str]] # (video_id, reason)
```

---

## Task 1: Metadata plumbing in `youtube.py`

Give the transcript path a way to persist rich metadata. The batch loop will call `ingest_video` with a pre-fetched metadata dict; `ingest(url)` becomes a thin wrapper so the Phase 0 behavior is preserved.

**Files:**
- Modify: `src/ingestion/youtube.py`
- Test: `tests/test_youtube.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_youtube.py`:

```python
def test_ingest_video_persists_given_text_and_metadata(tmp_path):
    from ingestion.youtube import ingest_video
    from ingestion.store import load, path_for
    import json

    meta = {"title": "T", "published_at": "2026-07-01", "person": "Someone"}
    vid = ingest_video("abc12345678", meta, text="hello", root=tmp_path)

    assert vid == "abc12345678"
    assert load("youtube", "abc12345678", root=tmp_path) == "hello"
    sidecar = json.loads(path_for("youtube", "abc12345678", root=tmp_path)
                         .with_suffix(".json").read_text())
    assert sidecar["title"] == "T"
    assert sidecar["published_at"] == "2026-07-01"
    assert sidecar["person"] == "Someone"
    assert sidecar["platform"] == "youtube"      # added by store.save
    assert sidecar["source_id"] == "abc12345678"  # added by store.save


def test_ingest_video_fetches_transcript_when_text_omitted(tmp_path, monkeypatch):
    import ingestion.youtube as yt
    from ingestion.store import load

    monkeypatch.setattr(yt, "fetch_transcript", lambda vid: f"fetched:{vid}")
    yt.ingest_video("vid00000001", {"url": "u"}, root=tmp_path)

    assert load("youtube", "vid00000001", root=tmp_path) == "fetched:vid00000001"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_youtube.py -k ingest_video -v`
Expected: FAIL with `ImportError: cannot import name 'ingest_video'`.

- [ ] **Step 3: Implement `ingest_video` and refactor `ingest`**

Replace the `ingest` function at the bottom of `src/ingestion/youtube.py` with:

```python
def ingest_video(
    video_id: str,
    metadata: dict,
    *,
    text: str | None = None,
    root: Path = DATA_ROOT,
) -> str:
    """Persist a transcript + metadata for a known video id. Fetches the
    transcript when `text` is not supplied. Returns the video id."""
    if text is None:
        text = fetch_transcript(video_id)
    save(
        TranscriptRecord(
            platform="youtube",
            source_id=video_id,
            text=text,
            metadata=metadata,
        ),
        root=root,
    )
    return video_id


def ingest(url: str, *, root: Path = DATA_ROOT) -> str:
    """Fetch a YouTube transcript from a URL and persist it. Returns the video id."""
    video_id = extract_video_id(url)
    return ingest_video(video_id, {"url": url}, root=root)
```

Add the needed imports at the top of `src/ingestion/youtube.py` (below the existing imports):

```python
from pathlib import Path

from ingestion.store import DATA_ROOT
```

(`TranscriptRecord` and `save` are already imported.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_youtube.py -v`
Expected: PASS (existing `extract_video_id` tests still pass; new `ingest_video` tests pass; the `@integration` test is deselected without `-m integration`).

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/src/ingestion/youtube.py packages/ingestion/tests/test_youtube.py && git commit -m "feat(ingestion): ingest_video persists rich metadata"
```

---

## Task 2: Channel-URL normalization (`channel.py`)

Pure functions — no network. Convert a watchlist channel identifier into tab URLs, handling `@handle`, bare `handle`, `UC...` ids, and full URLs.

**Files:**
- Create: `src/ingestion/channel.py`
- Test: `tests/test_channel.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_channel.py`:

```python
import pytest
from ingestion.channel import channel_base_url, tab_url


@pytest.mark.parametrize("channel,expected", [
    ("@benjaminjcowen", "https://www.youtube.com/@benjaminjcowen"),
    ("benjaminjcowen", "https://www.youtube.com/@benjaminjcowen"),
    ("UCYStZ8mMNGOVTj-Z4AbbSrQ", "https://www.youtube.com/channel/UCYStZ8mMNGOVTj-Z4AbbSrQ"),
    ("https://www.youtube.com/@TTrades_edu", "https://www.youtube.com/@TTrades_edu"),
    ("https://www.youtube.com/@TTrades_edu/", "https://www.youtube.com/@TTrades_edu"),
])
def test_channel_base_url(channel, expected):
    assert channel_base_url(channel) == expected


def test_tab_url_appends_tab():
    assert tab_url("@TTrades_edu", "streams") == "https://www.youtube.com/@TTrades_edu/streams"
    assert tab_url("UCYStZ8mMNGOVTj-Z4AbbSrQ", "videos") == \
        "https://www.youtube.com/channel/UCYStZ8mMNGOVTj-Z4AbbSrQ/videos"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_channel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingestion.channel'`.

- [ ] **Step 3: Implement normalization**

Create `src/ingestion/channel.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from yt_dlp import YoutubeDL


def channel_base_url(channel: str) -> str:
    """Normalize a watchlist channel identifier to its YouTube base URL.

    Accepts: '@handle', bare 'handle', 'UC...' channel id, or a full URL.
    """
    c = channel.strip().rstrip("/")
    if c.startswith("http"):
        return c
    if c.startswith("@"):
        return f"https://www.youtube.com/{c}"
    if c.startswith("UC") and len(c) == 24:
        return f"https://www.youtube.com/channel/{c}"
    return f"https://www.youtube.com/@{c}"


def tab_url(channel: str, tab: str) -> str:
    """URL for a channel's tab ('videos' | 'streams')."""
    return f"{channel_base_url(channel)}/{tab}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_channel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/src/ingestion/channel.py packages/ingestion/tests/test_channel.py && git commit -m "feat(ingestion): channel URL normalization"
```

---

## Task 3: Flat tab enumeration + `resolve_recent` (`channel.py`)

STAGE 1. List a channel's `/videos` and `/streams` tabs via yt-dlp flat extraction, merge + dedupe by id. The yt-dlp call is injected so tests run offline. A missing `/streams` tab (yt-dlp raises) is tolerated.

**Files:**
- Modify: `src/ingestion/channel.py`
- Test: `tests/test_channel.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_channel.py`:

```python
from ingestion.channel import VideoStub, list_tab, resolve_recent


def _fake_entries(mapping):
    """Return a fake flat-extract callable keyed by tab url."""
    def _entries(url, limit):
        for tab, rows in mapping.items():
            if url.endswith(f"/{tab}"):
                return rows[:limit]
        raise RuntimeError(f"no such tab: {url}")
    return _entries


def test_list_tab_builds_stubs_from_flat_entries():
    entries = _fake_entries({"videos": [
        {"id": "aaaaaaaaaaa", "title": "One", "live_status": None},
        {"id": "bbbbbbbbbbb", "title": "Two", "live_status": None},
    ]})
    stubs = list_tab("@x", "videos", 10, _entries=entries)
    assert stubs == [
        VideoStub("aaaaaaaaaaa", "One", "videos"),
        VideoStub("bbbbbbbbbbb", "Two", "videos"),
    ]


def test_list_tab_skips_entries_without_id():
    entries = _fake_entries({"videos": [
        {"id": None, "title": "bad"},
        {"id": "ccccccccccc", "title": "good"},
    ]})
    stubs = list_tab("@x", "videos", 10, _entries=entries)
    assert [s.video_id for s in stubs] == ["ccccccccccc"]


def test_resolve_recent_merges_tabs_and_dedupes():
    entries = _fake_entries({
        "videos": [{"id": "vid00000001", "title": "V1"},
                   {"id": "dup00000000", "title": "Dup"}],
        "streams": [{"id": "dup00000000", "title": "Dup"},
                    {"id": "str00000001", "title": "S1"}],
    })
    stubs = resolve_recent("@x", max_videos=10, _list_entries=entries)
    ids = [s.video_id for s in stubs]
    assert ids == ["vid00000001", "dup00000000", "str00000001"]  # videos first, dup kept once
    assert any(s.tab == "streams" and s.video_id == "str00000001" for s in stubs)


def test_resolve_recent_tolerates_missing_streams_tab():
    def entries(url, limit):
        if url.endswith("/videos"):
            return [{"id": "vid00000001", "title": "V1"}]
        raise RuntimeError("This channel has no streams tab")
    stubs = resolve_recent("@x", max_videos=10, _list_entries=entries)
    assert [s.video_id for s in stubs] == ["vid00000001"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_channel.py -k "list_tab or resolve_recent" -v`
Expected: FAIL with `ImportError: cannot import name 'VideoStub'`.

- [ ] **Step 3: Implement enumeration**

Add to `src/ingestion/channel.py`:

```python
_TABS = ("videos", "streams")


@dataclass(frozen=True)
class VideoStub:
    video_id: str
    title: str
    tab: str  # "videos" | "streams"


def _flat_entries(url: str, limit: int) -> list[dict]:
    opts = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "playlistend": limit,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("entries") or []


def list_tab(channel: str, tab: str, limit: int, *, _entries=None) -> list[VideoStub]:
    fetch = _entries or _flat_entries
    rows = fetch(tab_url(channel, tab), limit)
    stubs: list[VideoStub] = []
    for row in rows:
        vid = row.get("id")
        if not vid:
            continue
        stubs.append(VideoStub(video_id=vid, title=row.get("title") or "", tab=tab))
    return stubs


def resolve_recent(channel: str, max_videos: int, *, _list_entries=None) -> list[VideoStub]:
    """Enumerate a channel's recent uploads and livestreams (newest-first),
    merged and deduped by video id. Count cap applies per tab."""
    seen: set[str] = set()
    out: list[VideoStub] = []
    for tab in _TABS:
        try:
            stubs = list_tab(channel, tab, max_videos, _entries=_list_entries)
        except Exception:
            stubs = []  # tab may not exist (e.g. no /streams)
        for stub in stubs:
            if stub.video_id in seen:
                continue
            seen.add(stub.video_id)
            out.append(stub)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_channel.py -v`
Expected: PASS (all channel tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/src/ingestion/channel.py packages/ingestion/tests/test_channel.py && git commit -m "feat(ingestion): enumerate videos + streams tabs, merged and deduped"
```

---

## Task 4: Per-video hydration + age filter (`channel.py`)

STAGE 2. Full extract for one video → `VideoMeta` with reliable `published_at` (from `upload_date` or `timestamp`), `was_live`, `channel_id`. Plus a pure `is_recent_enough` age check with an injectable reference date.

**Files:**
- Modify: `src/ingestion/channel.py`
- Test: `tests/test_channel.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_channel.py`:

```python
from datetime import date
from ingestion.channel import VideoMeta, hydrate, is_recent_enough


def test_hydrate_maps_upload_date_to_iso_published_at():
    info = {"title": "T", "upload_date": "20260701", "duration": 1220,
            "channel_id": "UCabc", "was_live": True}
    meta = hydrate("vid00000001", _extract=lambda url: info)
    assert meta == VideoMeta("vid00000001", "T", "2026-07-01", 1220, "UCabc", True)


def test_hydrate_falls_back_to_timestamp_when_no_upload_date():
    # 2026-07-01T00:00:00Z == 1782518400
    info = {"title": "T", "timestamp": 1782518400, "was_live": False}
    meta = hydrate("vid00000002", _extract=lambda url: info)
    assert meta.published_at == "2026-07-01"
    assert meta.was_live is False


def test_hydrate_published_at_none_when_no_date_fields():
    meta = hydrate("vid00000003", _extract=lambda url: {"title": "T"})
    assert meta.published_at is None


def test_is_recent_enough_uses_cutoff():
    today = date(2026, 7, 22)
    assert is_recent_enough("2026-07-01", max_age_days=730, today=today) is True
    assert is_recent_enough("2020-01-01", max_age_days=730, today=today) is False


def test_is_recent_enough_false_for_missing_date():
    assert is_recent_enough(None, max_age_days=730, today=date(2026, 7, 22)) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_channel.py -k "hydrate or recent" -v`
Expected: FAIL with `ImportError: cannot import name 'VideoMeta'`.

- [ ] **Step 3: Implement hydration + age filter**

Add to `src/ingestion/channel.py`:

```python
from datetime import timedelta  # add to the datetime import line at the top


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    title: str
    published_at: str | None   # ISO "YYYY-MM-DD"
    duration: int | None
    channel_id: str | None
    was_live: bool


def _published_at(info: dict) -> str | None:
    upload_date = info.get("upload_date")
    if upload_date and len(upload_date) == 8:
        return f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    ts = info.get("timestamp") or info.get("release_timestamp")
    if ts:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    return None


def _full_extract(url: str) -> dict:
    with YoutubeDL({"skip_download": True, "quiet": True}) as ydl:
        return ydl.extract_info(url, download=False)


def hydrate(video_id: str, *, _extract=None) -> VideoMeta:
    """Full-extract a single video for reliable metadata."""
    extract = _extract or _full_extract
    info = extract(f"https://www.youtube.com/watch?v={video_id}")
    return VideoMeta(
        video_id=video_id,
        title=info.get("title") or "",
        published_at=_published_at(info),
        duration=info.get("duration"),
        channel_id=info.get("channel_id"),
        was_live=bool(info.get("was_live")),
    )


def is_recent_enough(published_at: str | None, max_age_days: int, *, today: date) -> bool:
    if not published_at:
        return False
    cutoff = today - timedelta(days=max_age_days)
    return date.fromisoformat(published_at) >= cutoff
```

Update the top-of-file datetime import to include `timedelta`:

```python
from datetime import date, datetime, timedelta, timezone
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_channel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/src/ingestion/channel.py packages/ingestion/tests/test_channel.py && git commit -m "feat(ingestion): per-video hydration + age filter"
```

---

## Task 5: Watchlist → active targets (`roster.py`)

Read `config/watchlist.yaml`, select people with `status: active`, and for each `channels` entry that is `platform: youtube` + `access: ok`, emit a `ChannelTarget`. Honor an optional per-person `backfill: {max_videos, max_age_days}` override.

**Files:**
- Create: `src/ingestion/roster.py`
- Test: `tests/test_roster.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_roster.py`:

```python
import textwrap
from ingestion.roster import ChannelTarget, active_targets, load_watchlist


WATCHLIST = textwrap.dedent("""
    people:
      - name: "Alice"
        status: active
        channels:
          - { platform: youtube, id: "@alice", access: ok }
          - { platform: x, id: "alice", access: grok }
      - name: "Bob"
        status: active
        backfill: { max_videos: 10, max_age_days: 365 }
        channels:
          - { platform: youtube, id: "UCbbbbbbbbbbbbbbbbbbbbbb", access: ok }
      - name: "Cara"
        status: candidate
        channels:
          - { platform: youtube, id: "@cara", access: ok }
      - name: "Dan"
        status: active
        channels:
          - { platform: youtube, id: "@danpaid", access: paid }
""")


def _write(tmp_path, text):
    p = tmp_path / "watchlist.yaml"
    p.write_text(text)
    return p


def test_active_targets_selects_active_youtube_ok_only(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    targets = active_targets(wl)
    names = [(t.person, t.channel) for t in targets]
    assert names == [("Alice", "@alice"), ("Bob", "UCbbbbbbbbbbbbbbbbbbbbbb")]
    # Cara excluded (candidate); Dan excluded (access=paid); Alice's x channel excluded


def test_active_targets_applies_defaults_and_overrides(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    by_person = {t.person: t for t in active_targets(wl)}
    assert by_person["Alice"] == ChannelTarget("Alice", "@alice", 50, 730)
    assert by_person["Bob"] == ChannelTarget("Bob", "UCbbbbbbbbbbbbbbbbbbbbbb", 10, 365)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_roster.py -k active_targets -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingestion.roster'`.

- [ ] **Step 3: Implement watchlist parsing**

Create `src/ingestion/roster.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ingestion import channel as channel_mod
from ingestion.store import DATA_ROOT
from ingestion.store import exists as _store_exists

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_roster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/src/ingestion/roster.py packages/ingestion/tests/test_roster.py && git commit -m "feat(ingestion): select active youtube targets from watchlist"
```

---

## Task 6: The batch loop — `ingest_channel` (`roster.py`)

Tie the stages together for one channel: enumerate → skip-if-stored → hydrate → age-filter → fetch transcript → save with rich metadata. Every external effect (`resolve`, `hydrate`, `fetch_transcript`, `save`) is injected so the loop is tested with zero network and a `tmp_path` store. Buckets: `ingested` / `skipped` / `stale` / `failed`.

**Files:**
- Modify: `src/ingestion/roster.py`
- Test: `tests/test_roster.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_roster.py`:

```python
from datetime import date
from ingestion.roster import ChannelResult, ChannelTarget, ingest_channel
from ingestion.channel import VideoStub, VideoMeta


def _stub(vid, tab="videos"):
    return VideoStub(vid, f"title-{vid}", tab)


def _meta(vid, published_at, was_live=False):
    return VideoMeta(vid, f"title-{vid}", published_at, 100, "UCabc", was_live)


def test_ingest_channel_happy_path_saves_rich_metadata(tmp_path):
    saved = {}
    target = ChannelTarget("Alice", "@alice", max_videos=50, max_age_days=730)

    result = ingest_channel(
        target,
        today=date(2026, 7, 22),
        resolve=lambda ch, n: [_stub("vid00000001", "streams")],
        hydrate=lambda vid: _meta(vid, "2026-07-01", was_live=True),
        fetch_transcript=lambda vid: f"text-{vid}",
        exists=lambda platform, vid: False,
        save_video=lambda vid, meta, text: saved.update({vid: (meta, text)}),
    )

    assert result.ingested == ["vid00000001"]
    assert result.skipped == [] and result.stale == [] and result.failed == []
    meta, text = saved["vid00000001"]
    assert text == "text-vid00000001"
    assert meta["person"] == "Alice"
    assert meta["published_at"] == "2026-07-01"
    assert meta["was_live"] is True
    assert meta["channel_id"] == "UCabc"
    assert meta["url"] == "https://www.youtube.com/watch?v=vid00000001"
    assert meta["title"] == "title-vid00000001"


def test_ingest_channel_skips_already_stored(tmp_path):
    target = ChannelTarget("Alice", "@alice", 50, 730)
    result = ingest_channel(
        target,
        today=date(2026, 7, 22),
        resolve=lambda ch, n: [_stub("have0000001")],
        hydrate=lambda vid: pytest.fail("should not hydrate a stored video"),
        fetch_transcript=lambda vid: "x",
        exists=lambda platform, vid: True,
        save_video=lambda *a: pytest.fail("should not save"),
    )
    assert result.skipped == ["have0000001"]
    assert result.ingested == []


def test_ingest_channel_buckets_stale_and_missing_date(tmp_path):
    target = ChannelTarget("Alice", "@alice", 50, 730)
    result = ingest_channel(
        target,
        today=date(2026, 7, 22),
        resolve=lambda ch, n: [_stub("old0000001"), _stub("nodate00001")],
        hydrate=lambda vid: _meta(vid, "2019-01-01") if vid == "old0000001"
                            else _meta(vid, None),
        fetch_transcript=lambda vid: "x",
        exists=lambda platform, vid: False,
        save_video=lambda *a: pytest.fail("neither video should be saved"),
    )
    assert result.stale == ["old0000001"]
    assert result.failed == [("nodate00001", "missing published_at")]


def test_ingest_channel_logs_transcript_failure_and_continues(tmp_path):
    saved = {}
    target = ChannelTarget("Alice", "@alice", 50, 730)

    def transcript(vid):
        if vid == "bad0000001":
            raise RuntimeError("no captions")
        return f"text-{vid}"

    result = ingest_channel(
        target,
        today=date(2026, 7, 22),
        resolve=lambda ch, n: [_stub("bad0000001"), _stub("good000001")],
        hydrate=lambda vid: _meta(vid, "2026-07-01"),
        fetch_transcript=transcript,
        exists=lambda platform, vid: False,
        save_video=lambda vid, meta, text: saved.update({vid: text}),
    )
    assert result.ingested == ["good000001"]
    assert result.failed == [("bad0000001", "transcript: no captions")]
    assert "good000001" in saved and "bad0000001" not in saved
```

Add `import pytest` to the top of `tests/test_roster.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_roster.py -k ingest_channel -v`
Expected: FAIL with `ImportError: cannot import name 'ChannelResult'`.

- [ ] **Step 3: Implement the batch loop**

Add to `src/ingestion/roster.py`:

```python
from ingestion.youtube import fetch_transcript as _fetch_transcript
from ingestion.youtube import ingest_video as _ingest_video


@dataclass
class ChannelResult:
    person: str
    channel: str
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


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
        except Exception as exc:  # noqa: BLE001 - log-and-continue per spec
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
```

(`_store_exists` was already imported at the top of `roster.py` in Task 5 — the aliased name keeps the injected `exists` parameter from shadowing it.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_roster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/src/ingestion/roster.py packages/ingestion/tests/test_roster.py && git commit -m "feat(ingestion): per-channel batch loop with ingested/skipped/stale/failed buckets"
```

---

## Task 7: Sweep the roster + run summary (`roster.py`)

`ingest_roster` runs `ingest_channel` for every active target and returns the results; `format_summary` renders a human-readable per-person report.

**Files:**
- Modify: `src/ingestion/roster.py`
- Test: `tests/test_roster.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_roster.py`:

```python
from ingestion.roster import format_summary, ingest_roster


def test_ingest_roster_runs_each_target(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    calls = []

    def fake_ingest_channel(target, **kwargs):
        calls.append(target.person)
        r = ChannelResult(target.person, target.channel)
        r.ingested.append("vid_" + target.person)
        return r

    results = ingest_roster(wl, _ingest_channel=fake_ingest_channel)
    assert calls == ["Alice", "Bob"]
    assert [r.person for r in results] == ["Alice", "Bob"]


def test_format_summary_reports_counts():
    r = ChannelResult("Alice", "@alice")
    r.ingested = ["a", "b"]
    r.skipped = ["c"]
    r.stale = ["d"]
    r.failed = [("e", "transcript: no captions")]
    text = format_summary([r])
    assert "Alice" in text
    assert "2 ingested" in text
    assert "1 skipped" in text
    assert "1 stale" in text
    assert "1 failed" in text
    assert "no captions" in text  # failure reasons surfaced
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_roster.py -k "ingest_roster or format_summary" -v`
Expected: FAIL with `ImportError: cannot import name 'ingest_roster'`.

- [ ] **Step 3: Implement the sweep + summary**

Add to `src/ingestion/roster.py`:

```python
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
        results.append(run(target, root=root, today=today))
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
```

Note: `ingest_roster` passes `root`/`today` through to `ingest_channel`; the injected `fake_ingest_channel` in the test accepts `**kwargs`, so this is compatible.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_roster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/src/ingestion/roster.py packages/ingestion/tests/test_roster.py && git commit -m "feat(ingestion): roster sweep + run summary"
```

---

## Task 8: CLI entry points (`cli.py` + `pyproject.toml`)

Two commands: `ingest-roster` (sweep all active targets) and `ingest-channel <handle-or-url>` (one channel, for testing). Keep the CLI thin — parse args, call into `roster.py`, print the summary.

**Files:**
- Create: `src/ingestion/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
from datetime import date
import ingestion.cli as cli
from ingestion.roster import ChannelResult


def test_roster_main_prints_summary(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_watchlist", lambda *a, **k: {"people": []})
    monkeypatch.setattr(
        cli, "ingest_roster",
        lambda wl, **k: [ChannelResult("Alice", "@alice")],
    )
    rc = cli.roster_main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Alice" in out


def test_channel_main_builds_target_and_reports(capsys, monkeypatch):
    captured = {}

    def fake_ingest_channel(target, **kwargs):
        captured["channel"] = target.channel
        captured["max_videos"] = target.max_videos
        r = ChannelResult(target.person, target.channel)
        r.ingested.append("vid1")
        return r

    monkeypatch.setattr(cli, "ingest_channel", fake_ingest_channel)
    rc = cli.channel_main(["@alice", "--max-videos", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["channel"] == "@alice"
    assert captured["max_videos"] == 5
    assert "1 ingested" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingestion.cli'`.

- [ ] **Step 3: Implement the CLI**

Create `src/ingestion/cli.py`:

```python
from __future__ import annotations

import argparse

from ingestion.roster import (
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_MAX_VIDEOS,
    ChannelTarget,
    format_summary,
    ingest_channel,
    ingest_roster,
    load_watchlist,
)


def roster_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest-roster",
                                     description="Ingest transcripts for all active roster channels.")
    parser.parse_args(argv)
    watchlist = load_watchlist()
    results = ingest_roster(watchlist)
    print(format_summary(results))
    return 0


def channel_main(argv: list[str] | None = None) -> int:
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
    result = ingest_channel(target)
    print(format_summary([result]))
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Register console scripts**

In `pyproject.toml`, replace the `[project.scripts]` block:

```toml
[project.scripts]
ingest-roster = "ingestion.cli:roster_main"
ingest-channel = "ingestion.cli:channel_main"
```

Then re-sync so the scripts are installed:

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv sync`
Expected: resolves and installs the `ingestion` package with the two scripts.

- [ ] **Step 6: Smoke-test the wired CLI (offline, expect a clean help exit)**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run ingest-channel --help`
Expected: argparse help text prints, exit 0.

- [ ] **Step 7: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/src/ingestion/cli.py packages/ingestion/tests/test_cli.py packages/ingestion/pyproject.toml packages/ingestion/uv.lock && git commit -m "feat(ingestion): ingest-roster / ingest-channel CLI"
```

---

## Task 9: Integration spike (real channel, both tabs, livestream present)

One `@integration` test that proves the two-stage resolve works end-to-end against a live channel: enumeration returns items, hydration yields a real `published_at`, and a livestream VOD is present. Then a real single-video ingest into a `tmp_path` store. Run explicitly — never in the default offline suite.

**Files:**
- Test: `tests/test_channel.py` (integration section)

- [ ] **Step 1: Write the integration test**

Add to `tests/test_channel.py`:

```python
@pytest.mark.integration
def test_resolve_and_hydrate_real_channel():
    from ingestion.channel import resolve_recent, hydrate

    # TTrades posts both uploads and livestreams; small cap to keep it fast.
    stubs = resolve_recent("@TTrades_edu", max_videos=5)
    assert len(stubs) > 0
    assert any(s.tab == "streams" for s in stubs), "expected at least one livestream VOD"

    meta = hydrate(stubs[0].video_id)
    assert meta.published_at is not None
    assert len(meta.published_at) == 10  # YYYY-MM-DD
    assert meta.channel_id and meta.channel_id.startswith("UC")


@pytest.mark.integration
def test_ingest_channel_real_end_to_end(tmp_path):
    from datetime import date
    from ingestion.roster import ChannelTarget, ingest_channel
    from ingestion.store import load

    target = ChannelTarget("Cowen", "@benjaminjcowen", max_videos=1, max_age_days=3650)
    result = ingest_channel(target, root=tmp_path, today=date.today())

    # Either the one video ingested, or it lacked captions/date — but never crashed.
    handled = result.ingested + result.skipped + result.stale + [v for v, _ in result.failed]
    assert len(handled) >= 1
    for vid in result.ingested:
        assert len(load("youtube", vid, root=tmp_path)) > 0
```

- [ ] **Step 2: Run the integration spike explicitly**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest tests/test_channel.py -m integration -v`
Expected: PASS (network required). If `@TTrades_edu` no longer streams, swap for another active channel from the watchlist and note it in the test comment.

- [ ] **Step 3: Confirm the offline suite is still green and fast**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run pytest -v`
Expected: all unit tests PASS; both `@integration` tests deselected.

- [ ] **Step 4: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades && git add packages/ingestion/tests/test_channel.py && git commit -m "test(ingestion): integration spike for two-stage resolve + livestream capture"
```

---

## Task 10: First real backfill run (manual, observed)

Not a code task — the payoff. Run the sweep by hand and watch the summary, per the "watch it work before trusting it" decision.

- [ ] **Step 1: Dry-run one channel first**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run ingest-channel @benjaminjcowen --person "Benjamin Cowen" --max-videos 3`
Expected: a summary line like `Benjamin Cowen (@benjaminjcowen): N ingested, ...`. Inspect one sidecar: `cat ../../data/transcripts/youtube/<vid>.json` — confirm `published_at`, `was_live`, `person`, `title` are populated.

- [ ] **Step 2: Full roster sweep**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run ingest-roster`
Expected: per-person summary for all active targets, then a `TOTAL:` line. Scan the `!` failure reasons — a channel with all-failed likely needs attention (caption-disabled, or a bad watchlist id).

- [ ] **Step 3: Confirm the ore landed and is git-ignored**

Run: `cd /Users/tseitz/code/projects/tegan-trades && ls data/transcripts/youtube | head && git status --porcelain data/`
Expected: transcript files present; `git status` shows **nothing** under `data/` (the ore stays out of git per `.gitignore`).

- [ ] **Step 4: Re-run to prove incrementality**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/ingestion && uv run ingest-roster`
Expected: the second sweep reports mostly `skipped` (already stored) and near-zero `ingested` — proving the `store.exists` incremental guard and that hydration is skipped for stored videos.

---

## Completion

After Task 9's suite is green and Task 10's manual run has filled the ore, use **superpowers:finishing-a-development-branch** to verify tests and complete the work. Update the vault memory (`tegan-trades.md`) and `_overview.md` "Current Focus" to reflect Phase 1 complete → Phase 2 (Distillation) next.

## Self-Review Notes (author)

- **Spec coverage:** YouTube-only ✓ (Task 5 filters `platform==youtube`), both tabs/livestreams ✓ (Task 3 + Task 9), min(50, 2yr) cap ✓ (count in Task 3 enumeration, age in Task 4/6), per-channel override ✓ (Task 5), rich metadata incl. `published_at`/`was_live` ✓ (Task 6), captions-only/log-and-continue ✓ (Task 6), manual CLI ✓ (Task 8), missing `published_at` = hard skip ✓ (Task 6 buckets it as failed). Not-in-scope items (distillation, scoring, cron, Whisper) are absent by construction.
- **Type consistency:** `VideoStub(video_id, title, tab)`, `VideoMeta(video_id, title, published_at, duration, channel_id, was_live)`, `ChannelTarget(person, channel, max_videos, max_age_days)`, `ChannelResult(person, channel, ingested, skipped, stale, failed)` used identically across Tasks 3–8.
- **Injection seams:** `ingest_channel` takes `resolve/hydrate/fetch_transcript/exists/save_video`; `ingest_roster` takes `_ingest_channel`; `cli` functions monkeypatched via module attrs. No unit test touches the network.
