"""Neo4j persistence for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import GraphDatabase

from .models import RequirementRecord, SourceRecord


@dataclass(slots=True)
class Neo4jStore:
    """Persistence layer for requirements and their source documents."""

    uri: str
    username: str
    password: str
    embedding_dimension: int = 32

    def __post_init__(self) -> None:
        self._driver = GraphDatabase.driver(
            self.uri, auth=(self.username, self.password)
        )

    def close(self) -> None:
        self._driver.close()

    def initialize_schema(self) -> None:
        """Create the constraints and vector index used by Phase 1."""

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

    def persist_source(self, source: SourceRecord) -> None:
        with self._driver.session() as session:
            session.run(
                """
                MERGE (s:Source {id: $id})
                SET s.type = $type,
                    s.hash = $hash,
                    s.text = $text
                """,
                id=source.id,
                type=source.type,
                hash=source.hash,
                text=source.text,
            )

    def persist_requirements(self, requirements: list[RequirementRecord]) -> None:
        with self._driver.session() as session:
            for requirement in requirements:
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
