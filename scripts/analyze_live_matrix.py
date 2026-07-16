"""Aggregate a completed or in-progress paired live context-manager matrix."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tracegraph.paired import analyze_live_matrix, write_live_matrix_report


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-manager", default="full_trajectory")
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=300)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    report = analyze_live_matrix(
        plan,
        project_root=project_root,
        results_root=args.results_root,
        reference_manager=args.reference_manager,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
    )
    write_live_matrix_report(report, args.output)
    print(
        json.dumps(
            {
                "matrix_id": report["matrix_id"],
                "complete": report["complete"],
                "counts": report["counts"],
                "condition_metrics": report["condition_metrics"],
                "paired_comparisons": report["paired_comparisons"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
