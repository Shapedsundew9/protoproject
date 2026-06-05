"""Data models used by the Phase 1 pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceRecord:
    """Origin metadata for an ingestion run."""

    id: str
    type: str
    hash: str
    text: str


@dataclass(slots=True)
class RequirementDraft:
    """A parsed requirement candidate before mechanical normalization."""

    text: str
    parent_index: int | None = None
    depends_on_indices: list[int] = field(default_factory=list)
    concern_value: int = 3
    layer: str = "Product"


@dataclass(slots=True)
class RequirementRecord:
    """A normalized requirement ready for persistence."""

    id: str
    text: str
    embedding: list[float]
    layer: str
    concern_value: int
    state: str
    version: int
    timestamp: int
    source_id: str
    parent_id: str | None = None
    depends_on_ids: list[str] = field(default_factory=list)
    supersedes_id: str | None = None


@dataclass(slots=True)
class AuditIssue:
    """A structural issue detected during the mechanical audit."""

    code: str
    message: str
    requirement_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IngestResult:
    """Summary returned after an ingest run."""

    source: SourceRecord
    requirements: list[RequirementRecord]
    issues: list[AuditIssue]
