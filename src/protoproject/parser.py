"""Requirement candidate extraction from raw text.

Primary path: async LLM extraction via GitHub Copilot SDK.
Fallback path: deterministic line-based parsing (used when no client is supplied
or when called with fallback=True — suitable for tests and offline mode).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from .models import RequirementDraft
from .progress import LLMUsageSummary, ProgressReporter, emit_progress

if TYPE_CHECKING:
    from copilot.generated.session_events import AssistantUsageData


_NumericT = TypeVar("_NumericT", int, float)

log = logging.getLogger(__name__)

_LLM_PARSE_STAGE = "llm_parse"
_LLM_HEARTBEAT_SECONDS = 0.2
_LLM_WAIT_TIMEOUT_SECONDS = 120.0

_PARSE_PROMPT = """\
Extract all distinct, atomic software requirements from the text below.
Return ONLY a JSON array. No markdown fences, no explanation.
Each element must have exactly these keys:
  "text"         – a complete, standalone requirement statement
  "layer"        – one of: Product, Architecture, Design, Implementation
  "concern_value"– integer 1 (AI-autonomous) to 5 (requires human approval)
  "parent_text"  – exact text of the direct parent requirement, or null
  "rationale"    – a concise explanation of *why* this requirement exists (the
                   business or engineering reason); must not be empty

Input text:
"""


def parse_requirement_text(
    raw_text: str,
    copilot_client=None,
    *,
    fallback: bool = False,
    default_rationale: str = "",
    progress: ProgressReporter | None = None,
    on_llm_usage: Callable[[LLMUsageSummary], None] | None = None,
    transcript: str | Path | None = None,
) -> list[RequirementDraft]:
    """Return requirement candidates extracted from *raw_text*.

    When *copilot_client* is ``None`` or *fallback* is ``True``, the
    deterministic mechanical parser is used instead of the LLM.
    *default_rationale* is stamped on every draft produced by the mechanical
    fallback (the LLM populates rationale itself from the prompt).
    """
    if fallback or copilot_client is None:
        message = (
            "Mechanical parser requested."
            if fallback
            else "No Copilot client available; using mechanical parser."
        )
        _reset_transcript(transcript)
        reason = "requested" if fallback else "no_copilot_client"
        _append_transcript_block(
            transcript,
            "FALLBACK",
            f"reason={reason}\n{message}",
        )
        emit_progress(
            progress,
            stage=_LLM_PARSE_STAGE,
            status="fallback",
            message=message,
        )
        return _mechanical_parse_fallback(raw_text, default_rationale=default_rationale)

    try:
        return asyncio.run(
            _llm_parse(
                raw_text,
                copilot_client,
                progress=progress,
                on_llm_usage=on_llm_usage,
                transcript=transcript,
            )
        )
    except asyncio.CancelledError:
        emit_progress(
            progress,
            stage=_LLM_PARSE_STAGE,
            status="cancelled",
            message="Copilot parse cancelled.",
        )
        raise
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        log.warning("LLM parse failed (%s); using mechanical fallback.", exc)
        _append_transcript_block(
            transcript,
            "FALLBACK",
            f"reason=llm_exception\nerror={exc}",
        )
        emit_progress(
            progress,
            stage=_LLM_PARSE_STAGE,
            status="fallback",
            message=f"LLM parse failed ({exc}); using mechanical fallback.",
        )
        return _mechanical_parse_fallback(raw_text, default_rationale=default_rationale)



# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------


async def _llm_parse(
    raw_text: str,
    copilot_client,
    *,
    progress: ProgressReporter | None = None,
    on_llm_usage: Callable[[LLMUsageSummary], None] | None = None,
    transcript: str | Path | None = None,
) -> list[RequirementDraft]:
    from copilot import PermissionHandler  # noqa: PLC0415
    from copilot.generated.session_events import (  # noqa: PLC0415
        AssistantMessageData,
        AssistantUsageData,
        SessionUsageInfoData,
    )

    prompt = _PARSE_PROMPT + raw_text
    _reset_transcript(transcript)
    if hasattr(copilot_client, "start"):
        await copilot_client.start()
    try:
        session = await copilot_client.create_session(
            model="auto",
            on_permission_request=PermissionHandler.approve_all,
            streaming=False,
        )
        usage = LLMUsageSummary(input_chars=len(prompt))
        started_at = time.perf_counter()

        def handle_session_event(event) -> None:
            match event.data:
                case AssistantMessageData() as data:
                    if usage.model is None and data.model:
                        usage.model = data.model
                case AssistantUsageData() as data:
                    _merge_usage_summary(usage, data)
                case SessionUsageInfoData() as data:
                    usage.context_tokens = data.current_tokens
                    usage.token_limit = data.token_limit

        emit_progress(
            progress,
            stage=_LLM_PARSE_STAGE,
            status="started",
            message="Copilot parse request in progress.",
        )
        unsubscribe = session.on(handle_session_event)
        heartbeat_task = asyncio.create_task(
            _emit_llm_wait_heartbeats(progress, usage, started_at)
        )

        try:
            _append_transcript_block(
                transcript,
                "REQUEST",
                (
                    "stage=llm_parse\n"
                    "session_model=auto\n"
                    f"prompt_chars={len(prompt)}\n\n"
                    "prompt:\n"
                    f"{prompt}"
                ),
            )
            response = await session.send_and_wait(
                prompt=prompt,
                timeout=_LLM_WAIT_TIMEOUT_SECONDS,
            )
        finally:
            unsubscribe()
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        response_text = _extract_text(response)
        usage.output_chars = len(response_text)
        _append_transcript_block(
            transcript,
            "RESPONSE",
            (
                f"response_chars={len(response_text)}\n"
                f"model={usage.model}\n"
                f"cost_usd={usage.cost_usd}\n"
                f"input_tokens={usage.input_tokens}\n"
                f"output_tokens={usage.output_tokens}\n"
                f"cache_read_tokens={usage.cache_read_tokens}\n"
                f"cache_write_tokens={usage.cache_write_tokens}\n"
                f"reasoning_tokens={usage.reasoning_tokens}\n"
                f"duration_seconds={usage.duration_seconds}\n"
                f"time_to_first_token_seconds={usage.time_to_first_token_seconds}\n"
                f"context_tokens={usage.context_tokens}\n"
                f"token_limit={usage.token_limit}\n\n"
                "response:\n"
                f"{response_text}"
            ),
        )
        if on_llm_usage is not None:
            on_llm_usage(usage)
        emit_progress(
            progress,
            stage=_LLM_PARSE_STAGE,
            status="completed",
            message="Copilot parse completed.",
            elapsed_seconds=time.perf_counter() - started_at,
            usage=usage,
        )
        items = _parse_json_response(response_text)
        return _items_to_drafts(items)
    finally:
        if hasattr(copilot_client, "stop"):
            from copilot.client import StopError  # noqa: PLC0415
            try:
                await copilot_client.stop()
            except* StopError:
                pass


def _merge_usage_summary(usage: LLMUsageSummary, data: AssistantUsageData) -> None:
    usage.model = data.model or usage.model
    usage.cost_usd = _add_nullable(usage.cost_usd, data.cost)
    usage.input_tokens = _add_nullable(usage.input_tokens, data.input_tokens)
    usage.output_tokens = _add_nullable(usage.output_tokens, data.output_tokens)
    usage.cache_read_tokens = _add_nullable(
        usage.cache_read_tokens, data.cache_read_tokens
    )
    usage.cache_write_tokens = _add_nullable(
        usage.cache_write_tokens, data.cache_write_tokens
    )
    usage.reasoning_tokens = _add_nullable(
        usage.reasoning_tokens, data.reasoning_tokens
    )
    usage.duration_seconds = _add_nullable(
        usage.duration_seconds,
        data.duration.total_seconds() if data.duration is not None else None,
    )
    if (
        usage.time_to_first_token_seconds is None
        and data.time_to_first_token is not None
    ):
        usage.time_to_first_token_seconds = data.time_to_first_token.total_seconds()


async def _emit_llm_wait_heartbeats(
    progress: ProgressReporter | None,
    usage: LLMUsageSummary,
    started_at: float,
) -> None:
    while True:
        await asyncio.sleep(_LLM_HEARTBEAT_SECONDS)
        context_suffix = ""
        if usage.context_tokens is not None and usage.token_limit is not None:
            context_suffix = (
                f" Context {usage.context_tokens}/{usage.token_limit} tokens."
            )
        emit_progress(
            progress,
            stage=_LLM_PARSE_STAGE,
            status="progress",
            message=f"Copilot parse still in progress.{context_suffix}",
            elapsed_seconds=time.perf_counter() - started_at,
        )


def _add_nullable(
    current: _NumericT | None, value: _NumericT | None
) -> _NumericT | None:
    if value is None:
        return current
    if current is None:
        return value
    return current + value


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_transcript_path(transcript: str | Path | None) -> Path | None:
    if transcript is None:
        return None
    return Path(transcript)


def _reset_transcript(transcript: str | Path | None) -> None:
    path = _resolve_transcript_path(transcript)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except OSError as exc:
        log.warning("Transcript reset failed for %s (%s).", path, exc)


def _append_transcript_block(
    transcript: str | Path | None,
    title: str,
    content: str,
) -> None:
    path = _resolve_transcript_path(transcript)
    if path is None:
        return

    block = f"=== {title} @ {_timestamp_utc()} ===\n" f"{content}\n\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
    except OSError as exc:
        log.warning("Transcript write failed for %s (%s).", path, exc)


def _extract_text(response) -> str:
    """Pull a plain string out of whatever the SDK returns."""
    if response is None:
        return ""
    if hasattr(response, "data"):
        return _extract_text(response.data)
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
            rationale=str(item.get("rationale", "")).strip(),
        )
        drafts.append(draft)
        text_to_index[text] = len(drafts) - 1

    return drafts


# ---------------------------------------------------------------------------
# Mechanical fallback (deterministic, no AI)
# ---------------------------------------------------------------------------


def _mechanical_parse_fallback(
    raw_text: str,
    *,
    default_rationale: str = "",
) -> list[RequirementDraft]:
    """Convert a raw text document into flat requirement candidates.

    Treats headings and bullet-like lines as candidates; preserves parent
    index from indentation depth.
    *default_rationale* is stamped on every produced draft; when the LLM
    fallback is used, rationale will be empty (triggering a MISSING_RATIONALE
    audit issue).
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

        draft = RequirementDraft(
            text=text,
            parent_index=parent_index,
            rationale=default_rationale,
        )
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
