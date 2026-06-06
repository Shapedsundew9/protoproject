"""Mechanical graph audit helpers."""

from __future__ import annotations

from collections import defaultdict, deque

from .models import AuditIssue, RequirementRecord


def audit_requirements(requirements: list[RequirementRecord]) -> list[AuditIssue]:
    """Check for basic structural issues before persistence.

    Detects:
    - Empty requirement text
    - Missing parent references
    - Missing dependency references
    - Cycles in the combined parent + depends_on graph
    """

    issues: list[AuditIssue] = []
    requirements_by_id = {r.id: r for r in requirements}

    for req in requirements:
        if not req.text.strip():
            issues.append(
                AuditIssue(
                    code="EMPTY_TEXT",
                    message="Requirement text is empty.",
                    requirement_id=req.id,
                )
            )
        if req.parent_id and req.parent_id not in requirements_by_id:
            issues.append(
                AuditIssue(
                    code="MISSING_PARENT",
                    message="Requirement parent does not exist in the current ingest set.",
                    requirement_id=req.id,
                    details={"parent_id": req.parent_id},
                )
            )
        for dep_id in req.depends_on_ids:
            if dep_id not in requirements_by_id:
                issues.append(
                    AuditIssue(
                        code="MISSING_DEPENDENCY",
                        message="Dependency target does not exist in the current ingest set.",
                        requirement_id=req.id,
                        details={"dependency_id": dep_id},
                    )
                )

    # Cycle detection over the combined parent + depends_on graph.
    # Edge direction: prerequisite → dependent (process prerequisites first).
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = defaultdict(int)

    for req in requirements:
        # Parent must be "processed" before child.
        if req.parent_id and req.parent_id in requirements_by_id:
            adjacency[req.parent_id].append(req.id)
            indegree[req.id] += 1
        # Dependency must be satisfied before the dependent.
        for dep_id in req.depends_on_ids:
            if dep_id in requirements_by_id:
                adjacency[dep_id].append(req.id)
                indegree[req.id] += 1

    queue = deque(req.id for req in requirements if indegree[req.id] == 0)
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
                message="Requirement graph contains a cycle (parent or depends_on edges).",
            )
        )

    return issues

