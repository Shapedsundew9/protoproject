"""Progress and telemetry models for ingest and refinement runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

ProgressStatus = Literal[
    "started",
    "progress",
    "completed",
    "fallback",
    "error",
    "cancelled",
]


@dataclass(slots=True)
class LLMUsageSummary:
    """Best-effort request telemetry for a single LLM parse call."""

    model: str | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    input_chars: int = 0
    output_chars: int = 0
    duration_seconds: float | None = None
    time_to_first_token_seconds: float | None = None
    context_tokens: int | None = None
    token_limit: int | None = None


@dataclass(slots=True)
class IngestProgressEvent:
    """Structured progress update emitted during ingest."""

    stage: str
    status: ProgressStatus
    message: str
    current: int | None = None
    total: int | None = None
    elapsed_seconds: float | None = None
    usage: LLMUsageSummary | None = None


ProgressReporter = Callable[[IngestProgressEvent], None]


@dataclass(slots=True)
class RefineProgressEvent:
    """Structured progress update emitted during a refinement run."""

    stage: str  # "mark_under_review" | "evaluate" | "generate_proposal"
    #             | "apply_auto" | "human_review" | "commit"
    status: ProgressStatus
    requirement_id: str
    message: str
    action: str | None = None  # "stabilized" | "auto_refined" | "human_accepted" | "skipped"
    usage: LLMUsageSummary | None = None


AnyProgressEvent = IngestProgressEvent | RefineProgressEvent
AnyProgressReporter = Callable[[AnyProgressEvent], None]


def emit_progress(
    reporter: ProgressReporter | None,
    *,
    stage: str,
    status: ProgressStatus,
    message: str,
    current: int | None = None,
    total: int | None = None,
    elapsed_seconds: float | None = None,
    usage: LLMUsageSummary | None = None,
) -> None:
    """Send an IngestProgressEvent if a reporter is configured."""

    if reporter is None:
        return
    reporter(
        IngestProgressEvent(
            stage=stage,
            status=status,
            message=message,
            current=current,
            total=total,
            elapsed_seconds=elapsed_seconds,
            usage=usage,
        )
    )


def emit_refine_progress(
    reporter: AnyProgressReporter | None,
    *,
    stage: str,
    status: ProgressStatus,
    requirement_id: str,
    message: str,
    action: str | None = None,
    usage: LLMUsageSummary | None = None,
) -> None:
    """Send a RefineProgressEvent if a reporter is configured."""

    if reporter is None:
        return
    reporter(
        RefineProgressEvent(
            stage=stage,
            status=status,
            requirement_id=requirement_id,
            message=message,
            action=action,
            usage=usage,
        )
    )


def format_usage_summary(usage: LLMUsageSummary) -> str:
    """Render a compact, user-facing usage summary."""

    parts: list[str] = []
    if usage.model:
        parts.append(f"model {usage.model}")
    if usage.cost_usd is not None:
        parts.append(f"cost ${usage.cost_usd:.4f}")
    if usage.input_tokens is not None or usage.output_tokens is not None:
        input_tokens = usage.input_tokens if usage.input_tokens is not None else "?"
        output_tokens = usage.output_tokens if usage.output_tokens is not None else "?"
        parts.append(f"tokens in/out {input_tokens}/{output_tokens}")
    parts.append(f"chars in/out {usage.input_chars}/{usage.output_chars}")
    if usage.duration_seconds is not None:
        parts.append(f"duration {usage.duration_seconds:.1f}s")
    return " | ".join(parts)
