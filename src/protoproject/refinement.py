"""Refinement helpers for Session 2."""

from __future__ import annotations

from dataclasses import replace

from .models import RequirementRecord, ReviewResult
from .quality import propose_refinement, review_requirement


def build_review(requirement: RequirementRecord) -> ReviewResult:
    """Create a review bundle for a single requirement."""

    issues = review_requirement(requirement)
    proposal = propose_refinement(requirement, issues) if issues else None
    return ReviewResult(
        requirement=requirement, quality_issues=issues, proposal=proposal
    )


def apply_refinement(
    requirement: RequirementRecord,
    proposed_text: str,
    concern_value: int | None = None,
) -> RequirementRecord:
    """Create the next version of a requirement from a refinement decision."""

    return replace(
        requirement,
        text=proposed_text.strip(),
        concern_value=(
            requirement.concern_value if concern_value is None else concern_value
        ),
        version=requirement.version + 1,
        state="Draft",
        supersedes_id=requirement.id,
        parent_id=requirement.parent_id,
        depends_on_ids=list(requirement.depends_on_ids),
    )
