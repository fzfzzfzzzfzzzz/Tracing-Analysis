"""Command-line entrypoint for graph validation and archive checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .archive import ArchiveStore
from .graph import TraceGraph


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tracegraph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-trace", help="validate a saved trace graph")
    validate.add_argument("path", type=Path)

    archive = subparsers.add_parser("verify-archive", help="verify all archive object hashes")
    archive.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-trace":
        graph = TraceGraph.load(args.path)
        errors = graph.validate()
        print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    if args.command == "verify-archive":
        failures = ArchiveStore(args.path).verify_all()
        print(json.dumps({"valid": not failures, "failures": failures}, indent=2))
        return 0 if not failures else 1
    return 2

