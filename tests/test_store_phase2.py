"""Integration tests for Phase 2 Neo4jStore methods.

Skipped automatically when NEO4J_URI is not set or Neo4j is unreachable.
Run with:
    pytest tests/test_store_phase2.py -v
"""

from __future__ import annotations

import os
import time
import unittest

_NEO4J_AVAILABLE = bool(os.getenv("NEO4J_URI") or os.getenv("NEO4J_PASSWORD"))


@unittest.skipUnless(_NEO4J_AVAILABLE, "NEO4J_URI/NEO4J_PASSWORD not set; skipping integration tests")
class TestStorePhase2(unittest.TestCase):
    """Integration tests for load_refinement_queue, mark_requirement_state, count_by_state."""

    @classmethod
    def setUpClass(cls) -> None:
        from protoproject.config import load_config
        from protoproject.store import Neo4jStore

        cfg = load_config()
        cls.store = Neo4jStore(
            uri=cfg.neo4j_uri,
            username=cfg.neo4j_username,
            password=cfg.neo4j_password,
            embedding_dimension=cfg.embedding_dimension,
        )
        cls.store.initialize_schema()

        # Seed a known test requirement.
        from protoproject.models import RequirementRecord, SourceRecord
        from protoproject.store import Neo4jStore

        source = SourceRecord(
            id="SRC-PHASE2-TEST",
            type="Test",
            hash="deadbeef" * 8,
            path="test_store_phase2.py",
            project_id="PROJ-PHASE2-TEST",
            content="Test source content for phase 2 integration tests.",
        )
        cls.store.persist_project("PROJ-PHASE2-TEST", name="Phase 2 Test Project")
        cls.store.persist_source(source)

        cls.test_req = RequirementRecord(
            id="REQ-PHASE2-TEST-0001",
            text="The system must satisfy the Phase 2 integration test.",
            embedding=[0.0] * cfg.embedding_dimension,
            layer="Product",
            concern_value=2,
            state="Draft",
            version=1,
            timestamp=int(time.time()),
            source_id="SRC-PHASE2-TEST",
            rationale="Required to validate Neo4j persistence for Phase 2.",
        )
        cls.store.persist_requirements([cls.test_req])

    @classmethod
    def tearDownClass(cls) -> None:
        # Clean up test data.
        with cls.store._driver.session() as session:
            session.run(
                "MATCH (r:Requirement) WHERE r.id STARTS WITH 'REQ-PHASE2-TEST' DETACH DELETE r"
            )
            session.run(
                "MATCH (s:Source {id: 'SRC-PHASE2-TEST'}) DETACH DELETE s"
            )
            session.run(
                "MATCH (p:Project {id: 'PROJ-PHASE2-TEST'}) DETACH DELETE p"
            )
        cls.store.close()

    def test_load_refinement_queue_returns_records(self) -> None:
        queue = self.store.load_refinement_queue(limit=1000)
        ids = [r.id for r in queue]
        self.assertIn(self.test_req.id, ids)
        # All returned records should be Draft or Under_Review.
        for req in queue:
            self.assertIn(req.state, ("Draft", "Under_Review"))

    def test_load_refinement_queue_under_review_first(self) -> None:
        """Mark test req as Under_Review and confirm it sorts before Draft nodes."""
        from protoproject.models import RequirementRecord
        import time as _time

        # Add a second Draft requirement.
        second = RequirementRecord(
            id="REQ-PHASE2-TEST-0002",
            text="The system must also pass the ordering test.",
            embedding=[0.0] * 384,
            layer="Product",
            concern_value=2,
            state="Draft",
            version=1,
            timestamp=int(_time.time()) + 100,  # later timestamp
            source_id="SRC-PHASE2-TEST",
            rationale="Ordering verification requirement.",
        )
        self.store.persist_requirements([second])

        # Mark the original as Under_Review.
        self.store.mark_requirement_state(self.test_req.id, "Under_Review")

        queue = self.store.load_refinement_queue(limit=1000)
        relevant = [r for r in queue if r.id in (self.test_req.id, second.id)]
        self.assertGreaterEqual(len(relevant), 2)
        first_relevant = next((r for r in queue if r.id in (self.test_req.id, second.id)), None)
        self.assertEqual(first_relevant.id, self.test_req.id)
        self.assertEqual(first_relevant.state, "Under_Review")

        # Restore state for other tests.
        self.store.mark_requirement_state(self.test_req.id, "Draft")

    def test_mark_requirement_state(self) -> None:
        self.store.mark_requirement_state(self.test_req.id, "Under_Review")
        # Confirm via queue (only Under_Review/Draft returned).
        queue = self.store.load_refinement_queue(limit=1000)
        match = next((r for r in queue if r.id == self.test_req.id), None)
        self.assertIsNotNone(match)
        self.assertEqual(match.state, "Under_Review")

        # Reset.
        self.store.mark_requirement_state(self.test_req.id, "Draft")

    def test_count_by_state(self) -> None:
        counts = self.store.count_by_state()
        self.assertIsInstance(counts, dict)
        # At minimum our seeded Draft requirement must appear.
        total = sum(counts.values())
        self.assertGreater(total, 0)
        self.assertIn("Draft", counts)

    def test_persist_and_load_project_node(self) -> None:
        """Project node exists and Source is linked via BELONGS_TO."""
        projects = self.store.list_projects()
        project_ids = [p["id"] for p in projects]
        self.assertIn("PROJ-PHASE2-TEST", project_ids)

        # Verify the Source node has a BELONGS_TO edge to the Project.
        with self.store._driver.session() as session:
            result = session.run(
                """
                MATCH (s:Source {id: 'SRC-PHASE2-TEST'})-[:BELONGS_TO]->(p:Project)
                RETURN p.id AS pid
                """
            )
            rows = list(result)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], "PROJ-PHASE2-TEST")

    def test_source_content_stored_verbatim(self) -> None:
        """Source node carries the verbatim content property."""
        with self.store._driver.session() as session:
            result = session.run(
                "MATCH (s:Source {id: 'SRC-PHASE2-TEST'}) RETURN s.content AS content"
            )
            rows = list(result)
        self.assertEqual(len(rows), 1)
        self.assertIn("Test source content", rows[0]["content"])

    def test_requirement_rationale_round_trips(self) -> None:
        """Rationale written during persist_requirements is readable back out."""
        queue = self.store.load_refinement_queue(limit=1000)
        match = next((r for r in queue if r.id == self.test_req.id), None)
        self.assertIsNotNone(match)
        self.assertEqual(
            match.rationale,
            "Required to validate Neo4j persistence for Phase 2.",
        )

    def test_requirement_belongs_to_project(self) -> None:
        """Requirement has a BELONGS_TO edge pointing to the Project node."""
        with self.store._driver.session() as session:
            result = session.run(
                """
                MATCH (r:Requirement {id: $req_id})-[:BELONGS_TO]->(p:Project)
                RETURN p.id AS pid
                """,
                req_id=self.test_req.id,
            )
            rows = list(result)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], "PROJ-PHASE2-TEST")


if __name__ == "__main__":
    unittest.main()
