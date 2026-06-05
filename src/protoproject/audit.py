"""Mechanical graph audit helpers."""

from __future__ import annotations

from collections import defaultdict, deque

from .models import AuditIssue, RequirementRecord


def audit_requirements(requirements: list[RequirementRecord]) -> list[AuditIssue]:
    """Check for basic structural issues before persistence."""

    issues: list[AuditIssue] = []
    requirements_by_id = {requirement.id: requirement for requirement in requirements}

    for requirement in requirements:
        if not requirement.text.strip():
            issues.append(
                AuditIssue(
                    code="EMPTY_TEXT",
                    message="Requirement text is empty.",
                    requirement_id=requirement.id,
                )
            )
        if requirement.parent_id and requirement.parent_id not in requirements_by_id:
            issues.append(
                AuditIssue(
                    code="MISSING_PARENT",
                    message="Requirement parent does not exist in the current ingest set.",
                    requirement_id=requirement.id,
                    details={"parent_id": requirement.parent_id},
                )
            )

    adjacency = defaultdict(list)
    indegree = defaultdict(int)
    for requirement in requirements:
        if requirement.parent_id:
            adjacency[requirement.parent_id].append(requirement.id)
            indegree[requirement.id] += 1

    queue = deque([req.id for req in requirements if indegree[req.id] == 0])
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for child_id in adjacency[node_id]:
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                queue.append(child_id)

    if visited != len(requirements):
        issues.append(
            AuditIssue(
                code="CYCLE_DETECTED",
                message="Requirement hierarchy contains a cycle.",
            )
        )

    return issues
