"""Requirement candidate extraction from raw text.

Primary path: async LLM extraction via GitHub Copilot SDK.
Fallback path: deterministic line-based parsing (used when no client is supplied
or when called with fallback=True — suitable for tests and offline mode).
"""

from __future__ import annotations

import json
import logging
import re

from .models import RequirementDraft

log = logging.getLogger(__name__)

_PARSE_PROMPT = """\
Extract all distinct, atomic software requirements from the text below.
Return ONLY a JSON array. No markdown fences, no explanation.
Each element must have exactly these keys:
  "text"         – a complete, standalone requirement statement
  "layer"        – one of: Product, Architecture, Design, Implementation
  "concern_value"– integer 1 (AI-autonomous) to 5 (requires human approval)
  "parent_text"  – exact text of the direct parent requirement, or null

Input text:
"""


async def parse_requirement_text(
    raw_text: str,
    copilot_client=None,
    *,
    fallback: bool = False,
) -> list[RequirementDraft]:
    """Return requirement candidates extracted from *raw_text*.

    When *copilot_client* is ``None`` or *fallback* is ``True``, the
    deterministic mechanical parser is used instead of the LLM.
    """
    if fallback or copilot_client is None:
        return _mechanical_parse_fallback(raw_text)

    try:
        return await _llm_parse(raw_text, copilot_client)
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM parse failed (%s); using mechanical fallback.", exc)
        return _mechanical_parse_fallback(raw_text)


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

async def _llm_parse(raw_text: str, copilot_client) -> list[RequirementDraft]:
    from copilot import PermissionHandler  # noqa: PLC0415

    prompt = _PARSE_PROMPT + raw_text
    session = await copilot_client.create_session(
        model="auto",
        on_permission_request=PermissionHandler.approve_all,
        streaming=False,
    )
    response = await session.send_and_wait(prompt=prompt)
    response_text = _extract_text(response)
    items = _parse_json_response(response_text)
    return _items_to_drafts(items)


def _extract_text(response) -> str:
    """Pull a plain string out of whatever the SDK returns."""
    if hasattr(response, "text") and response.text:
        return response.text
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif hasattr(block, "text"):
                    parts.append(block.text)
            return "\n".join(parts)
    return str(response)


def _parse_json_response(text: str) -> list[dict]:
    # Strip optional markdown code fences
    cleaned = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")
    return data


def _items_to_drafts(items: list[dict]) -> list[RequirementDraft]:
    drafts: list[RequirementDraft] = []
    text_to_index: dict[str, int] = {}

    for item in items:
        text = str(item.get("text", "")).strip()
        if not text:
            continue

        parent_text = item.get("parent_text")
        parent_index = text_to_index.get(parent_text) if parent_text else None

        draft = RequirementDraft(
            text=text,
            parent_index=parent_index,
            layer=str(item.get("layer", "Product")),
            concern_value=int(item.get("concern_value", 3)),
        )
        drafts.append(draft)
        text_to_index[text] = len(drafts) - 1

    return drafts


# ---------------------------------------------------------------------------
# Mechanical fallback (deterministic, no AI)
# ---------------------------------------------------------------------------

def _mechanical_parse_fallback(raw_text: str) -> list[RequirementDraft]:
    """Convert a raw text document into flat requirement candidates.

    Treats headings and bullet-like lines as candidates; preserves parent
    index from indentation depth.
    """
    drafts: list[RequirementDraft] = []
    stack: list[tuple[int, int]] = []  # (indent_level, draft_index)

    for line in raw_text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue

        indent = len(stripped) - len(stripped.lstrip())
        text = _normalize_line(stripped)
        if not text or _is_noise(text):
            continue

        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent_index = stack[-1][1] if stack else None

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

