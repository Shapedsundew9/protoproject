"""Embedding providers for Phase 1."""

from __future__ import annotations

from typing import Protocol

from .progress import ProgressReporter, emit_progress


class EmbeddingProvider(Protocol):
    """Interface for text embedding backends."""

    def embed_text(self, text: str) -> list[float]: ...


class SentenceTransformerProvider:
    """Semantic embedding provider using a local sentence-transformers model.

    The model is lazy-loaded on first use to avoid import-time cost.
    Vectors are unit-normalised 384-dim floats (all-MiniLM-L6-v2).
    """

    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, progress: ProgressReporter | None = None) -> None:
        self._model = None
        self._progress = progress

    def embed_text(self, text: str) -> list[float]:
        if self._model is None:
            emit_progress(
                self._progress,
                stage="embed_model",
                status="started",
                message=f"Loading embedding model {self.MODEL_NAME}.",
            )
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415

            self._model = SentenceTransformer(self.MODEL_NAME)
            emit_progress(
                self._progress,
                stage="embed_model",
                status="completed",
                message=f"Loaded embedding model {self.MODEL_NAME}.",
            )
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()
