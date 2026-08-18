"""Evaluate preregistered E0 eligibility from frozen TraceGraphs only."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from tracegraph.decision_point_dataset import (
    DEFAULT_ELIGIBILITY_THRESHOLDS,
    discover_graph_records,
    evaluate_benchmark_eligibility,
)


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path)
    args = parser.parse_args()
    thresholds = dict(DEFAULT_ELIGIBILITY_THRESHOLDS)
    if args.thresholds:
        configured = json.loads(args.thresholds.read_text(encoding="utf-8"))
        configured = configured.get("e0_eligibility", configured)
        thresholds.update(configured)
    records = discover_graph_records(args.input)
    report = evaluate_benchmark_eligibility(records, thresholds=thresholds)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "benchmark_eligibility.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (args.output / "benchmark_eligibility_sessions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        fieldnames = [
            "session_id", "domain", "task_id", "agent_actions", "tool_calls",
            "provider_input_tokens", "dynamic_provider_input_ratio", "oracle_headroom",
            "snapshot_replayable", "native_evaluator_success", "native_evaluator_side_effect",
            "success", "source_path",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["sessions"])
    print(json.dumps({key: value for key, value in report.items() if key != "sessions"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
