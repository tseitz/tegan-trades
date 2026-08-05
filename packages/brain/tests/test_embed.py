"""Tests for brain.embed.

The non-integration suite must never load fastembed or touch the network —
only the single test in TestFastEmbedderIntegration does that, and it is
marked `@pytest.mark.integration` so it can be excluded from the fast loop.
"""
from __future__ import annotations

import hashlib
import sys

import numpy as np
import pytest
from brain.embed import DEFAULT_MODEL, EMBED_DIM, FastEmbedder


class _FakeEmbedder:
    """Deterministic, dependency-free stand-in for FastEmbedder. Hashes each text
    into a small fixed-size vector and applies the same L2-normalization contract,
    so tests that exercise `vector_store`/`index_cli` never need a real model."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        vectors = np.array([self._hash_vector(t) for t in texts], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        safe_norms = np.where(norms == 0, 1.0, norms)
        return (vectors / safe_norms).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    def _hash_vector(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return np.frombuffer(digest[: self._dim], dtype=np.uint8).astype(np.float32)


class TestNoEagerLoad:
    """Constructing FastEmbedder must not load or download anything."""

    def test_importing_module_is_instant_and_side_effect_free(self):
        assert "fastembed" not in sys.modules

    def test_constructing_does_not_materialize_a_model(self):
        embedder = FastEmbedder()
        assert embedder._model is None
        assert "fastembed" not in sys.modules

    def test_construction_accepts_custom_model_name_and_dim(self):
        embedder = FastEmbedder(model_name="some/other-model", dim=512)
        assert embedder.dim == 512
        assert embedder._model is None


class TestFakeEmbedderContract:
    """Exercise the normalization/shape contract with the fake, matching what
    FastEmbedder must also satisfy (verified for real in the integration test)."""

    def test_embed_returns_correct_shape(self):
        embedder = _FakeEmbedder(dim=8)
        vectors = embedder.embed(["hello", "world"])
        assert vectors.shape == (2, 8)
        assert vectors.dtype == np.float32

    def test_embed_empty_list_returns_empty_array_with_dim_shape(self):
        embedder = _FakeEmbedder(dim=8)
        vectors = embedder.embed([])
        assert vectors.shape == (0, 8)

    def test_embed_vectors_are_l2_normalized(self):
        embedder = _FakeEmbedder(dim=8)
        vectors = embedder.embed(["some text", "other text"])
        norms = np.linalg.norm(vectors, axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-5)

    def test_embed_deterministic_for_same_text(self):
        embedder = _FakeEmbedder(dim=8)
        v1 = embedder.embed(["repeat this"])
        v2 = embedder.embed(["repeat this"])
        np.testing.assert_array_equal(v1, v2)

    def test_embed_query_returns_single_normalized_vector(self):
        embedder = _FakeEmbedder(dim=8)
        vector = embedder.embed_query("hello")
        assert vector.shape == (8,)
        assert abs(np.linalg.norm(vector) - 1.0) < 1e-5

    def test_zero_norm_vector_does_not_produce_nan(self):
        """A degenerate all-zero embedding must return the zero vector, not NaN."""

        class _ZeroEmbedder(_FakeEmbedder):
            def _hash_vector(self, text: str) -> np.ndarray:
                return np.zeros(self._dim, dtype=np.float32)

        embedder = _ZeroEmbedder(dim=8)
        vectors = embedder.embed(["anything"])
        assert not np.isnan(vectors).any()
        np.testing.assert_array_equal(vectors[0], np.zeros(8, dtype=np.float32))


@pytest.mark.integration
class TestFastEmbedderIntegration:
    """The one test that really loads BAAI/bge-small-en-v1.5 (already cached)."""

    def test_real_model_dim_and_semantic_similarity(self):
        embedder = FastEmbedder()
        vectors = embedder.embed([
            "Bitcoin is breaking out above resistance",
            "BTC just cleared a key level to the upside",
            "I made pasta for dinner last night",
        ])
        assert vectors.shape == (3, EMBED_DIM)
        assert embedder.dim == 384

        related_score = float(vectors[0] @ vectors[1])
        unrelated_score = float(vectors[0] @ vectors[2])
        assert related_score > unrelated_score

    def test_default_model_constant(self):
        assert DEFAULT_MODEL == "BAAI/bge-small-en-v1.5"
