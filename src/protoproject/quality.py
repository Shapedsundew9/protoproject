"""Deterministic quality checks for refinement review."""

from __future__ import annotations

import re

from .models import QualityIssue, RefinementProposal, RequirementRecord

_VAGUE_PATTERNS = (
    r"\bfast\b",
    r"\bquickly\b",
    r"\buser-friendly\b",
    r"\beasily\b",
    r"\brobust\b",
    r"\bscalable\b",
    r"\bflexible\b",
    r"\bintuitive\b",
    r"\bseamless\b",
)


def review_requirement(requirement: RequirementRecord) -> list[QualityIssue]:
    """Return deterministic quality issues for a single requirement."""

    issues: list[QualityIssue] = []
    text = requirement.text.strip()
    lowered = text.lower()

    if len(text) < 12:
        issues.append(
            QualityIssue(
                code="TOO_SHORT",
                message="Requirement text is too short to evaluate clearly.",
                requirement_id=requirement.id,
                severity="high",
            )
        )

    if not any(token in lowered for token in ("must", "shall", "should", "will")):
        issues.append(
            QualityIssue(
                code="NO_MODAL_VERB",
                message="Requirement does not use a clear normative verb.",
                requirement_id=requirement.id,
                severity="high",
            )
        )

    if any(re.search(pattern, lowered) for pattern in _VAGUE_PATTERNS):
        issues.append(
            QualityIssue(
                code="VAGUE_LANGUAGE",
                message="Requirement contains vague language that is hard to verify.",
                requirement_id=requirement.id,
                severity="high",
            )
        )

    if len(text.split()) < 4:
        issues.append(
            QualityIssue(
                code="LOW_SPECIFICITY",
                message="Requirement lacks enough detail to support verification.",
                requirement_id=requirement.id,
                severity="medium",
            )
        )

    return issues


def propose_refinement(
    requirement: RequirementRecord,
    issues: list[QualityIssue],
) -> RefinementProposal:
    """Create a lightweight refinement suggestion from issue hints."""

    proposed_text = requirement.text.strip()
    if not proposed_text:
        proposed_text = "The system must be defined with measurable behavior."
    elif not re.search(r"\b(must|shall|should|will)\b", proposed_text, flags=re.I):
        proposed_text = (
            f"The system must {proposed_text[0].lower()}{proposed_text[1:]}"
            if len(proposed_text) > 1
            else f"The system must {proposed_text.lower()}"
        )

    if any(issue.code == "VAGUE_LANGUAGE" for issue in issues):
        proposed_text = _tighten_vague_text(proposed_text)

    concern_value = requirement.concern_value
    if any(issue.severity == "high" for issue in issues):
        concern_value = max(concern_value, 4)

    return RefinementProposal(
        requirement_id=requirement.id,
        original_text=requirement.text,
        proposed_text=proposed_text,
        concern_value=concern_value,
        issues=issues,
    )


def _tighten_vague_text(text: str) -> str:
    replacements = {
        "fast": "complete within a measurable threshold",
        "quickly": "complete within a measurable threshold",
        "user-friendly": "support the documented interaction flow",
        "easily": "with a documented procedure",
        "robust": "meet the defined failure-handling criteria",
        "scalable": "support the defined load target",
        "flexible": "support the defined configuration options",
        "intuitive": "match the defined interaction steps",
        "seamless": "without unhandled interruption",
    }
    tightened = text
    for source, target in replacements.items():
        tightened = re.sub(rf"\b{re.escape(source)}\b", target, tightened, flags=re.I)
    return tightened
