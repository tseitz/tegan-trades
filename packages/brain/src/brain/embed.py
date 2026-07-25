"""Local embedding interface — a narrow, swappable seam between chunking and
the vector store.

Any concrete embedder need only satisfy `Embedder`: `.dim` and `.embed(texts)`.
Swapping models (or, later, a hosted embedding API) means writing a new class
against this Protocol — retrieval code (`brain.vector_store`) never imports
`fastembed` directly.

Vectors are **L2-normalized here, once, at write time**. Because every vector
stored (and every query vector) is unit-length, cosine similarity between two
vectors reduces to a plain dot product — `vector_store.search` relies on this
and does not renormalize.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (len(texts), dim) float32 array of L2-normalized vectors."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """Return a single (dim,) float32, L2-normalized vector."""
        ...


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """Normalize each row to unit length. A zero-norm row would otherwise divide
    by zero and emit NaN; guard it and return the zero vector unchanged instead."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    safe_norms = np.where(norms == 0, 1.0, norms)
    return (vectors / safe_norms).astype(np.float32)


class FastEmbedder:
    """Wraps `fastembed.TextEmbedding`.

    Lazy-loaded: constructing `FastEmbedder()` stores only the model name/dim
    and does not import `fastembed` or touch disk/network. The model loads on
    the first call to `embed()`. This keeps `import brain.embed` and plain
    construction instant, so unit tests can use this module freely without a
    real model ever being materialized.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, dim: int = EMBED_DIM) -> None:
        self._model_name = model_name
        self._dim = dim
        self._model = None

    @property
    def dim(self) -> int:
        return self._dim

    def _ensure_model(self):
        if self._model is None:
            from fastembed import TextEmbedding  # deferred: this is the load point

            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        model = self._ensure_model()
        vectors = np.array(list(model.embed(texts)), dtype=np.float32)
        return _l2_normalize(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]
