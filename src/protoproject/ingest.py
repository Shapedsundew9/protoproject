"""High-level ingestion pipeline."""

from __future__ import annotations

from pathlib import Path

from .audit import audit_requirements
from .config import AppConfig, load_config
from .embeddings import SentenceTransformerProvider
from .models import AuditIssue, IngestResult
from .parser import parse_requirement_text
from .progress import ProgressReporter, emit_progress
from .store import Neo4jStore
from .validator import ValidationContext, build_source_record, normalize_requirements

_NEAR_DUPLICATE_THRESHOLD = 0.92
_NEAR_DUPLICATE_LIMIT = 5


def ingest_file(
    path: str | Path,
    config: AppConfig | None = None,
    copilot_client=None,
    progress: ProgressReporter | None = None,
    transcript: str | Path | None = None,
) -> IngestResult:
    """Run the Phase 1 pipeline for a single text file.

    *copilot_client* is the GitHub Copilot SDK client used for LLM-based
    parsing.  When ``None``, the mechanical fallback parser is used.
    """
    config = config or load_config()
    path = Path(path)
    emit_progress(
        progress,
        stage="read_source",
        status="started",
        message=f"Reading source file {path}.",
    )
    raw_text = path.read_text(encoding="utf-8")
    emit_progress(
        progress,
        stage="read_source",
        status="completed",
        message=f"Read source file {path} ({len(raw_text)} chars).",
    )

    source = build_source_record(raw_text, path=str(path))
    emit_progress(
        progress,
        stage="build_source",
        status="completed",
        message=f"Built source record {source.id}.",
    )

    llm_usage = None

    def capture_llm_usage(summary) -> None:
        nonlocal llm_usage
        llm_usage = summary

    drafts = parse_requirement_text(
        raw_text,
        copilot_client,
        progress=progress,
        on_llm_usage=capture_llm_usage,
        transcript=transcript,
    )
    emit_progress(
        progress,
        stage="parse_requirements",
        status="completed",
        message=f"Prepared {len(drafts)} requirement drafts.",
        usage=llm_usage,
    )
    provider = SentenceTransformerProvider(progress=progress)
    emit_progress(
        progress,
        stage="embed_requirements",
        status="started",
        message=f"Normalizing and embedding {len(drafts)} requirements.",
    )
    requirements = normalize_requirements(
        drafts,
        ValidationContext(source=source, embedding_provider=provider),
        progress=progress,
    )
    emit_progress(
        progress,
        stage="embed_requirements",
        status="completed",
        message=f"Created {len(requirements)} normalized requirements.",
    )

    emit_progress(
        progress,
        stage="audit_requirements",
        status="started",
        message="Auditing requirement graph.",
    )
    issues = audit_requirements(requirements)
    emit_progress(
        progress,
        stage="audit_requirements",
        status="completed",
        message=f"Audit complete ({len(issues)} issues).",
    )

    store = Neo4jStore(
        uri=config.neo4j_uri,
        username=config.neo4j_username,
        password=config.neo4j_password,
        embedding_dimension=config.embedding_dimension,
        progress=progress,
    )
    try:
        store.initialize_schema()
        store.persist_source(source)
        store.persist_requirements(requirements)

        # Near-duplicate detection via vector similarity search
        total_requirements = len(requirements)
        emit_progress(
            progress,
            stage="similarity_scan",
            status="started",
            message=(
                f"Scanning {total_requirements} requirements for near-duplicates."
            ),
        )
        for index, requirement in enumerate(requirements, start=1):
            similar = store.find_similar(
                requirement.embedding,
                threshold=_NEAR_DUPLICATE_THRESHOLD,
                limit=_NEAR_DUPLICATE_LIMIT,
                exclude_id=requirement.id,
            )
            emit_progress(
                progress,
                stage="similarity_scan",
                status="progress",
                message=(
                    f"Scanned requirement {index} of {total_requirements} for near-duplicates."
                ),
                current=index,
                total=total_requirements,
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
        emit_progress(
            progress,
            stage="similarity_scan",
            status="completed",
            message="Near-duplicate scan complete.",
        )
    finally:
        store.close()

    emit_progress(
        progress,
        stage="complete",
        status="completed",
        message=(
            f"Ingest complete with {len(requirements)} requirements and {len(issues)} issues."
        ),
        usage=llm_usage,
    )
    return IngestResult(
        source=source,
        requirements=requirements,
        issues=issues,
        llm_usage=llm_usage,
    )
