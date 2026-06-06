from __future__ import annotations

import unittest
from typing import Any

from protoproject.models import RequirementRecord
from protoproject.quality import propose_refinement, review_requirement
from protoproject.refinement import apply_refinement, build_review

_EMBED = [0.0] * 384


def _req(**kwargs: Any) -> RequirementRecord:
    defaults: dict[str, Any] = dict(
        id="REQ-1",
        text="The system must be fast.",
        embedding=_EMBED,
        layer="Product",
        concern_value=3,
        state="Draft",
        version=1,
        timestamp=1,
        source_id="SRC-1",
    )
    defaults.update(kwargs)
    return RequirementRecord(**defaults)


class Session2Tests(unittest.TestCase):
    def test_quality_review_flags_vague_text(self) -> None:
        requirement = _req()
        issues = review_requirement(requirement)
        self.assertTrue(any(issue.code == "VAGUE_LANGUAGE" for issue in issues))

        proposal = propose_refinement(requirement, issues)
        self.assertIn("measurable threshold", proposal.proposed_text)

    def test_apply_refinement_creates_new_version(self) -> None:
        requirement = _req()
        revised = apply_refinement(
            requirement, "The system must complete within a measurable threshold.", 4
        )
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.supersedes_id, "REQ-1")
        self.assertEqual(revised.concern_value, 4)

    def test_build_review_returns_proposal(self) -> None:
        requirement = _req()
        review = build_review(requirement)
        self.assertIsNotNone(review.proposal)
        self.assertGreaterEqual(len(review.quality_issues), 1)


if __name__ == "__main__":
    unittest.main()
