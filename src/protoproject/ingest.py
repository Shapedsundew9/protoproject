"""High-level ingestion pipeline."""

from __future__ import annotations

from pathlib import Path

from .audit import audit_requirements
from .config import AppConfig, load_config
from .embeddings import SentenceTransformerProvider
from .models import AuditIssue, IngestResult
from .parser import parse_requirement_text
from .store import Neo4jStore
from .validator import ValidationContext, build_source_record, normalize_requirements

_NEAR_DUPLICATE_THRESHOLD = 0.92
_NEAR_DUPLICATE_LIMIT = 5


async def ingest_file(
    path: str | Path,
    config: AppConfig | None = None,
    copilot_client=None,
) -> IngestResult:
    """Run the Phase 1 pipeline for a single text file.

    *copilot_client* is the GitHub Copilot SDK client used for LLM-based
    parsing.  When ``None``, the mechanical fallback parser is used.
    """
    config = config or load_config()
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8")

    source = build_source_record(raw_text, path=str(path))
    drafts = await parse_requirement_text(raw_text, copilot_client)
    provider = SentenceTransformerProvider()
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

        # Near-duplicate detection via vector similarity search
        for requirement in requirements:
            similar = store.find_similar(
                requirement.embedding,
                threshold=_NEAR_DUPLICATE_THRESHOLD,
                limit=_NEAR_DUPLICATE_LIMIT,
                exclude_id=requirement.id,
            )
            for match in similar:
                issues.append(
                    AuditIssue(
                        code="NEAR_DUPLICATE",
                        message=(
                            f"Requirement is semantically similar (score "
                            f"{match['score']:.3f}) to an existing node."
                        ),
                        requirement_id=requirement.id,
                        details={"similar_id": match["id"], "score": match["score"]},
                    )
                )
    finally:
        store.close()

    return IngestResult(source=source, requirements=requirements, issues=issues)

