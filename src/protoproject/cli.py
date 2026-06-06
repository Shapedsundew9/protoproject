"""Command-line entrypoint for Phase 1."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from .ingest import ingest_file
from .parser import _mechanical_parse_fallback
from .refinement import build_review
from .tui import IngestReviewApp
from .validator import ValidationContext, build_source_record, normalize_requirements


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
        "--no-tui",
        action="store_true",
        help="Print a plain-text summary instead of launching the TUI",
    )

    review_p = subparsers.add_parser(
        "review", help="Review a requirement from a raw text file"
    )
    review_p.add_argument("path", type=Path)
    return parser


async def _run_ingest(path: Path, plain: bool) -> int:
    token = os.getenv("COPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    copilot_client = None

    if token:
        try:
            from copilot import CopilotClient  # noqa: PLC0415

            copilot_client = CopilotClient(github_token=token)
            await copilot_client.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Copilot client unavailable ({exc}); using mechanical parser.")
            copilot_client = None
    else:
        print("[warn] No COPILOT_GITHUB_TOKEN/GITHUB_TOKEN found; using mechanical parser.")

    try:
        result = await ingest_file(path, copilot_client=copilot_client)
    finally:
        if copilot_client is not None:
            try:
                await copilot_client.stop()
            except Exception:  # noqa: BLE001
                pass

    use_tui = not plain and sys.stdout.isatty()
    if use_tui:
        IngestReviewApp(result).run()
    else:
        print(f"Source:               {result.source.id}")
        print(f"Requirements created: {len(result.requirements)}")
        print(f"Audit issues:         {len(result.issues)}")
        for issue in result.issues:
            suffix = f" ({issue.requirement_id})" if issue.requirement_id else ""
            print(f"  [{issue.code}]{suffix}: {issue.message}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        return asyncio.run(_run_ingest(args.path, plain=args.no_tui))

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

