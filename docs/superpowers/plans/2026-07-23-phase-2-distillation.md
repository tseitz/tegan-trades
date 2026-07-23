# Phase 2 — Distillation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 620 raw transcripts into a firehose of structured, gradeable calls (JSON) via a single-pass Sonnet 5 extraction, skipping anything already distilled.

**Architecture:** New `packages/core` holds the Pydantic thesis schema (the shared contract); new `packages/distill` holds the LLM pipeline (`store` / `prompt` / `extract` / `roster` / `cli`), mirroring `packages/ingestion`'s conventions. A transcript yields 0..N theses tagged `trade` or `macro_lean`; the store is idempotent (skip-if-exists, empty-array = processed marker).

**Tech Stack:** Python 3.12 + uv, Pydantic v2 (schema + validation), Anthropic SDK (forced tool-use structured output), pytest (unit + `@integration`).

**Design spec:** `~/vault/Claude/Projects/tegan-trades/phase-2-spec.md`

---

## Before you start

- **Work on `main`. Do NOT create a worktree** (repo CLAUDE.md rule overrides skill defaults). Verify: `cd /Users/tseitz/code/projects/tegan-trades && git worktree list` shows only the main checkout.
- Writing under `~/code/projects` requires `dangerouslyDisableSandbox: true` on Bash calls; shell cwd resets each call, so use absolute `cd`.
- `ANTHROPIC_API_KEY` must be set in the environment for the integration test and real runs (unit tests mock the client).

---

## Task 1: Scaffold `packages/core` + LLM-facing extracted schema

The keystone invariant: a `trade` thesis is invalid without an `invalidation` and at least one `key_level`; a `macro_lean` needs neither. Modeled as a Pydantic discriminated union on `thesis_type`.

**Files:**
- Create: `packages/core/pyproject.toml`
- Create: `packages/core/src/core/__init__.py`
- Create: `packages/core/src/core/thesis.py`
- Test: `packages/core/tests/test_extracted.py`

- [ ] **Step 1: Scaffold the package**

Run (from repo root):
```bash
mkdir -p packages/core/src/core packages/core/tests
touch packages/core/src/core/__init__.py packages/core/tests/__init__.py
```

Create `packages/core/pyproject.toml`:
```toml
[project]
name = "core"
version = "0.1.0"
description = "Shared thesis schema — the contract every downstream phase imports."
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2",
]

[build-system]
requires = ["uv_build>=0.10.4,<0.11.0"]
build-backend = "uv_build"

[dependency-groups]
dev = [
    "pytest>=9.1.1",
]

[tool.pytest.ini_options]
markers = ["integration: hits the network; run explicitly"]
```

Create `packages/core/README.md`:
```markdown
# core

Shared thesis schema (Pydantic) for tegan-trades. The contract every downstream phase imports.
```

- [ ] **Step 2: Write the failing test**

Create `packages/core/tests/test_extracted.py`:
```python
import pytest
from pydantic import ValidationError

from core.thesis import ThesisExtraction, TradeThesis, MacroLeanThesis

TRADE = {
    "thesis_type": "trade", "domain": "crypto", "asset": "ETH",
    "direction": "long", "timeframe": "swing", "conviction": "high",
    "summary": "Long ETH off 2400 support", "invalidation": "close below 2200",
    "key_levels": [2400, 3000], "confidence": 0.8,
}
LEAN = {
    "thesis_type": "macro_lean", "domain": "crypto", "asset": "BTC",
    "direction": "long", "timeframe": "macro", "conviction": "med",
    "summary": "Bullish BTC into year-end", "confidence": 0.6,
}


def test_trade_and_lean_parse_via_discriminator():
    ex = ThesisExtraction.model_validate({"theses": [TRADE, LEAN]})
    assert isinstance(ex.theses[0], TradeThesis)
    assert isinstance(ex.theses[1], MacroLeanThesis)
    assert ex.theses[0].key_levels == [2400, 3000]
    assert ex.theses[1].invalidation is None


def test_trade_requires_invalidation():
    bad = {**TRADE}
    del bad["invalidation"]
    with pytest.raises(ValidationError):
        ThesisExtraction.model_validate({"theses": [bad]})


def test_trade_requires_nonempty_key_levels():
    with pytest.raises(ValidationError):
        ThesisExtraction.model_validate({"theses": [{**TRADE, "key_levels": []}]})


def test_lean_without_levels_is_valid():
    ex = ThesisExtraction.model_validate({"theses": [LEAN]})
    assert ex.theses[0].key_levels == []


def test_empty_extraction_is_valid():
    assert ThesisExtraction.model_validate({"theses": []}).theses == []


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        ThesisExtraction.model_validate({"theses": [{**LEAN, "confidence": 1.5}]})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/core && uv run pytest -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'core.thesis'`)

- [ ] **Step 4: Write minimal implementation**

Create `packages/core/src/core/thesis.py`:
```python
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

Domain = Literal["crypto", "stock", "macro"]
Direction = Literal["long", "short", "neutral"]
Timeframe = Literal["scalp", "swing", "position", "macro"]
Conviction = Literal["low", "med", "high"]


class Quote(BaseModel):
    text: str
    timestamp: str | None = None  # v1: always None — stored transcripts are flat text


class _Extracted(BaseModel):
    """Content fields the LLM produces for a single call (pre-enrichment)."""
    domain: Domain
    asset: str
    direction: Direction
    timeframe: Timeframe
    conviction: Conviction
    summary: str
    catalyst: str | None = None
    quotes: list[Quote] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class TradeThesis(_Extracted):
    thesis_type: Literal["trade"] = "trade"
    invalidation: str                                   # required — ICT "know your exit"
    key_levels: list[float] = Field(min_length=1)       # required, non-empty


class MacroLeanThesis(_Extracted):
    thesis_type: Literal["macro_lean"] = "macro_lean"
    invalidation: str | None = None
    key_levels: list[float] = Field(default_factory=list)


# Discriminated union: thesis_type selects the model, so trade-only invariants
# are enforced by the type system, not runtime branching.
ExtractedThesis = Annotated[
    Union[TradeThesis, MacroLeanThesis],
    Field(discriminator="thesis_type"),
]


class ThesisExtraction(BaseModel):
    """The tool-call payload the LLM returns for one transcript."""
    theses: list[ExtractedThesis] = Field(default_factory=list)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/core && uv run pytest -q`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades
git add packages/core
git commit -m "feat(core): thesis extraction schema (trade/macro_lean discriminated union)"
```

---

## Task 2: Stored `Thesis` record + `build_thesis` enrichment

The LLM produces content (`_Extracted`); we enrich it into the stored record with `id`, `source`, `extraction` (model + confidence + timestamp), `schema_version`, `status`, `ext`.

**Files:**
- Modify: `packages/core/src/core/thesis.py`
- Test: `packages/core/tests/test_thesis.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_thesis.py`:
```python
from core.thesis import (
    MacroLeanThesis, TradeThesis, Source, Thesis, build_thesis, SCHEMA_VERSION,
)

SOURCE = Source(
    person="Benjamin Cowen", platform="youtube",
    url="https://www.youtube.com/watch?v=abc12345678",
    published_at="2025-02-28", transcript_ref="youtube/abc12345678",
)


def test_build_thesis_enriches_trade():
    extracted = TradeThesis(
        domain="crypto", asset="ETH", direction="long", timeframe="swing",
        conviction="high", summary="Long ETH", invalidation="below 2200",
        key_levels=[2400.0], confidence=0.8,
    )
    t = build_thesis(extracted, source=SOURCE, model="claude-sonnet-5",
                     extracted_at="2026-07-23T00:00:00+00:00", index=0)
    assert isinstance(t, Thesis)
    assert t.id == "youtube/abc12345678#0"
    assert t.schema_version == SCHEMA_VERSION
    assert t.thesis_type == "trade"
    assert t.invalidation == "below 2200"
    assert t.key_levels == [2400.0]
    assert t.source == SOURCE
    assert t.extraction.model == "claude-sonnet-5"
    assert t.extraction.confidence == 0.8
    assert t.extraction.extracted_at == "2026-07-23T00:00:00+00:00"
    assert t.status == "raw"
    assert t.ext == {}


def test_build_thesis_enriches_lean():
    extracted = MacroLeanThesis(
        domain="crypto", asset="BTC", direction="long", timeframe="macro",
        conviction="med", summary="Bullish BTC", confidence=0.6,
    )
    t = build_thesis(extracted, source=SOURCE, model="claude-sonnet-5",
                     extracted_at="2026-07-23T00:00:00+00:00", index=3)
    assert t.id == "youtube/abc12345678#3"
    assert t.thesis_type == "macro_lean"
    assert t.invalidation is None
    assert t.key_levels == []


def test_thesis_round_trips_through_json():
    extracted = MacroLeanThesis(
        domain="macro", asset="DXY", direction="short", timeframe="position",
        conviction="low", summary="DXY topping", confidence=0.4,
    )
    t = build_thesis(extracted, source=SOURCE, model="m",
                     extracted_at="2026-07-23T00:00:00+00:00", index=1)
    restored = Thesis.model_validate_json(t.model_dump_json())
    assert restored == t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/core && uv run pytest tests/test_thesis.py -q`
Expected: FAIL (`ImportError: cannot import name 'Source'`)

- [ ] **Step 3: Write minimal implementation**

Append to `packages/core/src/core/thesis.py`:
```python
class Source(BaseModel):
    person: str
    platform: str
    url: str
    published_at: str
    transcript_ref: str


class Extraction(BaseModel):
    model: str
    confidence: float
    extracted_at: str


class Thesis(BaseModel):
    id: str
    schema_version: str = SCHEMA_VERSION
    thesis_type: Literal["trade", "macro_lean"]
    domain: Domain
    asset: str
    direction: Direction
    timeframe: Timeframe
    conviction: Conviction
    summary: str
    catalyst: str | None = None
    invalidation: str | None = None
    key_levels: list[float] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    source: Source
    extraction: Extraction
    status: Literal["raw", "reviewed", "promoted", "acted", "archived"] = "raw"
    ext: dict = Field(default_factory=dict)


def build_thesis(
    extracted: ExtractedThesis,
    *,
    source: Source,
    model: str,
    extracted_at: str,
    index: int,
) -> Thesis:
    """Enrich an LLM-extracted call into a stored Thesis record."""
    return Thesis(
        id=f"{source.transcript_ref}#{index}",
        thesis_type=extracted.thesis_type,
        domain=extracted.domain,
        asset=extracted.asset,
        direction=extracted.direction,
        timeframe=extracted.timeframe,
        conviction=extracted.conviction,
        summary=extracted.summary,
        catalyst=extracted.catalyst,
        invalidation=getattr(extracted, "invalidation", None),
        key_levels=getattr(extracted, "key_levels", []),
        quotes=extracted.quotes,
        source=source,
        extraction=Extraction(
            model=model, confidence=extracted.confidence, extracted_at=extracted_at,
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/core && uv run pytest -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades
git add packages/core
git commit -m "feat(core): stored Thesis record + build_thesis enrichment"
```

---

## Task 3: Scaffold `packages/distill` + idempotent thesis store

Mirrors `ingestion/store.py`. Theses live at `data/theses/<platform>/<source_id>.json` (gitignored under `data/`).

**Files:**
- Create: `packages/distill/pyproject.toml`, `packages/distill/README.md`
- Create: `packages/distill/src/distill/__init__.py`
- Create: `packages/distill/src/distill/store.py`
- Test: `packages/distill/tests/__init__.py`, `packages/distill/tests/test_store.py`

- [ ] **Step 1: Scaffold the package**

Run (from repo root):
```bash
mkdir -p packages/distill/src/distill packages/distill/tests
touch packages/distill/src/distill/__init__.py packages/distill/tests/__init__.py
```

Create `packages/distill/pyproject.toml`:
```toml
[project]
name = "distill"
version = "0.1.0"
description = "LLM distillation: raw transcripts -> structured theses."
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "core",
    "anthropic",
    "pydantic>=2",
]

[project.scripts]
distill-roster = "distill.cli:roster_main"
distill-transcript = "distill.cli:transcript_main"

[build-system]
requires = ["uv_build>=0.10.4,<0.11.0"]
build-backend = "uv_build"

[tool.uv.sources]
core = { path = "../core", editable = true }

[dependency-groups]
dev = [
    "pytest>=9.1.1",
]

[tool.pytest.ini_options]
markers = ["integration: hits the network; run explicitly"]
```

Create `packages/distill/README.md`:
```markdown
# distill

Single-pass Sonnet 5 distillation of raw transcripts into structured theses.
CLIs: `distill-roster` (sweep), `distill-transcript <video_id>` (one-off).
Requires `ANTHROPIC_API_KEY`.
```

Run to resolve the path dependency:
```bash
cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv sync
```
Expected: resolves `core` (editable) + `anthropic` + `pydantic`.

- [ ] **Step 2: Write the failing test**

Create `packages/distill/tests/test_store.py`:
```python
from core.thesis import Extraction, Source, Thesis
from distill.store import exists, load_theses, save_theses, thesis_path


def _thesis(i: int) -> Thesis:
    src = Source(person="P", platform="youtube", url="u",
                 published_at="2025-01-01", transcript_ref="youtube/vid00000001")
    return Thesis(
        id=f"youtube/vid00000001#{i}", thesis_type="macro_lean", domain="crypto",
        asset="BTC", direction="long", timeframe="macro", conviction="med",
        summary="s", source=src,
        extraction=Extraction(model="m", confidence=0.5, extracted_at="t"),
    )


def test_save_and_load_round_trip(tmp_path):
    save_theses("youtube", "vid00000001", [_thesis(0), _thesis(1)],
                model="claude-sonnet-5", distilled_at="2026-07-23T00:00:00+00:00",
                root=tmp_path)
    assert exists("youtube", "vid00000001", root=tmp_path)
    loaded = load_theses("youtube", "vid00000001", root=tmp_path)
    assert [t.id for t in loaded] == ["youtube/vid00000001#0", "youtube/vid00000001#1"]


def test_empty_array_still_marks_processed(tmp_path):
    save_theses("youtube", "empty0000001", [], model="m",
                distilled_at="t", root=tmp_path)
    assert exists("youtube", "empty0000001", root=tmp_path)  # processed marker
    assert load_theses("youtube", "empty0000001", root=tmp_path) == []


def test_exists_false_when_absent(tmp_path):
    assert not exists("youtube", "nope00000001", root=tmp_path)


def test_file_shape_has_metadata(tmp_path):
    import json
    save_theses("youtube", "vid00000001", [_thesis(0)], model="claude-sonnet-5",
                distilled_at="2026-07-23T00:00:00+00:00", root=tmp_path)
    doc = json.loads(thesis_path("youtube", "vid00000001", root=tmp_path).read_text())
    assert doc["transcript_ref"] == "youtube/vid00000001"
    assert doc["model"] == "claude-sonnet-5"
    assert doc["distilled_at"] == "2026-07-23T00:00:00+00:00"
    assert doc["schema_version"] == "1"
    assert len(doc["theses"]) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'distill.store'`)

- [ ] **Step 4: Write minimal implementation**

Create `packages/distill/src/distill/store.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

from core.thesis import SCHEMA_VERSION, Thesis

# Repo root: src/distill/store.py -> src/distill -> src -> distill -> packages -> <root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "theses"


def thesis_path(platform: str, source_id: str, root: Path = DATA_ROOT) -> Path:
    return root / platform / f"{source_id}.json"


def exists(platform: str, source_id: str, root: Path = DATA_ROOT) -> bool:
    return thesis_path(platform, source_id, root).exists()


def save_theses(
    platform: str,
    source_id: str,
    theses: list[Thesis],
    *,
    model: str,
    distilled_at: str,
    root: Path = DATA_ROOT,
) -> Path:
    path = thesis_path(platform, source_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "transcript_ref": f"{platform}/{source_id}",
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "distilled_at": distilled_at,
        "theses": [t.model_dump() for t in theses],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def load_theses(platform: str, source_id: str, root: Path = DATA_ROOT) -> list[Thesis]:
    doc = json.loads(thesis_path(platform, source_id, root).read_text(encoding="utf-8"))
    return [Thesis.model_validate(t) for t in doc["theses"]]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades
git add packages/distill
git commit -m "feat(distill): scaffold package + idempotent thesis store"
```

---

## Task 4: Prompt assembly

Pure function: `(transcript_text, source) -> (system_prompt, user_prompt)`. No LLM call, trivially testable.

**Files:**
- Create: `packages/distill/src/distill/prompt.py`
- Test: `packages/distill/tests/test_prompt.py`

- [ ] **Step 1: Write the failing test**

Create `packages/distill/tests/test_prompt.py`:
```python
from core.thesis import Source
from distill.prompt import build_prompt

SOURCE = Source(person="Benjamin Cowen", platform="youtube", url="u",
                published_at="2025-02-28", transcript_ref="youtube/abc")


def test_prompt_carries_context_and_rules():
    system, user = build_prompt("ETH looks strong above 2400.", SOURCE)
    # trade vs macro_lean distinction is explained
    assert "trade" in system and "macro_lean" in system
    # do-not-invent guardrail
    assert "empty" in system.lower()
    # per-transcript context threaded in
    assert "Benjamin Cowen" in user
    assert "2025-02-28" in user
    # the transcript body is included
    assert "ETH looks strong above 2400." in user


def test_prompt_is_deterministic():
    assert build_prompt("x", SOURCE) == build_prompt("x", SOURCE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest tests/test_prompt.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'distill.prompt'`)

- [ ] **Step 3: Write minimal implementation**

Create `packages/distill/src/distill/prompt.py`:
```python
from __future__ import annotations

from core.thesis import Source

_SYSTEM = """You extract trading calls from a market analyst's transcript.

Return ONLY genuine, gradeable calls via the extract_theses tool. Two kinds:

- "trade": a specific, level-based call. REQUIRES an `invalidation` (what proves
  it wrong) and at least one `key_level`. Example: "Long ETH off 2400, target
  3000, wrong below 2200."
- "macro_lean": a directional view without precise levels — still gradeable by
  direction over time. Example: "I'm bullish BTC into year-end." No levels needed.

Rules:
- Extract the analyst's OWN calls, not markets/prices they merely mention.
- `conviction` reflects how strongly/hedged they said it (low | med | high).
- `confidence` is YOUR certainty (0-1) that this is a real, correctly-parsed call.
- `quotes` must be verbatim snippets from the transcript (no timestamps).
- If the transcript contains no genuine calls, return an EMPTY list. Do NOT invent."""


def build_prompt(transcript_text: str, source: Source) -> tuple[str, str]:
    user = (
        f"Analyst: {source.person}\n"
        f"Published: {source.published_at}\n"
        f"URL: {source.url}\n\n"
        f"Transcript:\n{transcript_text}"
    )
    return _SYSTEM, user
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest tests/test_prompt.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades
git add packages/distill
git commit -m "feat(distill): extraction prompt assembly"
```

---

## Task 5: Extraction — forced tool-use + validate + retry

Single Sonnet 5 call with the `ThesisExtraction` schema as a forced tool. Parse the tool_use block → validate → retry once on validation/parse failure → on second failure raise. The Anthropic client is injected for testability.

**Files:**
- Create: `packages/distill/src/distill/extract.py`
- Test: `packages/distill/tests/test_extract.py`

- [ ] **Step 1: Write the failing test**

Create `packages/distill/tests/test_extract.py`:
```python
import pytest
from core.thesis import Source, Thesis
from distill.extract import extract_theses, ExtractionFailed

SOURCE = Source(person="P", platform="youtube", url="u",
                published_at="2025-01-01", transcript_ref="youtube/vid00000001")

VALID_INPUT = {"theses": [{
    "thesis_type": "macro_lean", "domain": "crypto", "asset": "BTC",
    "direction": "long", "timeframe": "macro", "conviction": "med",
    "summary": "Bullish BTC", "confidence": 0.6,
}]}
INVALID_INPUT = {"theses": [{"thesis_type": "trade", "domain": "crypto",
    "asset": "ETH", "direction": "long", "timeframe": "swing",
    "conviction": "high", "summary": "no invalidation", "confidence": 0.9}]}  # trade w/o invalidation


class _ToolBlock:
    type = "tool_use"
    def __init__(self, data): self.input = data


class _Msg:
    def __init__(self, data): self.content = [_ToolBlock(data)]


class _FakeClient:
    """Returns queued tool inputs, one per .messages.create call."""
    def __init__(self, *inputs):
        self._queue = list(inputs)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        return _Msg(self._queue.pop(0))


def test_extract_returns_enriched_theses():
    client = _FakeClient(VALID_INPUT)
    out = extract_theses("body", SOURCE, client=client,
                         extracted_at="2026-07-23T00:00:00+00:00")
    assert len(out) == 1
    assert isinstance(out[0], Thesis)
    assert out[0].asset == "BTC"
    assert out[0].id == "youtube/vid00000001#0"
    assert out[0].extraction.model  # model stamped


def test_extract_empty_is_ok():
    client = _FakeClient({"theses": []})
    assert extract_theses("body", SOURCE, client=client, extracted_at="t") == []


def test_extract_retries_once_then_succeeds():
    client = _FakeClient(INVALID_INPUT, VALID_INPUT)
    out = extract_theses("body", SOURCE, client=client, extracted_at="t")
    assert client.calls == 2
    assert len(out) == 1


def test_extract_raises_after_retry_exhausted():
    client = _FakeClient(INVALID_INPUT, INVALID_INPUT)
    with pytest.raises(ExtractionFailed):
        extract_theses("body", SOURCE, client=client, extracted_at="t")
    assert client.calls == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest tests/test_extract.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'distill.extract'`)

- [ ] **Step 3: Write minimal implementation**

Create `packages/distill/src/distill/extract.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

from core.thesis import Source, Thesis, ThesisExtraction, build_thesis
from distill.prompt import build_prompt

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 8192

_TOOL_NAME = "extract_theses"


class ExtractionFailed(Exception):
    """The model never returned a schema-valid extraction within the retry budget."""


def _tool_schema() -> dict:
    return {
        "name": _TOOL_NAME,
        "description": "Return all genuine trading calls found in the transcript.",
        "input_schema": ThesisExtraction.model_json_schema(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_input(message) -> dict:
    for block in message.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise ExtractionFailed("model returned no tool_use block")


def extract_theses(
    transcript_text: str,
    source: Source,
    *,
    client=None,
    model: str = DEFAULT_MODEL,
    retries: int = 2,
    extracted_at: str | None = None,
) -> list[Thesis]:
    if client is None:  # pragma: no cover - real client needs a network key
        import anthropic
        client = anthropic.Anthropic()
    extracted_at = extracted_at or _now()
    system, user = build_prompt(transcript_text, source)

    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=[_tool_schema()],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": user}],
            )
            extraction = ThesisExtraction.model_validate(_tool_input(message))
            return [
                build_thesis(e, source=source, model=model,
                             extracted_at=extracted_at, index=i)
                for i, e in enumerate(extraction.theses)
            ]
        except Exception as exc:  # noqa: BLE001 - validation/parse errors are retryable
            last_exc = exc
    raise ExtractionFailed(str(last_exc)) from last_exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest tests/test_extract.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades
git add packages/distill
git commit -m "feat(distill): Sonnet 5 extraction via forced tool-use + retry"
```

---

## Task 6: Roster sweep + run summary

Iterate the transcript store, skip already-distilled, distill the rest, bucket results, and format a per-person summary. Mirrors `ingestion/roster.py`.

**Files:**
- Create: `packages/distill/src/distill/roster.py`
- Test: `packages/distill/tests/test_roster.py`

- [ ] **Step 1: Write the failing test**

Create `packages/distill/tests/test_roster.py`:
```python
import json
from core.thesis import Extraction, Source, Thesis
from distill.roster import distill_all, format_summary, DistillResult


def _write_transcript(root, vid, person, text="body"):
    d = root / "transcripts" / "youtube"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid}.txt").write_text(text, encoding="utf-8")
    (d / f"{vid}.json").write_text(json.dumps({
        "url": f"https://www.youtube.com/watch?v={vid}", "title": "T",
        "published_at": "2025-02-28", "person": person,
        "platform": "youtube", "source_id": vid,
    }), encoding="utf-8")


def _one_thesis(source: Source) -> list[Thesis]:
    return [Thesis(id=f"{source.transcript_ref}#0", thesis_type="macro_lean",
                   domain="crypto", asset="BTC", direction="long",
                   timeframe="macro", conviction="med", summary="s", source=source,
                   extraction=Extraction(model="m", confidence=0.5, extracted_at="t"))]


def test_distill_all_buckets_results(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    _write_transcript(tmp_path, "vid00000002", "Cowen")

    def fake_extract(text, source, **kw):
        return _one_thesis(source) if source.transcript_ref.endswith("1") else []

    results = distill_all(root=tmp_path, extract=fake_extract,
                          distilled_at="2026-07-23T00:00:00+00:00")
    r = results[0]
    assert r.person == "Cowen"
    assert r.distilled == ["vid00000001"]
    assert r.empty == ["vid00000002"]


def test_distill_all_skips_already_distilled(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    calls = {"n": 0}

    def fake_extract(text, source, **kw):
        calls["n"] += 1
        return _one_thesis(source)

    distill_all(root=tmp_path, extract=fake_extract, distilled_at="t")
    results = distill_all(root=tmp_path, extract=fake_extract, distilled_at="t")
    assert calls["n"] == 1                       # second sweep skipped it
    assert results[0].skipped == ["vid00000001"]


def test_distill_all_records_failure_and_continues(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    _write_transcript(tmp_path, "vid00000002", "Cowen")

    def fake_extract(text, source, **kw):
        if source.transcript_ref.endswith("1"):
            raise RuntimeError("boom")
        return _one_thesis(source)

    results = distill_all(root=tmp_path, extract=fake_extract, distilled_at="t")
    r = results[0]
    assert r.failed and r.failed[0][0] == "vid00000001"
    assert r.distilled == ["vid00000002"]        # kept going


def test_force_redistills_existing(tmp_path):
    _write_transcript(tmp_path, "vid00000001", "Cowen")
    calls = {"n": 0}

    def fake_extract(text, source, **kw):
        calls["n"] += 1
        return _one_thesis(source)

    distill_all(root=tmp_path, extract=fake_extract, distilled_at="t")
    distill_all(root=tmp_path, extract=fake_extract, distilled_at="t", force=True)
    assert calls["n"] == 2                       # force bypassed the skip


def test_format_summary_has_totals(tmp_path):
    r = DistillResult(person="Cowen")
    r.distilled.append("a"); r.empty.append("b")
    out = format_summary([r])
    assert "Cowen" in out and "TOTAL" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest tests/test_roster.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'distill.roster'`)

- [ ] **Step 3: Write minimal implementation**

Create `packages/distill/src/distill/roster.py`:
```python
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
```

Note: the injected `extract` in tests is called as `extract(text, source, model=..., extracted_at=...)`; the test fakes accept `**kw`, and the real `extract_theses` accepts `model=` and `extracted_at=` keyword args — signatures line up.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest tests/test_roster.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades
git add packages/distill
git commit -m "feat(distill): roster sweep over transcript store + run summary"
```

---

## Task 7: CLI + console scripts

`distill-roster` (sweep) and `distill-transcript <video_id>` (one-off spot check). Scripts already registered in Task 3's pyproject.

**Files:**
- Create: `packages/distill/src/distill/cli.py`
- Test: `packages/distill/tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `packages/distill/tests/test_cli.py`:
```python
import distill.cli as cli


def test_roster_main_prints_summary(capsys, monkeypatch):
    from distill.roster import DistillResult
    monkeypatch.setattr(cli, "distill_all", lambda **kw: [DistillResult(person="Cowen")])
    rc = cli.roster_main([])
    assert rc == 0
    assert "Cowen" in capsys.readouterr().out


def test_transcript_main_distills_one(capsys, monkeypatch, tmp_path):
    import json
    d = tmp_path / "transcripts" / "youtube"
    d.mkdir(parents=True)
    (d / "vid00000001.txt").write_text("body")
    (d / "vid00000001.json").write_text(json.dumps({
        "platform": "youtube", "source_id": "vid00000001", "person": "P",
        "published_at": "2025-01-01", "url": "u"}))

    captured = {}
    monkeypatch.setattr(cli, "TRANSCRIPTS_ROOT", d.parent)
    monkeypatch.setattr(cli, "extract_theses", lambda text, source, **kw: [])
    monkeypatch.setattr(cli, "save_theses",
                        lambda *a, **k: captured.setdefault("saved", True))
    rc = cli.transcript_main(["vid00000001"])
    assert rc == 0
    assert captured.get("saved")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest tests/test_cli.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'distill.cli'`)

- [ ] **Step 3: Write minimal implementation**

Create `packages/distill/src/distill/cli.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest -q`
Expected: PASS (all distill tests pass)

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades
git add packages/distill
git commit -m "feat(distill): distill-roster / distill-transcript CLIs"
```

---

## Task 8: Integration test + data/ ignore verification

One real end-to-end distill against Sonnet 5, plus confirm theses are gitignored.

**Files:**
- Create: `packages/distill/tests/test_integration.py`

- [ ] **Step 1: Verify `data/` is gitignored (no code, just confirm)**

Run: `cd /Users/tseitz/code/projects/tegan-trades && git check-ignore data/theses/youtube/x.json`
Expected: prints the path (ignored). If it prints nothing, add `data/` to `.gitignore` and commit before proceeding.

- [ ] **Step 2: Write the integration test**

Create `packages/distill/tests/test_integration.py`:
```python
import os
import pytest

from core.thesis import Source, Thesis
from distill.extract import extract_theses

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"),
                    reason="needs ANTHROPIC_API_KEY")
def test_real_extraction_smoke():
    source = Source(person="Test", platform="youtube",
                    url="https://youtu.be/x", published_at="2025-02-28",
                    transcript_ref="youtube/x")
    text = ("I think Bitcoin is a long here. I'm bullish into year-end and expect "
            "a new all-time high. If we lose the 90k level though, I'm wrong and "
            "would close it. Ethereum I'm just watching for now.")
    theses = extract_theses(text, source)
    assert isinstance(theses, list)
    for t in theses:
        assert isinstance(t, Thesis)
        assert t.asset
        assert t.id.startswith("youtube/x#")


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"),
                    reason="needs ANTHROPIC_API_KEY")
def test_pure_chatter_yields_empty():
    source = Source(person="Test", platform="youtube", url="u",
                    published_at="2025-02-28", transcript_ref="youtube/y")
    text = "Thanks for watching, smash that like button and check the link below."
    assert extract_theses(text, source) == []
```

- [ ] **Step 3: Run the integration test explicitly**

Run: `cd /Users/tseitz/code/projects/tegan-trades/packages/distill && ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY uv run pytest -m integration -q`
Expected: PASS (2 passed) — a real Sonnet 5 call returns a bullish-BTC trade/lean and an empty list for chatter.

- [ ] **Step 4: Full unit sweep (both packages)**

Run:
```bash
cd /Users/tseitz/code/projects/tegan-trades/packages/core && uv run pytest -q
cd /Users/tseitz/code/projects/tegan-trades/packages/distill && uv run pytest -q -m "not integration"
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd /Users/tseitz/code/projects/tegan-trades
git add packages/distill
git commit -m "test(distill): real Sonnet 5 extraction integration smoke"
```

---

## After all tasks

- **Do NOT run the full 620-transcript backfill automatically** — that spends real API budget. Present the option to the user: spot-check a handful of long streams first (`distill-transcript <id>` on a Cowen/TTrades livestream), eyeball the calls against the transcript for "lost in the middle" misses, then decide whether to sweep the full roster with `distill-roster`.
- Update `~/vault/Claude/Projects/tegan-trades/phase-2-spec.md` and the project memory with the "as-built" outcome (any schema tweaks discovered during implementation).
