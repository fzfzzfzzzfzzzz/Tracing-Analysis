"""Merge deterministic GDSC offline shards and recompute the R2 gate."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tracegraph.trajectory_artifacts import sha256_json


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _boolean(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) not in (None, "")]
    return statistics.median(values) if values else None


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    return statistics.fmean(float(_boolean(row.get(key))) for row in rows) if rows else 0.0


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = [
        json.loads((path / "r2_offline_mechanism.json").read_text(encoding="utf-8"))
        for path in args.shard
    ]
    dataset_hashes = {report.get("dataset_sha256") for report in reports}
    shard_specs = {
        (int(report["shard"]["index"]), int(report["shard"]["count"]))
        for report in reports
    }
    counts = {count for _index, count in shard_specs}
    if len(dataset_hashes) != 1 or len(counts) != 1:
        raise ValueError("shards do not share a dataset hash/count")
    shard_count = counts.pop()
    if {index for index, _count in shard_specs} != set(range(shard_count)):
        raise ValueError("shard set is incomplete")
    budget_rows = [
        row for path in args.shard for row in _read_csv(path / "r2_budget_rows.csv")
    ]
    ablation_rows = [
        row for path in args.shard for row in _read_csv(path / "r2_ablation_rows.csv")
    ]
    point_ids = {str(row["decision_point_id"]) for row in budget_rows}
    expected_budget_rows = sum(int(report["decision_point_count"]) for report in reports) * len(
        {int(row["budget"]) for row in budget_rows}
    )
    if len(budget_rows) != expected_budget_rows:
        raise ValueError("budget rows are incomplete or duplicated")

    by_budget: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in budget_rows:
        by_budget[int(row["budget"])].append(row)
    budgets = []
    for budget, rows in sorted(by_budget.items()):
        budgets.append(
            {
                "budget": budget,
                "decision_points": len(rows),
                "hard_coverage_rate": _rate(rows, "hard_coverage"),
                "conservative_fallback_rate": _rate(rows, "conservative_fallback"),
                "hard_limit_exceeded_rate": _rate(rows, "hard_limit_exceeded"),
                "median_raw_serialized_tokens": _median(rows, "raw_serialized_tokens"),
                "median_compiled_serialized_tokens": _median(rows, "compiled_serialized_tokens"),
                "median_serialized_reduction": _median(rows, "serialized_reduction"),
            }
        )
    candidates = [
        row
        for row in budgets
        if row["hard_coverage_rate"] == 1.0
        and row["conservative_fallback_rate"] <= 0.05
        and row["hard_limit_exceeded_rate"] == 0.0
    ]
    primary_budget = min((int(row["budget"]) for row in candidates), default=None)
    primary = next((row for row in budgets if row["budget"] == primary_budget), None)

    by_ablation: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ablation_rows:
        by_ablation[str(row["ablation"])].append(row)
    ablations = [
        {
            "ablation": name,
            "decision_points": len(rows),
            "median_serialized_tokens": _median(rows, "serialized_tokens"),
            "hard_coverage_rate": _rate(rows, "hard_coverage"),
            "conservative_fallback_rate": _rate(rows, "conservative_fallback"),
        }
        for name, rows in sorted(by_ablation.items())
    ]

    structured_total = sum(int(report["structured_representation_count"]) for report in reports)
    structured_equivalent = sum(
        int(report["structured_representation_count"])
        * float(report["structured_equivalence_rate"])
        for report in reports
    )
    decision_count = sum(int(report["decision_point_count"]) for report in reports)
    sufficient = sum(
        int(report["decision_point_count"])
        * float(report["provisional_decision_sufficiency_rate"])
        for report in reports
    )
    structured_rate = structured_equivalent / structured_total if structured_total else 0.0
    sufficiency_rate = sufficient / decision_count if decision_count else 0.0
    checks = {
        "structured_equivalence_100_percent": structured_rate == 1.0,
        "provisional_decision_sufficiency_at_least_95_percent": sufficiency_rate >= 0.95,
        "primary_budget_identified": primary_budget is not None,
        "median_serialized_reduction_at_least_30_percent": bool(primary)
        and float(primary["median_serialized_reduction"] or 0.0) >= 0.30,
    }
    report: dict[str, Any] = {
        "schema_version": "gdsc_offline_mechanism_v1",
        "execution": "offline_zero_api_deterministic_shards",
        "dataset_sha256": next(iter(dataset_hashes)),
        "decision_point_count": decision_count,
        "unique_decision_point_count": len(point_ids),
        "shard_count": shard_count,
        "tool_schema_provenance": "native_tau3_environment_openai_schema",
        "structured_representation_count": structured_total,
        "structured_equivalence_rate": structured_rate,
        "provisional_decision_sufficiency_rate": sufficiency_rate,
        "budgets": budgets,
        "primary_budget": primary_budget,
        "ablations": ablations,
        "r2_gate": {**checks, "passed": all(checks.values())},
        "limitations": reports[0]["limitations"],
    }
    report["report_sha256"] = sha256_json(report)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "r2_offline_mechanism.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(args.output / "r2_budget_rows.csv", budget_rows)
    _write_csv(args.output / "r2_ablation_rows.csv", ablation_rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
