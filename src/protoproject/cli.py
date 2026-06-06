"""Command-line entrypoint for Phase 1."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from copilot import CopilotClient
from copilot.client import StopError

from .ingest import ingest_file
from .parser import _mechanical_parse_fallback
from .progress import IngestProgressEvent, LLMUsageSummary, format_usage_summary
from .refinement import build_review
from .tui import IngestReviewApp
from .validator import ValidationContext, build_source_record, normalize_requirements


class CliProgressReporter:
    def __init__(self, stream) -> None:
        self._stream = stream
        self.last_event: IngestProgressEvent | None = None
        self._active_inline_stage: str | None = None
        self._last_line_length = 0

    def __call__(self, event: IngestProgressEvent) -> None:
        self.last_event = event
        if self._should_render_inline(event):
            self._write_inline(self._format_inline_event(event))
            if event.status in {"completed", "fallback", "error", "cancelled"}:
                self._finish_inline()
                if event.stage == "llm_parse" and event.usage is not None:
                    print(self._format_event(event), file=self._stream, flush=True)
            else:
                self._active_inline_stage = event.stage
            return

        if self._active_inline_stage is not None:
            self._finish_inline()

        print(self._format_event(event), file=self._stream, flush=True)

    def interruption_message(self) -> str:
        if self.last_event is None:
            return "Ingest interrupted."
        return f"Ingest interrupted during {self.last_event.stage.replace('_', ' ')}."

    def close(self) -> None:
        if self._active_inline_stage is not None:
            self._finish_inline()

    def _format_event(self, event: IngestProgressEvent) -> str:
        stage = event.stage.replace("_", " ")
        parts = [f"[ingest:{stage}] {event.message}"]
        if event.current is not None and event.total is not None:
            parts.append(f"{event.current}/{event.total}")
        if event.elapsed_seconds is not None:
            parts.append(f"{event.elapsed_seconds:.1f}s")
        if event.usage is not None and event.stage == "llm_parse":
            parts.append(format_usage_summary(event.usage))
        return " | ".join(parts)

    def _format_inline_event(self, event: IngestProgressEvent) -> str:
        stage = event.stage.replace("_", " ")
        if event.current is not None and event.total is not None and event.total > 0:
            percentage = int(round((event.current / event.total) * 100))
            return (
                f"[ingest:{stage}] {self._progress_bar(event.current, event.total)} "
                f"{event.current}/{event.total} {percentage}%"
            )

        spinner = self._spinner_frame(event)
        timer = ""
        if event.elapsed_seconds is not None:
            timer = f" {event.elapsed_seconds:.1f}s"
        return f"[ingest:{stage}] {spinner}{timer} {event.message}".rstrip()

    def _should_render_inline(self, event: IngestProgressEvent) -> bool:
        if not self._is_tty():
            return False
        if event.current is not None and event.total is not None:
            return True
        return event.stage == "llm_parse" and event.status in {
            "started",
            "progress",
            "completed",
            "fallback",
            "error",
            "cancelled",
        }

    def _write_inline(self, text: str) -> None:
        padded_text = text
        if self._last_line_length > len(text):
            padded_text = text + (" " * (self._last_line_length - len(text)))
        self._stream.write(f"\r{padded_text}")
        self._stream.flush()
        self._last_line_length = len(text)

    def _finish_inline(self) -> None:
        self._stream.write("\n")
        self._stream.flush()
        self._active_inline_stage = None
        self._last_line_length = 0

    def _is_tty(self) -> bool:
        isatty = getattr(self._stream, "isatty", None)
        if callable(isatty):
            return bool(isatty())
        return False

    def _progress_bar(self, current: int, total: int, width: int = 16) -> str:
        ratio = min(max(current / total, 0.0), 1.0)
        filled = round(ratio * width)
        return f"[{('#' * filled) + ('-' * (width - filled))}]"

    def _spinner_frame(self, event: IngestProgressEvent) -> str:
        if event.status in {"completed", "fallback"}:
            return "done"
        if event.status in {"error", "cancelled"}:
            return "stop"
        frames = ("|", "/", "-", "\\")
        if event.elapsed_seconds is None:
            return frames[0]
        return frames[int(event.elapsed_seconds) % len(frames)]


def _usage_summary_line(usage: LLMUsageSummary | None) -> str:
    if usage is None:
        return "mechanical parser"
    return format_usage_summary(usage)


def _load_requirement_from_text(raw_text: str):
    from .embeddings import SentenceTransformerProvider  # noqa: PLC0415

    source = build_source_record(raw_text)
    drafts = [d for d in _mechanical_parse_fallback(raw_text) if d.text]
    requirements = normalize_requirements(
        drafts,
        ValidationContext(
            source=source, embedding_provider=SentenceTransformerProvider()
        ),
    )
    if not requirements:
        raise SystemExit("No requirements found in the supplied text.")
    return requirements[0]


def _build_arg_parser():
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="protoproject")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_p = subparsers.add_parser("ingest", help="Ingest a raw text file")
    ingest_p.add_argument("path", type=Path)
    ingest_p.add_argument(
        "--transcript",
        nargs="?",
        const=Path("transcript.log"),
        type=Path,
        default=None,
        help=(
            "Record LLM request/response transcript. "
            "Optionally provide a file path (defaults to ./transcript.log)."
        ),
    )
    ingest_p.add_argument(
        "--no-tui",
        action="store_true",
        help="Print a plain-text summary instead of launching the TUI",
    )

    review_p = subparsers.add_parser(
        "review", help="Review a requirement from a raw text file"
    )
    review_p.add_argument("path", type=Path)
    return parser


async def _run_ingest(
    path: Path,
    plain: bool,
    transcript: Path | None = None,
) -> int:
    token = os.getenv("COPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    copilot_client = None
    reporter = CliProgressReporter(sys.stderr)

    if token:
        try:
            copilot_client = CopilotClient(github_token=token)
            await copilot_client.start()
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(
                f"[warn] Copilot client unavailable ({exc}); using mechanical parser.",
                file=sys.stderr,
            )
            copilot_client = None
    else:
        print(
            "[warn] No COPILOT_GITHUB_TOKEN/GITHUB_TOKEN found; using mechanical parser.",
            file=sys.stderr,
        )

    try:
        result = await ingest_file(
            path,
            copilot_client=copilot_client,
            progress=reporter,
            transcript=transcript,
        )
    except asyncio.CancelledError:
        reporter.close()
        print(f"[cancelled] {reporter.interruption_message()}", file=sys.stderr)
        raise
    finally:
        if copilot_client is not None:
            try:
                await copilot_client.stop()
            except* StopError:
                pass
    reporter.close()

    use_tui = not plain and sys.stdout.isatty()
    if use_tui:
        await IngestReviewApp(result).run_async()
    else:
        print(f"Source:               {result.source.id}")
        print(f"Requirements created: {len(result.requirements)}")
        print(f"Audit issues:         {len(result.issues)}")
        print(f"LLM usage:            {_usage_summary_line(result.llm_usage)}")
        for issue in result.issues:
            suffix = f" ({issue.requirement_id})" if issue.requirement_id else ""
            print(f"  [{issue.code}]{suffix}: {issue.message}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        try:
            return asyncio.run(
                _run_ingest(
                    args.path,
                    plain=args.no_tui,
                    transcript=args.transcript,
                )
            )
        except KeyboardInterrupt:
            return 130

    if args.command == "review":
        raw_text = args.path.read_text(encoding="utf-8")
        requirement = _load_requirement_from_text(raw_text)
        review = build_review(requirement)
        print(f"Requirement: {review.requirement.id}")
        print(f"Text: {review.requirement.text}")
        print(f"Concern: {review.requirement.concern_value}")
        print(f"Quality issues: {len(review.quality_issues)}")
        for issue in review.quality_issues:
            print(f"  [{issue.code}] {issue.severity}: {issue.message}")
        if review.proposal:
            print(f"Proposal: {review.proposal.proposed_text}")
            print(f"Suggested concern: {review.proposal.concern_value}")
        return 0

    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
