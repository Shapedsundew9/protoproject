from __future__ import annotations

import asyncio
import unittest

from protoproject.audit import audit_requirements
from protoproject.embeddings import SentenceTransformerProvider
from protoproject.parser import _mechanical_parse_fallback, parse_requirement_text
from protoproject.validator import (
    ValidationContext,
    build_source_record,
    normalize_requirements,
)

_FAKE_EMBED = [0.0] * 384


def _mock_provider():
    """Return a SentenceTransformerProvider whose embed_text is patched."""
    provider = SentenceTransformerProvider()
    provider.embed_text = lambda text: _FAKE_EMBED  # type: ignore[method-assign]
    return provider


class Phase1PipelineTests(unittest.TestCase):
    def test_parse_normalize_and_audit(self) -> None:
        raw_text = """# Requirements
- The system must store requirements.
  - The system must store a source hash.
- The system must trace versions.
"""
        drafts = _mechanical_parse_fallback(raw_text)
        self.assertEqual(len(drafts), 3)

        source = build_source_record(raw_text)
        requirements = normalize_requirements(
            drafts,
            ValidationContext(source=source, embedding_provider=_mock_provider()),
        )

        self.assertEqual(len(requirements), 3)
        self.assertEqual(requirements[1].parent_id, requirements[0].id)
        self.assertTrue(all(len(req.embedding) == 384 for req in requirements))

        issues = audit_requirements(requirements)
        self.assertEqual(issues, [])

    def test_parser_ignores_blank_lines(self) -> None:
        drafts = _mechanical_parse_fallback("\n\nThe system must work.\n\n")
        self.assertEqual(len(drafts), 1)

    def test_async_parser_uses_fallback_without_client(self) -> None:
        """parse_requirement_text falls back to mechanical parse when no client."""
        raw_text = "- The system must work.\n- The system must scale.\n"
        drafts = asyncio.run(parse_requirement_text(raw_text))
        self.assertEqual(len(drafts), 2)

    def test_depends_on_cycle_detected(self) -> None:
        source = build_source_record("test")
        from protoproject.models import RequirementRecord  # noqa: PLC0415

        req_a = RequirementRecord(
            id="REQ-A",
            text="Requirement A",
            embedding=_FAKE_EMBED,
            layer="Product",
            concern_value=3,
            state="Draft",
            version=1,
            timestamp=0,
            source_id=source.id,
            depends_on_ids=["REQ-B"],
        )
        req_b = RequirementRecord(
            id="REQ-B",
            text="Requirement B",
            embedding=_FAKE_EMBED,
            layer="Product",
            concern_value=3,
            state="Draft",
            version=1,
            timestamp=0,
            source_id=source.id,
            depends_on_ids=["REQ-A"],
        )
        issues = audit_requirements([req_a, req_b])
        codes = {i.code for i in issues}
        self.assertIn("CYCLE_DETECTED", codes)

    def test_missing_dependency_flagged(self) -> None:
        source = build_source_record("test")
        from protoproject.models import RequirementRecord  # noqa: PLC0415

        req = RequirementRecord(
            id="REQ-A",
            text="Requirement A",
            embedding=_FAKE_EMBED,
            layer="Product",
            concern_value=3,
            state="Draft",
            version=1,
            timestamp=0,
            source_id=source.id,
            depends_on_ids=["REQ-MISSING"],
        )
        issues = audit_requirements([req])
        codes = {i.code for i in issues}
        self.assertIn("MISSING_DEPENDENCY", codes)


if __name__ == "__main__":
    unittest.main()
