"""Mechanical validation and normalization for requirement drafts."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from .embeddings import HashEmbeddingProvider
from .models import RequirementDraft, RequirementRecord, SourceRecord


@dataclass(slots=True)
class ValidationContext:
    """Values used to normalize a single ingest run."""

    source: SourceRecord
    embedding_provider: HashEmbeddingProvider
    version: int = 1
    state: str = "Draft"
    layer: str = "Product"
    concern_value: int = 3


def build_source_record(raw_text: str, source_type: str = "Markdown") -> SourceRecord:
    """Create a stable source record for a raw document."""

    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    source_id = f"SRC-{digest[:8].upper()}"
    return SourceRecord(id=source_id, type=source_type, hash=digest, text=raw_text)


def normalize_requirements(
    drafts: list[RequirementDraft],
    context: ValidationContext,
) -> list[RequirementRecord]:
    """Turn parsed drafts into persistence-ready records."""

    normalized: list[RequirementRecord] = []
    source_seed = context.source.hash[:8]
    timestamp = int(time.time())

    for index, draft in enumerate(drafts, start=1):
        requirement_id = f"REQ-{source_seed}-{index:04d}"
        parent_id = None
        if draft.parent_index is not None and draft.parent_index < len(normalized):
            parent_id = normalized[draft.parent_index].id

        embedding = context.embedding_provider.embed_text(draft.text)
        normalized.append(
            RequirementRecord(
                id=requirement_id,
                text=draft.text,
                embedding=embedding,
                layer=draft.layer or context.layer,
                concern_value=(
                    draft.concern_value
                    if draft.concern_value is not None
                    else context.concern_value
                ),
                state=context.state,
                version=context.version,
                timestamp=timestamp,
                source_id=context.source.id,
                parent_id=parent_id,
                depends_on_ids=[],
            )
        )

    return normalized
