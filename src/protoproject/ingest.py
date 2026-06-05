"""High-level ingestion pipeline."""

from __future__ import annotations

from pathlib import Path

from .audit import audit_requirements
from .config import AppConfig, load_config
from .embeddings import HashEmbeddingProvider
from .models import IngestResult
from .parser import parse_requirement_text
from .store import Neo4jStore
from .validator import ValidationContext, build_source_record, normalize_requirements


def ingest_file(path: str | Path, config: AppConfig | None = None) -> IngestResult:
    """Run the Phase 1 pipeline for a single text file."""

    config = config or load_config()
    raw_text = Path(path).read_text(encoding="utf-8")
    source = build_source_record(raw_text, source_type="Markdown")
    drafts = parse_requirement_text(raw_text)
    provider = HashEmbeddingProvider(dimension=config.embedding_dimension)
    requirements = normalize_requirements(
        drafts,
        ValidationContext(source=source, embedding_provider=provider),
    )
    issues = audit_requirements(requirements)

    store = Neo4jStore(
        uri=config.neo4j_uri,
        username=config.neo4j_username,
        password=config.neo4j_password,
        embedding_dimension=config.embedding_dimension,
    )
    try:
        store.initialize_schema()
        store.persist_source(source)
        store.persist_requirements(requirements)
    finally:
        store.close()

    return IngestResult(source=source, requirements=requirements, issues=issues)
