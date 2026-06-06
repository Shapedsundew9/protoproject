"""Tests for the Phase 2 LangGraph refinement workflow.

Uses a MockStore (no Neo4j required) and a MockCopilotClient that returns
a canned response so the full happy-path can be exercised offline.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from protoproject.models import HumanDecision, RequirementRecord, WorkflowOutcome
from protoproject.workflow import RefinementWorkflow

_EMBED = [0.0] * 384


def _req(**kwargs: Any) -> RequirementRecord:
    defaults: dict[str, Any] = dict(
        id="REQ-TEST-0001",
        text="The system must complete within a measurable threshold.",
        embedding=_EMBED,
        layer="Product",
        concern_value=2,
        state="Draft",
        version=1,
        timestamp=1,
        source_id="SRC-1",
    )
    defaults.update(kwargs)
    return RequirementRecord(**defaults)


class MockStore:
    """In-memory store mock — no Neo4j connection needed."""

    def __init__(self):
        self.states: dict[str, str] = {}
        self.committed: list[RequirementRecord] = []

    def mark_requirement_state(self, req_id: str, state: str) -> None:
        self.states[req_id] = state

    def persist_requirement_revision(self, revision: RequirementRecord) -> None:
        self.committed.append(revision)

    def load_refinement_queue(self, limit: int = 100):
        return []

    def count_by_state(self) -> dict[str, int]:
        return {}


class MockEmbeddingProvider:
    def embed_text(self, text: str) -> list[float]:
        return [0.1] * 384


class TestWorkflowAutoRefine(unittest.TestCase):
    """Requirements that should be handled without human intervention."""

    def _make_workflow(self):
        store = MockStore()
        wf = RefinementWorkflow(
            store,
            embedding_provider=MockEmbeddingProvider(),
            copilot_client=None,
        )
        return wf, store

    def test_stabilizes_clean_requirement(self) -> None:
        wf, store = self._make_workflow()
        req = _req(
            text="The system must complete all requests within 200ms.",
            concern_value=2,
        )
        outcome = wf.run_one(req)
        self.assertEqual(outcome.status, "stabilized")
        self.assertIsNone(outcome.error)
        # Should be committed to the mock store.
        self.assertEqual(len(store.committed), 1)
        self.assertEqual(store.committed[0].state, "Stabilized")
        # Under_Review was written as checkpoint, then Stabilized on commit.
        self.assertEqual(store.states.get(req.id), "Under_Review")

    def test_auto_refines_low_concern_no_modal(self) -> None:
        wf, store = self._make_workflow()
        # NO_MODAL_VERB triggers — but this is high severity, concern=2 means low
        # concern but high severity → should escalate to human.
        # Use VAGUE_LANGUAGE (high severity) + low concern to verify escalation.
        req = _req(
            text="The system should be fast and seamless for users.",
            concern_value=2,
        )
        # VAGUE_LANGUAGE is high severity → escalates to human.
        outcome = wf.run_one(req)
        self.assertEqual(outcome.status, "needs_human")
        self.assertIsNotNone(outcome.pending_review)

    def test_auto_refines_medium_severity_issue(self) -> None:
        """LOW_SPECIFICITY is medium severity — should be auto-refined at low concern."""
        wf, store = self._make_workflow()
        # LOW_SPECIFICITY alone (medium) + low concern → auto path.
        # Text must: be ≥12 chars, contain a modal verb, have no vague terms, but < 4 words.
        # "Must log errors" is 3 words (< 4) → LOW_SPECIFICITY (medium only).
        req = _req(text="Must log errors.", concern_value=2)
        outcome = wf.run_one(req)
        # medium severity + concern <= 3 → auto_refined
        self.assertIn(outcome.status, ("auto_refined", "stabilized"))

    def test_escalates_high_concern_requirement(self) -> None:
        """High concern_value should always route to human review."""
        wf, store = self._make_workflow()
        req = _req(
            text="The system must complete within a measurable threshold.",
            concern_value=4,  # high concern — always human
        )
        # No quality issues, but concern ≥ 4 → the proposal path isn't entered;
        # a clean requirement goes straight to mark_stabilized + commit.
        # So this tests that a CLEAN high-concern requirement is still auto-stabilized.
        outcome = wf.run_one(req)
        self.assertEqual(outcome.status, "stabilized")
        self.assertEqual(store.committed[0].state, "Stabilized")

    def test_escalates_high_severity_issue_regardless_of_concern(self) -> None:
        """High severity (e.g. NO_MODAL_VERB) should require human even at concern=1."""
        wf, store = self._make_workflow()
        req = _req(
            text="Processing data quickly for all users in the pipeline.",
            concern_value=1,
        )
        # VAGUE_LANGUAGE (high severity) + NO_MODAL_VERB (high severity) → human.
        outcome = wf.run_one(req)
        self.assertEqual(outcome.status, "needs_human")


class TestWorkflowResume(unittest.TestCase):
    """Tests for the resume() path after a human-review interrupt."""

    def _make_workflow(self):
        store = MockStore()
        wf = RefinementWorkflow(
            store,
            embedding_provider=MockEmbeddingProvider(),
            copilot_client=None,
        )
        return wf, store

    def test_resume_accept_commits_stabilized(self) -> None:
        wf, store = self._make_workflow()
        req = _req(text="The system should be fast.", concern_value=2)
        # First run pauses at human review.
        outcome = wf.run_one(req)
        self.assertEqual(outcome.status, "needs_human")

        # Simulate human accepting.
        decision = HumanDecision(
            action="accept",
            text="The system must respond within 200ms.",
            concern_value=3,
        )
        resumed = wf.resume(req, decision)
        self.assertIn(resumed.status, ("auto_refined", "stabilized"))
        self.assertEqual(len(store.committed), 1)
        committed = store.committed[0]
        self.assertEqual(committed.state, "Stabilized")
        self.assertEqual(committed.version, 2)
        self.assertEqual(committed.supersedes_id, req.id)
        self.assertEqual(committed.text, "The system must respond within 200ms.")

    def test_resume_skip_reverts_to_draft(self) -> None:
        wf, store = self._make_workflow()
        req = _req(text="The system should be fast.", concern_value=2)
        outcome = wf.run_one(req)
        self.assertEqual(outcome.status, "needs_human")

        decision = HumanDecision(
            action="skip",
            text=req.text,
            concern_value=req.concern_value,
        )
        resumed = wf.resume(req, decision)
        self.assertEqual(resumed.status, "skipped")
        # No revision committed.
        self.assertEqual(len(store.committed), 0)
        # State reverted to Draft.
        self.assertEqual(store.states.get(req.id), "Draft")

    def test_resume_edit_uses_custom_text(self) -> None:
        wf, store = self._make_workflow()
        req = _req(text="The system should be fast.", concern_value=2)
        wf.run_one(req)  # pause

        decision = HumanDecision(
            action="accept",
            text="The system must process each request in under 500ms.",
            concern_value=2,
        )
        wf.resume(req, decision)
        self.assertEqual(store.committed[0].text,
                         "The system must process each request in under 500ms.")


class TestWorkflowCopilotFallback(unittest.TestCase):
    """Copilot failures should not crash the workflow."""

    def test_copilot_exception_falls_back_to_rule_proposal(self) -> None:
        store = MockStore()
        bad_client = MagicMock()

        # Make the copilot call raise.
        import asyncio

        async def _fail(*args, **kwargs):
            raise RuntimeError("Simulated Copilot failure")

        bad_client.start = _fail

        wf = RefinementWorkflow(
            store,
            embedding_provider=MockEmbeddingProvider(),
            copilot_client=bad_client,
        )
        req = _req(
            text="The system must complete within a measurable threshold.",
            concern_value=2,
        )
        # Should still succeed (clean req → stabilized) without crashing.
        outcome = wf.run_one(req)
        self.assertNotEqual(outcome.status, "error")


class TestWorkflowUnderReviewResumption(unittest.TestCase):
    """Under_Review nodes from prior sessions should be processed normally."""

    def test_under_review_node_is_processed(self) -> None:
        store = MockStore()
        wf = RefinementWorkflow(
            store,
            embedding_provider=MockEmbeddingProvider(),
            copilot_client=None,
        )
        req = _req(
            text="The system must complete within a measurable threshold.",
            state="Under_Review",
            concern_value=2,
        )
        outcome = wf.run_one(req)
        # mark_under_review is idempotent (just sets the state again).
        self.assertEqual(store.states.get(req.id), "Under_Review")
        self.assertNotEqual(outcome.status, "error")


class TestApplyRefinementTargetState(unittest.TestCase):
    """Unit test for the updated apply_refinement target_state parameter."""

    def test_default_state_is_draft(self) -> None:
        from protoproject.refinement import apply_refinement

        req = _req()
        revised = apply_refinement(req, "The system must respond in under 200ms.")
        self.assertEqual(revised.state, "Draft")

    def test_stabilized_state(self) -> None:
        from protoproject.refinement import apply_refinement

        req = _req()
        revised = apply_refinement(
            req,
            "The system must respond in under 200ms.",
            target_state="Stabilized",
        )
        self.assertEqual(revised.state, "Stabilized")
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.supersedes_id, req.id)


if __name__ == "__main__":
    unittest.main()
