"""Command-line entrypoint for Phase 1."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .ingest import ingest_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="protoproject")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest a raw text file")
    ingest_parser.add_argument("path", type=Path)
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

    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
