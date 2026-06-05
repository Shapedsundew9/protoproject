"""Deterministic requirement candidate parsing."""

from __future__ import annotations

from .models import RequirementDraft


def parse_requirement_text(raw_text: str) -> list[RequirementDraft]:
    """Convert a raw text document into flat requirement candidates.

    The parser is intentionally mechanical: it treats headings and bullet-like
    lines as requirement candidates and preserves a simple parent index based on
    indentation.
    """

    drafts: list[RequirementDraft] = []
    stack: list[tuple[int, int]] = []

    for line in raw_text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue

        indent = len(stripped) - len(stripped.lstrip())
        text = _normalize_line(stripped)
        if not text:
            continue

        if _is_noise(text):
            continue

        parent_index = None
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            parent_index = stack[-1][1]

        draft = RequirementDraft(text=text, parent_index=parent_index)
        drafts.append(draft)
        stack.append((indent, len(drafts) - 1))

    return drafts


def _normalize_line(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith(("- ", "* ", "+ ")):
        stripped = stripped[2:].strip()
    if stripped and stripped[0].isdigit():
        parts = stripped.split(maxsplit=1)
        if len(parts) == 2 and parts[0].rstrip(".").isdigit():
            stripped = parts[1].strip()
    return stripped


def _is_noise(text: str) -> bool:
    lowered = text.lower()
    return lowered in {"---", "***"} or lowered.startswith("#")
