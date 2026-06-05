"""Embedding helpers for Phase 1."""

from __future__ import annotations

import hashlib


class HashEmbeddingProvider:
    """Deterministic fallback embedding provider.

    This keeps Phase 1 self-contained while the real embedding backend is
    decided and wired in later.
    """

    def __init__(self, dimension: int = 32) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = [token for token in _normalize(text).split() if token]
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = digest[0] % self.dimension
            weight = (digest[1] / 255.0) + 0.5
            vector[bucket] += weight

        magnitude = sum(value * value for value in vector) ** 0.5
        if magnitude == 0:
            return vector
        return [round(value / magnitude, 6) for value in vector]


def _normalize(text: str) -> str:
    return " ".join(text.lower().strip().split())
