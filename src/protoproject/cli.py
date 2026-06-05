"""Command-line entrypoint for Phase 1."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .embeddings import HashEmbeddingProvider
from .ingest import ingest_file
from .parser import parse_requirement_text
from .refinement import build_review
from .validator import ValidationContext, build_source_record, normalize_requirements


def _load_requirement_from_text(raw_text: str):
    source = build_source_record(raw_text)
    drafts = [draft for draft in parse_requirement_text(raw_text) if draft.text]
    requirements = normalize_requirements(
        drafts,
        ValidationContext(source=source, embedding_provider=HashEmbeddingProvider()),
    )
    if not requirements:
        raise SystemExit("No requirements found in the supplied text.")
    return requirements[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="protoproject")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest a raw text file")
    ingest_parser.add_argument("path", type=Path)

    review_parser = subparsers.add_parser(
        "review", help="Review a requirement from a raw text file"
    )
    review_parser.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        result = ingest_file(args.path)
        print(f"Source: {result.source.id}")
        print(f"Requirements created: {len(result.requirements)}")
        print(f"Audit issues: {len(result.issues)}")
        for issue in result.issues:
            suffix = f" ({issue.requirement_id})" if issue.requirement_id else ""
            print(f"- {issue.code}{suffix}: {issue.message}")
        return 0

    if args.command == "review":
        raw_text = args.path.read_text(encoding="utf-8")
        requirement = _load_requirement_from_text(raw_text)
        review = build_review(requirement)
        print(f"Requirement: {review.requirement.id}")
        print(f"Text: {review.requirement.text}")
        print(f"Concern: {review.requirement.concern_value}")
        print(f"Quality issues: {len(review.quality_issues)}")
        for issue in review.quality_issues:
            print(f"- {issue.code} [{issue.severity}]: {issue.message}")
        if review.proposal:
            print(f"Proposal: {review.proposal.proposed_text}")
            print(f"Suggested concern: {review.proposal.concern_value}")
        return 0

    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
