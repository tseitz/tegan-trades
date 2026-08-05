"""Transcript chunking with overlapping windows and sentence-boundary preference.

Chunks transcripts for embedding, preferring to end at sentence boundaries
(period, exclamation, question mark, or newline) within a configurable lookback
window. Handles overlap correctly and uses content-addressed ids.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

CHUNK_SIZE = 1800      # characters
CHUNK_OVERLAP = 200    # characters

# Content-addressed id generation (mirrors core.stance pattern)
_ID_SEP = "\x1f"   # unit separator — cannot appear in extracted text
_ID_LEN = 12       # 48 bits; collisions are negligible


@dataclass(frozen=True)
class Chunk:
    """A single chunk of transcript text with content-addressed id.

    Attributes:
        id: Content-addressed identifier (transcript_ref#hash of text).
        transcript_ref: Reference to the source transcript.
        index: 0-based position in the sequence of chunks.
        start: Character offset into the source text (inclusive).
        end: Character offset (exclusive); text == source[start:end].
        text: The chunk's text content, with leading/trailing whitespace stripped.
    """
    id: str
    transcript_ref: str
    index: int
    start: int
    end: int
    text: str


def _chunk_id(transcript_ref: str, text: str) -> str:
    """Content-addressed id based on transcript_ref and chunk text.

    The id is computed from the content (transcript_ref + text), not from
    positional information (index, start, end). This means re-running the
    chunking algorithm over the same transcript produces identical ids,
    even if offsets shift — the id identifies content, not a slot.

    Whitespace and case are normalized to prevent trivial reformatting
    from churning the id.
    """
    parts = [transcript_ref, text]
    # Whitespace/case normalized so trivial reformatting doesn't churn the id.
    raw = _ID_SEP.join(" ".join(str(p).split()).lower() for p in parts)
    return f"{transcript_ref}#{sha256(raw.encode('utf-8')).hexdigest()[:_ID_LEN]}"


def _find_sentence_boundary(text: str, end_pos: int, lookback: int) -> int | None:
    """Search backwards from end_pos for a sentence-ending boundary.

    A boundary is: '. ', '! ', '? ', or a newline. The search space is
    from max(0, end_pos - lookback) to end_pos.

    Args:
        text: Full source text to search within.
        end_pos: Position to search backwards from.
        lookback: Maximum distance to search backwards.

    Returns:
        Position after the boundary marker (e.g., after '. ' or '\n'),
        or None if no boundary is found within the window.
    """
    search_start = max(0, end_pos - lookback)
    search_text = text[search_start:end_pos]

    # Search backwards within the lookback window
    for i in range(len(search_text) - 1, -1, -1):
        if search_text[i] == "\n":
            # Newline is a boundary; return position after it
            return search_start + i + 1
        # Check for '. ', '! ', '? ' — needs a following char, hence the bound.
        if (
            i < len(search_text) - 1
            and search_text[i] in ".!?"
            and search_text[i + 1] == " "
        ):
            return search_start + i + 2

    return None


def chunk_transcript(
    text: str,
    transcript_ref: str,
    *,
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[Chunk]:
    """Split a transcript into overlapping text chunks for embedding.

    Chunks are at most `size` characters and prefer to end at sentence
    boundaries (. ! ? or newline) within a lookback window of size//6.
    If no boundary is found, a hard cut at size characters is used.

    Each subsequent chunk starts `overlap` characters before the previous
    chunk's end, ensuring context. Forward progress is guaranteed: each
    chunk's start is strictly greater than the previous chunk's start.

    Leading/trailing whitespace on individual chunks is stripped, but
    start/end offsets remain accurate to the original string.

    Args:
        text: The transcript text to chunk.
        transcript_ref: Identifier for the source transcript.
        size: Maximum characters per chunk (default 1800).
        overlap: Characters of overlap between consecutive chunks (default 200).

    Returns:
        List of Chunk objects covering the full text, or [] if text is empty
        or whitespace-only.
    """
    # Strip and handle empty input
    if not text.strip():
        return []

    chunks: list[Chunk] = []
    lookback = size // 6
    # A boundary is only worth honouring if the resulting chunk is still nearly a full
    # window. Accepting any boundary merely *ahead* of current_start is what made this
    # degenerate: a boundary just past the start yields a tiny chunk, next_start
    # (chunk_end - overlap) then lands at or before current_start, the forward-progress
    # guard bumps it by a single character, and the loop crawls one char at a time
    # emitting hundreds of near-duplicate chunks. Measured before this floor existed:
    # a 3,823-char transcript produced 203 chunks instead of 3.
    min_chunk = max(1, size - lookback)
    current_start = 0
    index = 0

    while current_start < len(text):
        # Compute the raw end position (hard cut)
        raw_end = min(current_start + size, len(text))

        if raw_end >= len(text):
            # Final chunk: take the whole remainder. Searching for a boundary here is
            # what produced the pathological tail loop — a boundary short of the end
            # leaves a remainder smaller than the overlap, so the next iteration
            # re-reads almost the same span and only inches forward.
            chunk_end = raw_end
        else:
            boundary_pos = _find_sentence_boundary(text, raw_end, lookback)
            if boundary_pos is not None and boundary_pos >= current_start + min_chunk:
                chunk_end = boundary_pos
            else:
                # No boundary found, or honouring it would cut the chunk too short.
                chunk_end = raw_end

        # Extract raw text and strip it
        raw_chunk_text = text[current_start:chunk_end]
        stripped_text = raw_chunk_text.strip()

        # Skip empty chunks after stripping
        if not stripped_text:
            # Move past the whitespace and try again
            current_start = chunk_end
            if current_start >= len(text):
                break
            continue

        # Find the actual start/end offsets of the stripped text within original
        # The stripped text starts at the first non-whitespace char after current_start
        actual_start = current_start
        while actual_start < chunk_end and text[actual_start].isspace():
            actual_start += 1

        # The end is at the last non-whitespace char in the chunk
        actual_end = chunk_end
        while actual_end > actual_start and text[actual_end - 1].isspace():
            actual_end -= 1

        # Trimming leading whitespace can pull two different raw spans onto the same
        # first non-space character, so a chunk can end up adding nothing the previous
        # one didn't already cover. Skip those: they would break the strictly-increasing
        # `start` guarantee and, because ids are content-addressed, collide on id too.
        if chunks and actual_start <= chunks[-1].start and actual_end <= chunks[-1].end:
            if chunk_end >= len(text):
                break
            current_start = max(chunk_end - overlap, current_start + 1)
            continue

        # Create the chunk
        chunk = Chunk(
            id=_chunk_id(transcript_ref, stripped_text),
            transcript_ref=transcript_ref,
            index=index,
            start=actual_start,
            end=actual_end,
            text=stripped_text,
        )
        chunks.append(chunk)
        index += 1

        # If we've reached the end of the text, stop
        if chunk_end >= len(text):
            break

        # Compute the next start position (overlap from current end)
        next_start = chunk_end - overlap

        # Backstop only. With `min_chunk` above, a normal call already strides
        # >= size - lookback - overlap per iteration; this catches pathological
        # parameters (e.g. size=10, overlap=9) where that stride can reach zero.
        if next_start <= current_start:
            next_start = current_start + 1

        current_start = next_start

    return chunks
