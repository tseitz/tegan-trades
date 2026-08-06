"""Pull X posts for the roster via xAI's ``x_search`` tool.

**xAI is a transport here, not an LLM.** It sits exactly where ``yt-dlp`` sits for YouTube and
Whisper sits for podcasts: it is the only way to *reach* the source, and its job ends at
turning what it reached into text. Every judgement — direction, conviction, timeframe, what
even counts as a thesis — stays in ``distill`` on ``packages/llm``, which remains the repo's
only LLM boundary.

That split is not stylistic. Cross-source agreement is 20% of a setup's score, so "four people
agree on ZEC" is only meaningful if one extractor judged all four; letting Grok extract X while
Claude extracts YouTube would silently mix two models' notions of "high conviction" into one
number. And because X posts get deleted and accounts go private, the verbatim capture written
here is unrecoverable ore — storing Grok's *interpretation* instead would make X the one source
that can never be re-distilled under a new schema, which is the whole living-schema bet.

**Three things measured on 2026-07-26 shape this module.** Each is a failure that returns a
plausible-looking success, so each is stated here with the defence it forced:

1. A strict ``text.format`` json_schema **silently disables the search** — 4 tool calls to 0,
   still HTTP 200, still well-formed, reading as a quiet day. So the payload is requested as
   JSON *in the prose* and parsed out here, and ``x_search_calls == 0`` is a hard error.
2. The model cannot see the tool's own ``allowed_x_handles``/dates. Its reasoning on the first
   attempt was "the message doesn't specify which accounts or the time window" and it skipped
   searching. The window is restated in the prompt.
3. Provenance is best-effort — annotation spans were real on one call and all zeros on another,
   and the model narrated searching unrestricted despite the filter being set. So both
   integrity rules are re-applied locally rather than trusted.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

XAI_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.5"

# xAI's documented ceiling for allowed_x_handles. The roster's digest list is 17.
MAX_HANDLES = 20

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)
_BARE_ARRAY = re.compile(r"(\[\s*(?:\{.*\})?\s*\])", re.DOTALL)

_REQUIRED = ("handle", "post_id", "url", "posted_at", "text")

# Asks for verbatim capture and nothing else. Deliberately contains no vocabulary from the
# thesis schema — no "direction", no "conviction" — because the moment this prompt starts
# asking for judgement, the boundary this module exists to hold has moved.
PROMPT = """\
Search X for every post published by these accounts between {from_date} and {to_date} \
inclusive:

{handles}

Return ONLY a JSON array in a ```json fenced block. One object per post:

  "handle"    the author's X handle, without the @
  "post_id"   the numeric status id
  "url"       the full permalink
  "posted_at" the post's own date, YYYY-MM-DD
  "text"      the post's text COPIED VERBATIM. Never paraphrase, translate, shorten, or \
merge two posts. If a post is a reply or quote, capture only this author's words.
  "chart"     if the post has an attached chart image, a literal transcription of what is on \
it: ticker, timeframe, and every labelled horizontal price level as a number. Describe only \
what is drawn. Do NOT say what it implies or which way it points. Empty string if no chart.

Report only posts you actually retrieved. If there are none, return []. Never fill a gap from \
memory or from what you know about these accounts.\
"""


class SearchNotRun(Exception):
    """The response is well-formed but ``x_search`` never executed.

    Its own failure mode: this arrives as HTTP 200 with a valid body and zero posts, which is
    byte-identical to a genuinely quiet day. Treating it as "no news" would let a silent
    config break look like a calm market for as long as nobody checked.
    """


@dataclass(frozen=True, slots=True)
class XPost:
    handle: str
    post_id: str
    url: str
    posted_at: str   # YYYY-MM-DD
    text: str        # verbatim
    chart: str       # transcription of an attached chart, "" when there is none


@dataclass(frozen=True)
class Harvest:
    """What a window yielded, plus what it refused and why.

    ``dropped`` is a first-class output for the same reason ``core.setups`` keeps its
    ``NotASetup`` tally: a harvest that quietly discarded most of its posts and a quiet day are
    indistinguishable unless the refusals are counted.
    """
    posts: tuple[XPost, ...]
    dropped: Counter = field(default_factory=Counter)
    tool_calls: int = 0


# ── request ─────────────────────────────────────────────────────────────────────

def build_request(handles, from_date: str, to_date: str, *, images: bool,
                  model: str = DEFAULT_MODEL) -> dict:
    """The request body. Note what is deliberately absent: no ``text.format``.

    ``tool_choice`` is left alone — "required" was measured to be echoed back and ignored, so
    setting it would only imply a guarantee we do not have.
    """
    handles = list(handles)
    if len(handles) > MAX_HANDLES:
        raise ValueError(f"x_search accepts at most {MAX_HANDLES} handles, got {len(handles)}")
    return {
        "model": model,
        # The handle list is repeated in the prompt even though the tool already carries it.
        # The model cannot see its own tool config: given "the accounts you have been given" it
        # answered "No accounts were provided in the query" and returned an empty array without
        # searching — a clean, well-formed, entirely fictional quiet day. Restating the window
        # alone was not enough; the handles have to be named too.
        "input": PROMPT.format(from_date=from_date, to_date=to_date,
                               handles=" ".join(f"@{h}" for h in handles)),
        "tools": [{
            "type": "x_search",
            "allowed_x_handles": handles,
            "from_date": from_date,
            "to_date": to_date,
            "enable_image_understanding": images,
        }],
    }


# ── reading the response ────────────────────────────────────────────────────────

# xAI reports spend in ticks of 1e-10 USD. Verified against token prices on four runs; tool
# invocations appear to sit outside it, so treat this as a floor rather than the whole bill.
USD_PER_TICK = 1e-10


def cost_usd(response: dict) -> float:
    """What this call cost in real dollars.

    Reported by the command that spends it, rather than reconstructed later by scanning
    ``data/raw/x/`` by modification time — that was tried, and it silently re-counted earlier
    runs from the same day into the nightly total.
    """
    return float((response.get("usage") or {}).get("cost_in_usd_ticks") or 0) * USD_PER_TICK


def tool_calls(response: dict) -> int:
    """How many times ``x_search`` actually ran.

    This is the only reliable signal that the search happened. ``usage.num_sources_used`` was
    measured at 0 on calls that returned real cited posts, and the documented top-level
    ``citations`` array was absent from every response — neither can be used.
    """
    details = (response.get("usage") or {}).get("server_side_tool_usage_details") or {}
    return int(details.get("x_search_calls") or 0)


def _text_blocks(response: dict):
    for item in response.get("output") or []:
        for block in item.get("content") or []:
            if block.get("type") in ("output_text", "text"):
                yield block


def output_text(response: dict) -> str:
    return "".join(block.get("text", "") for block in _text_blocks(response))


def status_id(url: str) -> str:
    """The numeric status id in an X permalink, or "" if there isn't one.

    Citations must be matched on this, never on the URL string. Measured on a real window: the
    annotations came back as ``https://x.com/i/status/<id>`` — the handle-less ``/i/`` form —
    while the extracted posts carried ``https://x.com/<handle>/status/<id>``. Both name the
    same 167 posts, so a string comparison rejected **every genuine post** while reporting a
    clean, well-formed empty day.
    """
    match = re.search(r"/status/(\d+)", url or "")
    return match.group(1) if match else ""


def cited_status_ids(response: dict) -> set[str]:
    """Every post id the response cited. Our evidence a post is real rather than recalled."""
    return {
        sid
        for block in _text_blocks(response)
        for ann in (block.get("annotations") or [])
        if (sid := status_id(ann.get("url", "")))
    }


def extract_payload(text: str) -> list:
    """Pull the JSON array out of Grok's prose.

    Needed only because structured-output mode disables the search. Prefers a fenced block and
    falls back to a bare array; anything unparseable is *no posts*, never a raise — a malformed
    payload is handled by the caller's drop tally like any other bad row.
    """
    for pattern in (_JSON_FENCE, _BARE_ARRAY):
        match = pattern.search(text)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    return []


def harvest(response: dict, *, allowed) -> Harvest:
    """Turn a raw response into verified posts, refusing anything we cannot stand behind."""
    calls = tool_calls(response)
    if calls == 0:
        raise SearchNotRun(
            "x_search never executed (x_search_calls=0) — the response is well-formed but "
            "contains no live data. Most likely a response schema was attached, which "
            "silently disables server-side tools."
        )

    cited = cited_status_ids(response)
    permitted = {h.lower() for h in allowed}
    posts: list[XPost] = []
    dropped: Counter = Counter()

    for row in extract_payload(output_text(response)):
        if not isinstance(row, dict) or any(not row.get(k) for k in _REQUIRED[:4]):
            # posted_at is inside the required slice on purpose, but an explicitly blank date
            # is reported as `undated` rather than `malformed` — a post that arrived without a
            # date is a different problem from one that arrived without a URL.
            if isinstance(row, dict) and all(row.get(k) for k in ("handle", "post_id", "url")):
                dropped["undated"] += 1
            else:
                dropped["malformed"] += 1
            continue
        if row["handle"].lower() not in permitted:
            dropped["handle_not_allowed"] += 1
            continue
        if status_id(row["url"]) not in cited:
            dropped["uncited"] += 1
            continue
        if not (row.get("text") or "").strip() and not (row.get("chart") or "").strip():
            # An image-only post read without image understanding. It carries nothing, and
            # keeping it would pad a document with blanks and still cost a distill call.
            # Measured: 79 of 167 posts in one real window were under 20 characters and
            # several were empty — this roster's chartists post charts, not captions.
            dropped["empty"] += 1
            continue
        posts.append(XPost(
            handle=row["handle"], post_id=str(row["post_id"]), url=row["url"],
            posted_at=row["posted_at"][:10], text=row.get("text", ""),
            chart=row.get("chart", "") or "",
        ))

    return Harvest(posts=tuple(posts), dropped=dropped, tool_calls=calls)


# ── grouping and rendering ──────────────────────────────────────────────────────

def group_by_author_day(posts) -> dict[tuple[str, str], list[XPost]]:
    """One document per author per day.

    ``distill.roster._source_from_sidecar`` builds exactly one ``Source`` per document, so
    every thesis in a file inherits that file's ``person``. A whole-window document would
    therefore attribute seventeen people's views to whichever name the sidecar happened to
    carry. One author per document keeps that model intact and needs no change in distill.
    """
    groups: dict[tuple[str, str], list[XPost]] = defaultdict(list)
    for post in posts:
        groups[(post.handle, post.posted_at)].append(post)
    return dict(groups)


def source_id_for(handle: str, day: str) -> str:
    return f"{handle}-{day}"


def render_document(handle: str, day: str, posts) -> str:
    """The ore: one author's posts for one day, verbatim, with permalinks.

    A chart transcription is written under its own ``CHART:`` label rather than folded into the
    post body, so the distiller can never read levels that were drawn on an image as words the
    author typed. Same reason ``core.setups`` shows stop and invalidation as two labelled
    values instead of one number.
    """
    lines = [f"@{handle} — {day}", ""]
    for post in posts:
        lines.append(post.url)
        lines.append(post.text.strip())
        if post.chart:
            lines.append(f"CHART: {post.chart.strip()}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ── the call itself (impure; injectable for tests) ──────────────────────────────

def day_windows(from_date: str, to_date: str) -> list[tuple[str, str]]:
    """Split `[from_date, to_date)` into one `(from, to)` request window per day covered.

    **`to_date` is EXCLUSIVE at the xAI tool, whatever the prompt says.** Measured across
    all 13 historical raw responses in `data/raw/x/`, without exception: a request for
    `2026-08-02 .. 2026-08-04` returned posts dated 08-02 and 08-03 and never 08-04;
    `2026-07-31 .. 2026-08-03` returned 07-31, 08-01, 08-02. The PROMPT asks for the range
    "inclusive" and that wording is left alone because every successful capture in the
    corpus was made under it — but it describes what the model is asked for, not what the
    tool serves, and the tool is what binds. A one-day window is therefore `(d, d+1)`, not
    `(d, d)`. `(d, d)` is an EMPTY range: it returns zero posts, for every handle, looking
    exactly like a quiet day.

    Consequences worth knowing, both pre-existing rather than introduced here: the caller
    passing `to_date = today` captures only up to yesterday, and re-requesting the last
    captured day (which `x_roster.resume_window` does deliberately) re-fetches a day that
    may have been partial when it was first seen.

    Splitting at all is what makes a slow window survivable. The whole range used to go up
    as one call, so its duration scaled with the ground it covered and a failure lost all of
    it — the 2026-08-05 run asked for three days, spent 301s against a 300s timeout, and
    kept nothing. Because `x_roster.store_posts` writes a day wholesale and `resume_window`
    reads the last day actually captured, a day that fails now costs only that day.
    """
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    if end <= start:
        return []
    return [
        ((start + timedelta(days=i)).isoformat(), (start + timedelta(days=i + 1)).isoformat())
        for i in range((end - start).days)
    ]


# **A hang guard, NOT a cost or latency control.** It is worth being explicit about this,
# because the natural reading is backwards: a timeout does not save money. The search has
# almost certainly completed and billed server-side by the time the client gives up — all
# timing out does is throw the answer away and under-report the spend, since the cost line is
# parsed from a response that never arrived.
#
# So the only thing this defends against is a socket that never closes wedging the nightly
# cycle, which runs unattended and would otherwise never reach fetch-prices or setups. It is
# therefore set far above any plausible real answer rather than near it: measured across eight
# nightly runs, 63/164/195/201/236/240/301s for a 2-3 day window, and the old 300s default sat
# *inside* that spread. The real fix for slowness is `day_windows` — a smaller unit of work —
# not a tighter clock.
#
# Split connect/read: failing to reach the host at all is a different thing from a call
# thinking, and should not take fifteen minutes to notice.
DEFAULT_CONNECT_TIMEOUT = 15
DEFAULT_TIMEOUT = 900

# Total attempts, not retries-after-the-first.
DEFAULT_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 20


def _retryable():
    """Transport failures worth repeating — the response never arrived, so nothing is known.

    Deliberately narrow. An HTTP error is NOT in here: a 4xx will answer the same way next
    time, and repeating it just bills again for the same rejection.
    """
    import requests
    return (requests.exceptions.Timeout, requests.exceptions.ConnectionError)


def search(handles, from_date: str, to_date: str, *, images: bool = False,
           model: str = DEFAULT_MODEL, api_key: str | None = None,
           timeout: int = DEFAULT_TIMEOUT, attempts: int = DEFAULT_ATTEMPTS,
           _post=None, _sleep=None) -> dict:
    """POST one window to xAI and return the raw response.

    The raw dict is returned rather than a parsed result so the caller can persist it: it is
    the only copy of the annotations, the usage counters, and the model's own narration, and
    all three have already proved necessary to diagnose a silent failure once.

    **A timed-out attempt is retried, and that is a considered trade rather than a free
    win.** This is the only call in the repo that spends real money, and a read timeout does
    not mean the request failed server-side — the work may well have completed and billed
    with only the response lost, so a retry can pay twice for one window. The alternative is
    worse: `ingest-x` resumes from the last capture but only within
    `x_roster.MAX_AUTO_LOOKBACK_DAYS`, so consecutive failures silently drop days that no
    later run can recover. Paying twice is bounded and visible (the nightly's own monthly cap
    gates the step); losing a day of the roster's posts is neither.
    """
    key = api_key or os.environ.get("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY is not set; add it to .env (see .env.example)")

    body = build_request(handles, from_date, to_date, images=images, model=model)
    if _post is None:  # pragma: no cover - network
        import requests
        _post = requests.post
    if _sleep is None:  # pragma: no cover - timing
        _sleep = time.sleep

    for attempt in range(1, attempts + 1):
        try:
            resp = _post(XAI_URL, headers={"Authorization": f"Bearer {key}",
                                           "Content-Type": "application/json"},
                         json=body, timeout=(DEFAULT_CONNECT_TIMEOUT, timeout))
        except _retryable() as exc:
            if attempt == attempts:
                raise
            # Printed, not swallowed: a run that quietly succeeded on its third try still
            # spent up to three times over, and the log is where that becomes visible.
            print(f"  ! xAI attempt {attempt}/{attempts} failed ({type(exc).__name__}); "
                  f"retrying in {RETRY_BACKOFF_SECONDS}s", file=sys.stderr)
            _sleep(RETRY_BACKOFF_SECONDS)
            continue

        if resp.status_code != 200:
            raise RuntimeError(f"xAI returned HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    raise AssertionError("unreachable: the loop either returns or raises")
