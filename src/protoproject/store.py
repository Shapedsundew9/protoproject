"""Neo4j persistence for ProtoProject (Phase 1 and 2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from .models import RequirementRecord, SourceRecord
from .progress import ProgressReporter, emit_progress


@dataclass(slots=True)
class Neo4jStore:
    """Persistence layer for requirements and their source documents."""

    uri: str
    username: str
    password: str
    embedding_dimension: int = 32
    progress: ProgressReporter | None = None
    _driver: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._driver = GraphDatabase.driver(
            self.uri, auth=(self.username, self.password)
        )

    def close(self) -> None:
        self._driver.close()

    def initialize_schema(self) -> None:
        """Create the constraints and vector index used by Phase 1."""

        emit_progress(
            self.progress,
            stage="schema_init",
            status="started",
            message="Initializing Neo4j schema.",
        )
        statements = [
            "CREATE CONSTRAINT requirement_id IF NOT EXISTS FOR (r:Requirement) REQUIRE r.id IS UNIQUE",
            "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (s:Source) REQUIRE s.id IS UNIQUE",
            (
                "CREATE VECTOR INDEX requirement_embedding_index IF NOT EXISTS "
                "FOR (r:Requirement) ON (r.embedding) "
                f"OPTIONS {{indexConfig: {{`vector.dimensions`: {self.embedding_dimension}, `vector.similarity_function`: 'cosine'}}}}"
            ),
        ]
        with self._driver.session() as session:
            for statement in statements:
                session.run(statement)
        emit_progress(
            self.progress,
            stage="schema_init",
            status="completed",
            message="Neo4j schema ready.",
        )

    def persist_source(self, source: SourceRecord) -> None:
        emit_progress(
            self.progress,
            stage="persist_source",
            status="started",
            message=f"Persisting source {source.id}.",
        )
        with self._driver.session() as session:
            session.run(
                """
                MERGE (s:Source {id: $id})
                SET s.type = $type,
                    s.hash = $hash,
                    s.path = $path
                """,
                id=source.id,
                type=source.type,
                hash=source.hash,
                path=source.path,
            )
        emit_progress(
            self.progress,
            stage="persist_source",
            status="completed",
            message=f"Persisted source {source.id}.",
        )

    def persist_requirements(self, requirements: list[RequirementRecord]) -> None:
        total = len(requirements)
        emit_progress(
            self.progress,
            stage="persist_requirements",
            status="started",
            message=f"Persisting {total} requirements.",
        )
        with self._driver.session() as session:
            for index, requirement in enumerate(requirements, start=1):
                session.run(
                    """
                    MERGE (r:Requirement {id: $id})
                    SET r.text = $text,
                        r.embedding = $embedding,
                        r.layer = $layer,
                        r.concern_value = $concern_value,
                        r.state = $state,
                        r.version = $version,
                        r.timestamp = $timestamp,
                        r.supersedes_id = $supersedes_id
                    WITH r
                    MATCH (s:Source {id: $source_id})
                    MERGE (r)-[:ORIGINATED_FROM]->(s)
                    """,
                    id=requirement.id,
                    text=requirement.text,
                    embedding=requirement.embedding,
                    layer=requirement.layer,
                    concern_value=requirement.concern_value,
                    state=requirement.state,
                    version=requirement.version,
                    timestamp=requirement.timestamp,
                    supersedes_id=requirement.supersedes_id,
                    source_id=requirement.source_id,
                )
                emit_progress(
                    self.progress,
                    stage="persist_requirements",
                    status="progress",
                    message=f"Persisted requirement {index} of {total}.",
                    current=index,
                    total=total,
                )

            for requirement in requirements:
                if requirement.parent_id:
                    session.run(
                        """
                        MATCH (child:Requirement {id: $child_id})
                        MATCH (parent:Requirement {id: $parent_id})
                        MERGE (child)-[:CHILD_OF]->(parent)
                        """,
                        child_id=requirement.id,
                        parent_id=requirement.parent_id,
                    )
                for dependency_id in requirement.depends_on_ids:
                    session.run(
                        """
                        MATCH (child:Requirement {id: $child_id})
                        MATCH (dependency:Requirement {id: $dependency_id})
                        MERGE (child)-[:DEPENDS_ON]->(dependency)
                        """,
                        child_id=requirement.id,
                        dependency_id=dependency_id,
                    )
        emit_progress(
            self.progress,
            stage="persist_requirements",
            status="completed",
            message=f"Persisted {total} requirements and relationships.",
        )

    def persist_requirement_revision(self, revision: RequirementRecord) -> None:
        """Persist a refinement revision and mark the prior version chain."""

        with self._driver.session() as session:
            session.run(
                """
                MERGE (r:Requirement {id: $id})
                SET r.text = $text,
                    r.embedding = $embedding,
                    r.layer = $layer,
                    r.concern_value = $concern_value,
                    r.state = $state,
                    r.version = $version,
                    r.timestamp = $timestamp,
                    r.supersedes_id = $supersedes_id
                WITH r
                MATCH (s:Source {id: $source_id})
                MERGE (r)-[:ORIGINATED_FROM]->(s)
                """,
                id=revision.id,
                text=revision.text,
                embedding=revision.embedding,
                layer=revision.layer,
                concern_value=revision.concern_value,
                state=revision.state,
                version=revision.version,
                timestamp=revision.timestamp,
                supersedes_id=revision.supersedes_id,
                source_id=revision.source_id,
            )

            if revision.supersedes_id:
                session.run(
                    """
                    MATCH (current:Requirement {id: $current_id})
                    MATCH (previous:Requirement {id: $previous_id})
                    MERGE (current)-[:SUPERSEDES]->(previous)
                    SET previous.state = 'Superseded'
                    """,
                    current_id=revision.id,
                    previous_id=revision.supersedes_id,
                )

            if revision.parent_id:
                session.run(
                    """
                    MATCH (child:Requirement {id: $child_id})
                    MATCH (parent:Requirement {id: $parent_id})
                    MERGE (child)-[:CHILD_OF]->(parent)
                    """,
                    child_id=revision.id,
                    parent_id=revision.parent_id,
                )

            for dependency_id in revision.depends_on_ids:
                session.run(
                    """
                    MATCH (child:Requirement {id: $child_id})
                    MATCH (dependency:Requirement {id: $dependency_id})
                    MERGE (child)-[:DEPENDS_ON]->(dependency)
                    """,
                    child_id=revision.id,
                    dependency_id=dependency_id,
                )

    def find_similar(
        self,
        embedding: list[float],
        threshold: float = 0.92,
        limit: int = 10,
        exclude_id: str | None = None,
    ) -> list[dict]:
        """Return existing requirements whose embeddings are above *threshold*
        cosine similarity to *embedding*.

        Returns an empty list if the vector index is unavailable.
        """
        try:
            with self._driver.session() as session:
                result = session.run(
                    """
                    CALL db.index.vector.queryNodes(
                        'requirement_embedding_index', $limit, $embedding
                    ) YIELD node, score
                    WHERE score >= $threshold
                      AND ($exclude_id IS NULL OR node.id <> $exclude_id)
                    RETURN node.id AS id, node.text AS text, score
                    ORDER BY score DESC
                    """,
                    limit=limit + (1 if exclude_id else 0),
                    embedding=embedding,
                    threshold=threshold,
                    exclude_id=exclude_id,
                )
                return [
                    {"id": row["id"], "text": row["text"], "score": row["score"]}
                    for row in result
                ]
        except Neo4jError:
            # Vector index may not be available (e.g., Community edition without
            # the vector plugin, or schema not yet initialised).
            return []

    # ------------------------------------------------------------------
    # Phase 2 — refinement workflow helpers
    # ------------------------------------------------------------------

    def load_refinement_queue(self, limit: int = 100) -> list[RequirementRecord]:
        """Return requirements queued for refinement, Under_Review first then Draft.

        Under_Review nodes are prioritised so an interrupted session resumes
        from where it left off.  Draft nodes are ordered by timestamp (oldest
        first) so requirements are refined in ingestion order.
        """
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (r:Requirement)
                WHERE r.state IN ['Under_Review', 'Draft']
                OPTIONAL MATCH (r)-[:ORIGINATED_FROM]->(s:Source)
                RETURN r, s.id AS source_id
                ORDER BY
                    CASE r.state WHEN 'Under_Review' THEN 0 ELSE 1 END,
                    r.timestamp
                LIMIT $limit
                """,
                limit=limit,
            )
            records = []
            for row in result:
                node = row["r"]
                sid = row["source_id"] or node.get("source_id", "")
                records.append(
                    RequirementRecord(
                        id=node["id"],
                        text=node["text"],
                        embedding=list(node.get("embedding") or []),
                        layer=node.get("layer", "Product"),
                        concern_value=int(node.get("concern_value", 3)),
                        state=node["state"],
                        version=int(node.get("version", 1)),
                        timestamp=int(node.get("timestamp", 0)),
                        source_id=sid,
                        parent_id=node.get("parent_id"),
                        supersedes_id=node.get("supersedes_id"),
                        depends_on_ids=[],
                    )
                )
            return records

    def mark_requirement_state(self, req_id: str, state: str) -> None:
        """Set the state of a requirement node (crash-safe checkpoint write)."""
        with self._driver.session() as session:
            session.run(
                "MATCH (r:Requirement {id: $id}) SET r.state = $state",
                id=req_id,
                state=state,
            )

    def count_by_state(self) -> dict[str, int]:
        """Return a mapping of state → count across all Requirement nodes."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (r:Requirement) RETURN r.state AS state, count(*) AS n"
            )
            return {row["state"]: row["n"] for row in result}

