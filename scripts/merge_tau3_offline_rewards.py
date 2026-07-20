"""Materialize append-only offline rewards into a new τ³ results JSON file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.trajectory_artifacts import (
    TrajectoryArtifactStore,
    merge_rewards_into_results,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path)
    args = parser.parse_args()
    if args.results.resolve() == args.output.resolve():
        raise SystemExit("output must differ from the immutable generation results input")
    source = json.loads(args.results.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ValueError("results input must be a JSON object")
    output, audit = merge_rewards_into_results(source, TrajectoryArtifactStore(args.store))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit_output = args.audit_output or args.output.with_name(
        f"{args.output.stem}_offline_merge_audit.json"
    )
    audit_output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
