"""Command-line entrypoint for ProtoProject (Phase 1 and 2)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from copilot import CopilotClient
from copilot.client import StopError

from .config import load_config
from .ingest import ingest_file
from .models import HumanDecision
from .parser import _mechanical_parse_fallback
from .progress import (
    AnyProgressEvent,
    IngestProgressEvent,
    LLMUsageSummary,
    RefineProgressEvent,
    format_usage_summary,
)
from .refinement import build_review
from .store import Neo4jStore
from .tui import IngestReviewApp, ProtoProjectApp
from .validator import ValidationContext, build_source_record, normalize_requirements


class CliProgressReporter:
    def __init__(self, stream) -> None:
        self._stream = stream
        self.last_event: AnyProgressEvent | None = None
        self._active_inline_stage: str | None = None
        self._last_line_length = 0

    def __call__(self, event: AnyProgressEvent) -> None:
        self.last_event = event
        if isinstance(event, RefineProgressEvent):
            self._handle_refine_event(event)
            return
        # IngestProgressEvent path (original behaviour).
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

    def _handle_refine_event(self, event: RefineProgressEvent) -> None:
        if self._active_inline_stage is not None:
            self._finish_inline()
        action_tag = f" → {event.action}" if event.action else ""
        usage_tag = ""
        if event.usage is not None:
            usage_tag = f" | {format_usage_summary(event.usage)}"
        line = (
            f"[refine:{event.stage}] {event.requirement_id} | "
            f"{event.message}{action_tag}{usage_tag}"
        )
        print(line, file=self._stream, flush=True)


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
        "--project",
        metavar="PROJECT_ID",
        default=None,
        help=(
            "Project to assign the ingested requirements to. "
            "If omitted, an interactive picker is shown."
        ),
    )
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

    refine_p = subparsers.add_parser(
        "refine",
        help="Run the Phase 2 refinement workflow over Draft requirements in Neo4j",
    )
    refine_p.add_argument(
        "--limit",
        type=int,
        default=100,
        metavar="N",
        help="Maximum number of requirements to process in this session (default 100)",
    )
    refine_p.add_argument(
        "--req-id",
        metavar="ID",
        default=None,
        help="Refine a single requirement by ID (ignores --limit)",
    )
    refine_p.add_argument(
        "--no-tui",
        action="store_true",
        help="Use plain-text prompts instead of the interactive TUI for human review",
    )
    return parser


def _pick_project(
    plain: bool,
    config,
    *,
    preselected: str | None = None,
) -> str:
    """Resolve which project this ingest run belongs to.

    Resolution order:
    1. *preselected* — supplied via ``--project`` flag; used immediately.
    2. Interactive TUI / plain-text picker — queries Neo4j for existing projects
       and lets the user select one or enter a new name.

    Returns the chosen project ID (non-empty string).
    """
    if preselected:
        return preselected.strip()

    # Query Neo4j for existing projects.
    store = Neo4jStore(
        uri=config.neo4j_uri,
        username=config.neo4j_username,
        password=config.neo4j_password,
        embedding_dimension=config.embedding_dimension,
    )
    try:
        store.initialize_schema()
        projects = store.list_projects()
    finally:
        store.close()

    use_tui = not plain and sys.stdout.isatty()

    if use_tui:
        from .tui import ProjectPickerApp  # noqa: PLC0415
        chosen = ProjectPickerApp(projects=projects).run()
        if chosen:
            return chosen
        # User closed the TUI without selecting — fall through to plain-text.

    # Plain-text fallback.
    print("\n── Project Selection ──", file=sys.stderr)
    if projects:
        print("Existing projects:", file=sys.stderr)
        for i, p in enumerate(projects, start=1):
            print(f"  [{i}] {p['id']}", file=sys.stderr)
        print(
            "  [n] Enter a new project ID",
            file=sys.stderr,
        )
        while True:
            try:
                choice = input("Select project number or 'n' for new: ").strip()
            except EOFError:
                choice = "n"
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(projects):
                    return projects[idx]["id"]
            if choice.lower() in ("n", ""):
                break

    while True:
        try:
            new_id = input("New project ID: ").strip()
        except EOFError:
            new_id = ""
        if new_id:
            return new_id
        print("[error] Project ID cannot be empty.", file=sys.stderr)


def _run_ingest(
    path: Path,
    plain: bool,
    transcript: Path | None = None,
    project_id: str = "",
) -> int:
    token = os.getenv("COPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    copilot_client = None
    reporter = CliProgressReporter(sys.stderr)

    if token:
        try:
            copilot_client = CopilotClient(github_token=token)
        except (ImportError, ValueError) as exc:
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
        result = ingest_file(
            path,
            copilot_client=copilot_client,
            progress=reporter,
            transcript=transcript,
            project_id=project_id,
        )
    except KeyboardInterrupt:
        reporter.close()
        print(f"[cancelled] {reporter.interruption_message()}", file=sys.stderr)
        raise
    reporter.close()

    use_tui = not plain and sys.stdout.isatty()
    if use_tui:
        IngestReviewApp(ingest_result=result).run()
    else:
        print(f"Source:               {result.source.id}")
        print(f"Requirements created: {len(result.requirements)}")
        print(f"Audit issues:         {len(result.issues)}")
        print(f"LLM usage:            {_usage_summary_line(result.llm_usage)}")
        for issue in result.issues:
            suffix = f" ({issue.requirement_id})" if issue.requirement_id else ""
            print(f"  [{issue.code}]{suffix}: {issue.message}")
    return 0


def _run_refine(
    limit: int,
    req_id: str | None,
    plain: bool,
) -> int:
    """Run the Phase 2 batch refinement workflow.

    Each requirement is processed end-to-end:
    - Auto-refined when concern is low and no high-severity issues.
    - Paused for human review (TUI or plain-text) otherwise.
    Every decision is persisted to Neo4j immediately so the session is
    resumable after an interruption.
    """
    from .workflow import RefinementWorkflow  # noqa: PLC0415

    cfg = load_config()
    reporter = CliProgressReporter(sys.stderr)
    token = os.getenv("COPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    copilot_client = None
    if token:
        try:
            copilot_client = CopilotClient(github_token=token)
        except (ImportError, ValueError) as exc:
            print(
                f"[warn] Copilot client unavailable ({exc}); using rule-based proposals only.",
                file=sys.stderr,
            )

    store = Neo4jStore(
        uri=cfg.neo4j_uri,
        username=cfg.neo4j_username,
        password=cfg.neo4j_password,
        embedding_dimension=cfg.embedding_dimension,
        progress=reporter,
    )

    try:
        if req_id:
            queue = [
                r for r in store.load_refinement_queue(limit=10000)
                if r.id == req_id
            ]
            if not queue:
                print(f"[error] Requirement '{req_id}' not found or not in Draft/Under_Review state.",
                      file=sys.stderr)
                return 1
        else:
            queue = store.load_refinement_queue(limit=limit)

        under_review = sum(1 for r in queue if r.state == "Under_Review")
        draft = sum(1 for r in queue if r.state == "Draft")
        print(
            f"Refinement queue: {draft} Draft, {under_review} Under_Review "
            f"({len(queue)} total to process)",
            file=sys.stderr,
        )

        workflow = RefinementWorkflow(
            store,
            copilot_client=copilot_client,
            progress=reporter,
        )

        counts: dict[str, int] = {
            "stabilized": 0,
            "auto_refined": 0,
            "human_accepted": 0,
            "skipped": 0,
            "error": 0,
        }

        use_tui = not plain and sys.stdout.isatty()

        for requirement in queue:
            outcome = workflow.run_one(requirement)

            if outcome.status == "needs_human":
                review_result = outcome.pending_review
                decision: HumanDecision | None = None

                if use_tui:
                    decision = ProtoProjectApp(review_result=review_result).run()
                else:
                    decision = _plain_text_human_review(requirement, review_result)

                if decision is None or decision.action == "skip":
                    # Treat a closed TUI window (decision=None) as a skip.
                    if decision is None:
                        decision = HumanDecision(
                            action="skip",
                            text=requirement.text,
                            concern_value=requirement.concern_value,
                        )
                    outcome = workflow.resume(requirement, decision)

                else:
                    outcome = workflow.resume(requirement, decision)

            if outcome.status in ("stabilized", "auto_refined"):
                if outcome.status == "stabilized" and not (outcome.revised and outcome.revised.version > 1):
                    counts["stabilized"] += 1
                elif outcome.status == "auto_refined":
                    # Distinguish human-accepted from automatic.
                    if outcome.revised and hasattr(outcome, "_human"):
                        counts["human_accepted"] += 1
                    else:
                        counts["auto_refined"] += 1
                else:
                    counts["stabilized"] += 1
            elif outcome.status == "skipped":
                counts["skipped"] += 1
            elif outcome.status == "error":
                counts["error"] += 1
                print(
                    f"[error] {requirement.id}: {outcome.error}",
                    file=sys.stderr,
                )

    except KeyboardInterrupt:
        reporter.close()
        print("\n[cancelled] Refinement interrupted.", file=sys.stderr)
        return 130
    finally:
        store.close()
        reporter.close()

    total = sum(counts.values())
    print(
        f"\nRefinement complete: {total} processed — "
        f"{counts['stabilized']} stabilized, "
        f"{counts['auto_refined']} auto-refined, "
        f"{counts['skipped']} skipped, "
        f"{counts['error']} error(s)"
    )
    return 0 if counts["error"] == 0 else 1


def _plain_text_human_review(requirement, review_result) -> HumanDecision:
    """Fallback plain-text human review for non-TTY / --no-tui mode."""
    req = review_result.requirement
    issues = review_result.quality_issues
    proposal = review_result.proposal

    print(f"\n{'='*60}")
    print(f"Requirement: {req.id}  (v{req.version}, concern={req.concern_value})")
    print(f"Text: {req.text}")
    print(f"Issues ({len(issues)}):")
    for issue in issues:
        print(f"  [{issue.code}] {issue.severity}: {issue.message}")
    if proposal:
        print(f"Proposed: {proposal.proposed_text}")
        print(f"Suggested concern value: {proposal.concern_value}")
    print("\nOptions: [a] Accept proposal  [e] Edit  [s] Skip")

    while True:
        try:
            choice = input("Choice: ").strip().lower()
        except EOFError:
            choice = "s"
        if choice == "a" and proposal:
            return HumanDecision(
                action="accept",
                text=proposal.proposed_text,
                concern_value=proposal.concern_value,
            )
        if choice == "e":
            try:
                new_text = input("Enter revised requirement text: ").strip()
                if new_text:
                    cv_str = input(
                        f"Concern value [{req.concern_value}]: "
                    ).strip()
                    cv = int(cv_str) if cv_str.isdigit() else req.concern_value
                    cv = max(1, min(5, cv))
                    return HumanDecision(action="accept", text=new_text, concern_value=cv)
            except EOFError:
                pass
        if choice in ("s", ""):
            return HumanDecision(
                action="skip",
                text=req.text,
                concern_value=req.concern_value,
            )


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        try:
            cfg = load_config()
            project_id = _pick_project(
                plain=args.no_tui,
                config=cfg,
                preselected=args.project,
            )
            return _run_ingest(
                args.path,
                plain=args.no_tui,
                transcript=args.transcript,
                project_id=project_id,
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

    if args.command == "refine":
        try:
            return _run_refine(
                limit=args.limit,
                req_id=args.req_id,
                plain=args.no_tui,
            )
        except KeyboardInterrupt:
            return 130

    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
