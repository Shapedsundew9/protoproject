from __future__ import annotations

import unittest

from protoproject.audit import audit_requirements
from protoproject.embeddings import HashEmbeddingProvider
from protoproject.parser import parse_requirement_text
from protoproject.validator import (
    ValidationContext,
    build_source_record,
    normalize_requirements,
)


class Phase1PipelineTests(unittest.TestCase):
    def test_parse_normalize_and_audit(self) -> None:
        raw_text = """# Requirements
- The system must store requirements.
  - The system must store a source hash.
- The system must trace versions.
"""

        drafts = parse_requirement_text(raw_text)
        self.assertEqual(len(drafts), 3)

        source = build_source_record(raw_text)
        requirements = normalize_requirements(
            drafts,
            ValidationContext(
                source=source, embedding_provider=HashEmbeddingProvider()
            ),
        )

        self.assertEqual(len(requirements), 3)
        self.assertEqual(requirements[1].parent_id, requirements[0].id)
        self.assertTrue(
            all(len(requirement.embedding) == 32 for requirement in requirements)
        )

        issues = audit_requirements(requirements)
        self.assertEqual(issues, [])

    def test_parser_ignores_blank_lines(self) -> None:
        drafts = parse_requirement_text("\n\nThe system must work.\n\n")
        self.assertEqual(len(drafts), 1)


if __name__ == "__main__":
    unittest.main()
