"""Tests for transcript chunking with overlapping windows and sentence boundaries."""
from __future__ import annotations

from itertools import pairwise

import pytest
from brain.chunk import CHUNK_OVERLAP, CHUNK_SIZE, chunk_transcript


class TestEmptyAndWhitespace:
    """Empty inputs should return empty chunk lists."""

    def test_empty_string(self):
        result = chunk_transcript("", "ref/123")
        assert result == []

    def test_whitespace_only(self):
        result = chunk_transcript("   \n  \t  ", "ref/123")
        assert result == []

    def test_tabs_and_newlines(self):
        result = chunk_transcript("\t\n\r\n", "ref/123")
        assert result == []


class TestSingleChunk:
    """Text shorter than chunk size should yield exactly one chunk."""

    def test_short_text(self):
        text = "hello world"
        result = chunk_transcript(text, "ref/123")
        assert len(result) == 1
        chunk = result[0]
        assert chunk.index == 0
        assert chunk.start == 0
        assert chunk.end == len(text)
        assert chunk.text == text
        assert chunk.transcript_ref == "ref/123"

    def test_text_exactly_size(self):
        """Text exactly at size boundary should still be one chunk."""
        text = "x" * CHUNK_SIZE
        result = chunk_transcript(text, "ref/123")
        assert len(result) == 1
        assert result[0].text == text
        assert result[0].start == 0
        assert result[0].end == CHUNK_SIZE


class TestOffsetInvariant:
    """The invariant: chunk.text == original[chunk.start:chunk.end] must hold always."""

    def test_offset_invariant_long_text(self):
        """Test offset invariant on realistic longer text."""
        text = (
            "so if we look at bitcoin here. i think we're going to see a move. "
            "the weekly close matters a lot. there's a lot of support down here. "
            "if we break below this level, we could see some weakness. "
            "but for now, i'm bullish on the long-term outlook. "
            "ethereum is looking strong too. the merge was a big catalyst. "
            "i expect we'll see more adoption this year. prices should follow. "
            "my targets are aggressive but realistic given the fundamentals. "
        ) * 10  # Make it long enough to generate multiple chunks

        result = chunk_transcript(text, "ref/123")
        assert len(result) > 1

        for chunk in result:
            reconstructed = text[chunk.start : chunk.end]
            assert (
                chunk.text == reconstructed
            ), f"Chunk {chunk.index}: text mismatch at [{chunk.start}:{chunk.end}]"
            # Also verify the text matches what we expect
            assert chunk.text == reconstructed

    def test_offset_invariant_with_leading_trailing_whitespace(self):
        """Offsets should be accurate even when chunk.text is stripped."""
        text = "  hello world  " + "x" * CHUNK_SIZE * 2 + "  goodbye  "
        result = chunk_transcript(text, "ref/123")

        for chunk in result:
            # The text is stripped, but start/end should still point correctly
            original_span = text[chunk.start : chunk.end]
            assert chunk.text == original_span


class TestChunkOrdering:
    """Chunks should be sequential with correct indices."""

    def test_chunk_indices_sequential(self):
        text = "x" * (CHUNK_SIZE * 3)
        result = chunk_transcript(text, "ref/123")

        assert len(result) >= 2
        for i, chunk in enumerate(result):
            assert chunk.index == i

    def test_chunk_starts_strictly_increasing(self):
        """Each chunk start must be strictly greater than the previous."""
        text = "x" * (CHUNK_SIZE * 5)
        result = chunk_transcript(text, "ref/123")

        for i in range(len(result) - 1):
            assert result[i].start < result[i + 1].start


class TestOverlapAndCoverage:
    """Chunks should overlap correctly and cover the full text."""

    def test_overlap_between_consecutive_chunks(self):
        text = "a" * 100 + "b" * 100 + "c" * 100 + "d" * 100 + "e" * 100
        result = chunk_transcript(
            text, "ref/123", size=CHUNK_SIZE, overlap=CHUNK_OVERLAP
        )

        if len(result) > 1:
            for i in range(len(result) - 1):
                current_end = result[i].end
                next_start = result[i + 1].start
                # Next chunk should start before current ends (overlap)
                assert next_start < current_end
                # The overlap should be approximately CHUNK_OVERLAP
                actual_overlap = current_end - next_start
                assert actual_overlap > 0

    def test_full_text_coverage(self):
        """Last chunk should end at len(text)."""
        text = "x" * (CHUNK_SIZE * 3 + 500)
        result = chunk_transcript(text, "ref/123")

        assert len(result) > 0
        assert result[-1].end == len(text)

    def test_no_gaps_between_chunks(self):
        """Chunks should overlap; no gaps in coverage."""
        text = "a" * (CHUNK_SIZE * 2 + 200)
        result = chunk_transcript(text, "ref/123")

        if len(result) > 1:
            for i in range(len(result) - 1):
                # Next chunk should start before or at current end
                assert result[i + 1].start < result[i].end


class TestSentenceBoundaryPreference:
    """Should prefer sentence boundaries within the lookback window."""

    def test_sentence_boundary_within_window(self):
        """When a period+space falls within the lookback, prefer it."""
        lookback = CHUNK_SIZE // 6
        # Build text where period+space is just inside the lookback window
        before_boundary = "x" * (CHUNK_SIZE - lookback + 10)
        boundary = ". "
        after_boundary = "y" * 100

        text = before_boundary + boundary + after_boundary

        result = chunk_transcript(text, "ref/123", size=CHUNK_SIZE, overlap=100)

        if len(result) > 0:
            first_chunk = result[0]
            # Should end after the period+space (prefer boundary)
            # The chunk should include the period
            assert "." in first_chunk.text

    def test_no_boundary_in_window_hard_cut(self):
        """No boundary in lookback window → hard cut at exactly size."""
        lookback = CHUNK_SIZE // 6
        # Text with NO punctuation in lookback window
        text = "x" * (CHUNK_SIZE + lookback + 100)

        result = chunk_transcript(text, "ref/123", size=CHUNK_SIZE, overlap=100)

        first_chunk = result[0]
        # With no punctuation, first chunk should be exactly CHUNK_SIZE
        # (the hard cut at start+size)
        assert first_chunk.end - first_chunk.start == CHUNK_SIZE

    def test_multiple_boundary_types(self):
        """Should recognize . ! ? and newline as boundaries."""
        text = (
            "first sentence. second sentence! third sentence? fourth\nfifth sentence. "
            + "x" * (CHUNK_SIZE * 2)
            + "final sentence."  # End with punctuation
        )

        result = chunk_transcript(text, "ref/123")
        assert len(result) > 0
        # Multiple chunks should be created because text is long
        assert len(result) > 1
        # Chunks should contain boundaries within their text
        has_boundaries = any("." in chunk.text for chunk in result)
        assert has_boundaries


class TestPathologicalSize:
    """Large overlap relative to size should still terminate with forward progress."""

    def test_size_10_overlap_9_terminates(self):
        """Pathological: overlap almost as large as size."""
        text = "x" * 500
        result = chunk_transcript(text, "ref/123", size=10, overlap=9)

        # Should not loop infinitely; should produce chunks
        assert len(result) > 0

        # All starts must be strictly increasing
        starts = [chunk.start for chunk in result]
        assert starts == sorted(starts)
        assert len(starts) == len(set(starts))  # No duplicates

        # Coverage should be complete
        assert result[-1].end == len(text)


class TestContentAddressedIds:
    """IDs should be content-addressed based on transcript_ref and text."""

    def test_deterministic_ids(self):
        """Same input should produce identical ids."""
        text = "test transcript content"
        result1 = chunk_transcript(text, "ref/123")
        result2 = chunk_transcript(text, "ref/123")

        assert result1[0].id == result2[0].id

    def test_different_transcript_ref_different_id(self):
        """Different transcript_ref should yield different ids even for same text."""
        text = "test transcript content"
        result1 = chunk_transcript(text, "ref/123")
        result2 = chunk_transcript(text, "ref/456")

        assert result1[0].id != result2[0].id

    def test_same_text_same_ref_same_id(self):
        """Same text in same transcript should have same id (content-addressed)."""
        text = "test content " + "x" * CHUNK_SIZE
        result1 = chunk_transcript(text, "ref/123")
        result2 = chunk_transcript(text, "ref/123")

        # All corresponding chunks should have identical ids
        assert len(result1) == len(result2)
        for c1, c2 in zip(result1, result2, strict=True):
            assert c1.id == c2.id

    def test_different_text_different_id(self):
        """Different text should produce different ids."""
        result1 = chunk_transcript("text one", "ref/123")
        result2 = chunk_transcript("text two", "ref/123")

        if len(result1) > 0 and len(result2) > 0:
            assert result1[0].id != result2[0].id


class TestRealisticASRInput:
    """Test with realistic ASR-style transcripts (lowercase, flat text)."""

    def test_realistic_transcript_chunking(self):
        """Real-world ASR transcript: flat, lowercase, minimal punctuation."""
        text = (
            "so if we look at bitcoin here. i think we're going to see a move. "
            "the weekly close matters a lot. there's key support at twenty thousand. "
            "if we break below this level, we could see weakness to eighteen k. "
            "but for now, i'm bullish. ethereum is looking strong too. "
            "the merge was a catalyst for the ecosystem. i expect adoption to grow. "
            "staking rewards are attractive at current rates. "
        ) * 5
        text = text.rstrip()  # Remove trailing whitespace

        result = chunk_transcript(text, "youtube/abc123")

        # Should produce multiple chunks
        assert len(result) > 1

        # All chunks should have valid offsets
        for chunk in result:
            assert chunk.start < chunk.end
            assert text[chunk.start : chunk.end].strip() == chunk.text.strip()

        # Should cover the full text
        assert result[-1].end == len(text)

        # Chunks should be in order with increasing starts
        starts = [c.start for c in result]
        assert starts == sorted(starts)


class TestWhitespaceStripping:
    """Leading/trailing whitespace on chunks should be stripped."""

    def test_text_stripped_but_offsets_accurate(self):
        """Chunk text is stripped, but start/end point to original bounds."""
        text = "  hello world  " + "x" * (CHUNK_SIZE * 2)
        result = chunk_transcript(text, "ref/123")

        for chunk in result:
            # Text should not have leading/trailing whitespace
            assert chunk.text == chunk.text.strip()
            # But offsets should still point to the right location
            assert text[chunk.start : chunk.end].strip() == chunk.text

    def test_skip_empty_chunks_after_stripping(self):
        """A chunk that becomes empty after stripping should be skipped."""
        # Build a pathological case with mostly whitespace at a chunk boundary
        text = "x" * CHUNK_SIZE + "   \n  " + "y" * CHUNK_SIZE
        result = chunk_transcript(text, "ref/123", size=CHUNK_SIZE, overlap=50)

        # Should not include an empty chunk
        for chunk in result:
            assert len(chunk.text) > 0


class TestChunkId:
    """Verify chunk id structure and content-addressing."""

    def test_id_format(self):
        """ID should be transcript_ref#hash (12 hex chars)."""
        result = chunk_transcript("test", "myref/123")
        chunk = result[0]

        assert "#" in chunk.id
        parts = chunk.id.split("#")
        assert len(parts) == 2
        assert parts[0] == "myref/123"
        # Hash part should be exactly 12 hex chars
        hash_part = parts[1]
        assert len(hash_part) == 12
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_hash_based_on_content(self):
        """Hash should change when text changes."""
        text1 = "content one"
        text2 = "content two"

        result1 = chunk_transcript(text1, "ref/123")
        result2 = chunk_transcript(text2, "ref/123")

        assert result1[0].id != result2[0].id

    def test_hash_not_based_on_index_or_offsets(self):
        """Hash should not include index or offsets (content-addressed only)."""
        # Two calls that might produce chunks with different indices
        # but same text should produce same hash
        text = "test " + "x" * (CHUNK_SIZE * 3)

        result1 = chunk_transcript(text, "ref/123")
        result2 = chunk_transcript(text, "ref/123")

        # Corresponding chunks should have same ids
        for c1, c2 in zip(result1, result2, strict=True):
            assert c1.id == c2.id


class TestChunkDataclass:
    """Verify Chunk dataclass structure."""

    def test_chunk_fields(self):
        """Chunk should have all required fields."""
        text = "hello world"
        result = chunk_transcript(text, "myref")
        chunk = result[0]

        assert hasattr(chunk, "id")
        assert hasattr(chunk, "transcript_ref")
        assert hasattr(chunk, "index")
        assert hasattr(chunk, "start")
        assert hasattr(chunk, "end")
        assert hasattr(chunk, "text")

        assert chunk.id is not None
        assert chunk.transcript_ref == "myref"
        assert chunk.index == 0
        assert chunk.start >= 0
        assert chunk.end > chunk.start
        assert chunk.text == "hello world"

    def test_chunk_frozen(self):
        """Chunk should be immutable (frozen dataclass)."""
        result = chunk_transcript("test", "ref/123")
        chunk = result[0]

        with pytest.raises(AttributeError):
            chunk.text = "modified"  # type: ignore


class TestCustomSizeAndOverlap:
    """Test with custom size and overlap parameters."""

    def test_custom_size(self):
        """Custom chunk size should be respected."""
        text = "x" * 500
        size = 100
        result = chunk_transcript(text, "ref/123", size=size, overlap=10)

        # Each chunk (except possibly the last) should be close to size
        for chunk in result[:-1]:
            chunk_len = chunk.end - chunk.start
            # Should be at most size chars
            assert chunk_len <= size

    def test_custom_overlap(self):
        """Custom overlap should be applied."""
        text = "x" * 500
        overlap = 50
        result = chunk_transcript(text, "ref/123", size=200, overlap=overlap)

        if len(result) > 1:
            for i in range(len(result) - 1):
                current_end = result[i].end
                next_start = result[i + 1].start
                actual_overlap = current_end - next_start
                # Should roughly match requested overlap
                # (exact match only if no sentence boundaries)
                assert actual_overlap > 0


class TestEdgeCases:
    """Various edge cases and boundary conditions."""

    def test_single_word(self):
        text = "word"
        result = chunk_transcript(text, "ref/123")
        assert len(result) == 1
        assert result[0].text == "word"

    def test_text_with_only_punctuation(self):
        text = ". ! ? . ! ?"
        result = chunk_transcript(text, "ref/123")
        # Should produce at least one chunk even if it's just punctuation
        assert len(result) >= 1

    def test_very_long_single_word(self):
        """A very long word with no spaces should still chunk."""
        text = "x" * (CHUNK_SIZE * 2)
        result = chunk_transcript(text, "ref/123")

        assert len(result) >= 1
        assert result[-1].end == len(text)

    def test_boundary_exactly_at_lookback_edge(self):
        """Test when a boundary falls exactly at the edge of the lookback window."""
        lookback = CHUNK_SIZE // 6
        text = "x" * (CHUNK_SIZE - lookback) + ". " + "y" * 200
        result = chunk_transcript(text, "ref/123", size=CHUNK_SIZE, overlap=50)

        assert len(result) > 0
        # First chunk should end somewhere reasonable
        assert result[0].end <= len(text)


# ── regression: degenerate over-chunking (found against the real corpus) ─────
#
# The first implementation accepted any sentence boundary merely ahead of the chunk
# start. That produced a tiny chunk, so `chunk_end - overlap` landed at or before the
# current start, the forward-progress guard advanced a single character, and the loop
# crawled — a 3,823-char transcript yielded 203 chunks instead of 3, and 23 of 25 real
# transcripts were affected. These pin the properties that catch it.

def _properties(text, chunks, size):
    """Every invariant that must hold for any input. Returns a list of violations."""
    errs = []
    for c in chunks:
        if text[c.start:c.end] != c.text:
            errs.append(f"offset invariant broken at index {c.index}")
        if len(c.text) > size:
            errs.append(f"chunk {c.index} exceeds size ({len(c.text)} > {size})")
    for a, b in pairwise(chunks):
        if b.index != a.index + 1:
            errs.append(f"index gap {a.index} -> {b.index}")
        if b.start <= a.start:
            errs.append(f"no forward progress {a.start} -> {b.start}")
        if b.start > a.end:
            errs.append(f"coverage gap: {a.end} -> {b.start}")
    covered = bytearray(len(text))
    for c in chunks:
        for i in range(c.start, c.end):
            covered[i] = 1
    lost = [i for i, ch in enumerate(text) if not ch.isspace() and not covered[i]]
    if lost:
        errs.append(f"lost {len(lost)} non-space chars, first at {lost[0]}")
    return errs


_ASR = (
    "so if we look at bitcoin here i think we're going to see a move. the weekly close "
    "matters a lot to me and honestly the four hour is still bearish. now ethereum is a "
    "different story entirely. i've been saying this for weeks now! what would change my "
    "mind? a daily close back above the range high.\n"
)


def test_realistic_transcript_does_not_degenerate_into_hundreds_of_chunks():
    text = _ASR * 30  # ~11k chars
    chunks = chunk_transcript(text, "youtube/abc123")
    assert _properties(text, chunks, 1800) == []
    # ~1800-char windows over ~11k chars is single digits, not hundreds.
    assert len(chunks) < 3 * (len(text) // 1800 + 2)


def test_average_chunk_stays_near_the_target_size():
    """The degenerate loop showed up as a collapsed mean chunk length long before it
    showed up as a wrong answer."""
    text = _ASR * 30
    chunks = chunk_transcript(text, "youtube/abc123")
    mean = sum(len(c.text) for c in chunks) / len(chunks)
    assert mean > 1800 * 0.6, f"mean chunk length collapsed to {mean:.0f}"


def test_a_short_text_just_over_one_window_yields_two_chunks_not_dozens():
    text = _ASR * 6  # ~2.2k chars: one full window plus a small remainder
    chunks = chunk_transcript(text, "youtube/abc123")
    assert _properties(text, chunks, 1800) == []
    assert len(chunks) <= 3


def test_properties_hold_under_pathological_parameters():
    """overlap at 90% of size is not a real config, but it must still terminate and
    satisfy every invariant rather than emitting duplicate-start chunks."""
    text = "word " * 400
    chunks = chunk_transcript(text, "youtube/abc123", size=10, overlap=9)
    assert _properties(text, chunks, 10) == []


def test_text_with_no_sentence_boundaries_at_all_still_strides_full_windows():
    text = "x" * 5000
    chunks = chunk_transcript(text, "youtube/abc123")
    assert _properties(text, chunks, 1800) == []
    # Hard cuts at 1800 with 200 overlap -> a 1600-char stride: [0,1800] [1600,3400]
    # [3200,5000]. Three windows, and critically the last one lands exactly on the end.
    assert len(chunks) == 3
    assert chunks[-1].end == len(text)
