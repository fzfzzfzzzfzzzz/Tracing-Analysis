"""Evaluate formal P3 completion and the fail-closed P4 Go gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.phase3_gates import evaluate_phase3_gates, write_phase3_gate_report


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p1-manifest", type=Path, required=True)
    parser.add_argument("--p2-report", type=Path)
    parser.add_argument(
        "--p3-report",
        action="append",
        default=[],
        metavar="REFERENCE=PATH",
    )
    parser.add_argument("--noninferiority-margin", type=float, default=-0.05)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    reports = {}
    for item in args.p3_report:
        reference, separator, path = item.partition("=")
        if not separator or not reference or not path:
            raise ValueError("--p3-report must use REFERENCE=PATH")
        if reference in reports:
            raise ValueError(f"duplicate P3 reference: {reference}")
        reports[reference] = _read(Path(path))
    report = evaluate_phase3_gates(
        p1_manifest=_read(args.p1_manifest),
        p2_report=_read(args.p2_report) if args.p2_report else None,
        p3_reports_by_reference=reports,
        noninferiority_margin=args.noninferiority_margin,
    )
    write_phase3_gate_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
