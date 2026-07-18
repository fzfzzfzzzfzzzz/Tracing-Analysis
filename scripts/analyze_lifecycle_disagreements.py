"""Rank lifecycle/no-lifecycle disagreements and export a targeted blind set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.annotation import export_annotation_package
from tracegraph.graph import TraceGraph
from tracegraph.lifecycle_diagnostics import (
    analyze_lifecycle_disagreements,
    write_lifecycle_diagnostics,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--reference-manager",
        default="ours_without_lifecycle_states",
    )
    parser.add_argument("--comparator-manager", default="full_ours")
    parser.add_argument("--annotation-output", type=Path)
    parser.add_argument("--sample-size", type=int, default=120)
    parser.add_argument("--seed", type=int, default=301)
    args = parser.parse_args()

    project_root = Path.cwd().resolve()
    live_report = json.loads(args.report.read_text(encoding="utf-8"))
    report = analyze_lifecycle_disagreements(
        live_report,
        project_root=project_root,
        reference_manager=args.reference_manager,
        comparator_manager=args.comparator_manager,
    )
    write_lifecycle_diagnostics(report, args.output)

    annotation_summary = None
    if args.annotation_output is not None:
        graphs = [
            TraceGraph.load(project_root / path)
            for path in report["selected_comparator_trace_files"]
        ]
        key = export_annotation_package(
            graphs,
            output_dir=args.annotation_output,
            sample_size=args.sample_size,
            seed=args.seed,
        )
        annotation_summary = {
            "graphs": len(graphs),
            "sample_size": key["sample_size_actual"],
            "output": str(args.annotation_output),
            "blind_annotation": True,
        }

    print(
        json.dumps(
            {
                "matrix_id": report["matrix_id"],
                "counts": report["counts"],
                "output": str(args.output),
                "annotation": annotation_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

