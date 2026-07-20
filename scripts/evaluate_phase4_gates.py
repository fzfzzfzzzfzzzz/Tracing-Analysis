"""Evaluate Phase 4 engineering readiness without authorizing external experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.phase4_gates import evaluate_phase4_gates


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migration-audit", type=Path, required=True)
    parser.add_argument("--v2-construct-report", type=Path, required=True)
    parser.add_argument("--trajectory-protocol-audit", type=Path, required=True)
    parser.add_argument("--post-failure-report", type=Path, required=True)
    parser.add_argument("--common-prefix-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_phase4_gates(
        migration_audit=_read(args.migration_audit),
        v2_construct_report=_read(args.v2_construct_report),
        trajectory_protocol_audit=_read(args.trajectory_protocol_audit),
        post_failure_report=_read(args.post_failure_report),
        common_prefix_report=(
            _read(args.common_prefix_report) if args.common_prefix_report else None
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
