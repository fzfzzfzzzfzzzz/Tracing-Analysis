"""Aggregate a completed or in-progress GLM Stage 1 experiment matrix."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tracegraph.stage1 import (
    analyze_stage1_plan,
    materialize_trace_archives,
    materialize_trace_graphs,
    write_stage1_report,
)


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--graph-output", type=Path)
    parser.add_argument("--archive-output", type=Path)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    report = analyze_stage1_plan(
        plan,
        project_root=project_root,
        results_root=args.results_root,
    )
    write_stage1_report(report, args.output)
    graph_files = []
    if args.graph_output is not None:
        graph_files = materialize_trace_graphs(
            report,
            project_root=project_root,
            output_dir=args.graph_output,
        )
    archive_files = []
    if args.archive_output is not None:
        archive_files = materialize_trace_archives(
            report,
            project_root=project_root,
            output_dir=args.archive_output,
        )
    summary = {
        "matrix_id": report["matrix_id"],
        "state": report["state"],
        "complete": report["complete"],
        "overall_pass": report["overall_pass"],
        "counts": report["counts"],
        "metrics": report["metrics"],
        "materialized_graphs": len(graph_files),
        "materialized_archive_objects": len(archive_files),
        "output": str(args.output),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
