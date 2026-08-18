"""Build reproducible token-reliability Pareto JSON/CSV from a frozen matrix report."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from tracegraph.trajectory_artifacts import sha256_json


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def build(report: dict) -> dict:
    rows = []
    for condition, metrics in sorted((report.get("condition_metrics") or {}).items()):
        rows.append({
            "condition": condition,
            "mean_provider_input_tokens": metrics.get("mean_provider_input_tokens"),
            "mean_net_token_cost": metrics.get("mean_net_token_cost"),
            "success_rate": metrics.get("success_rate"),
            "mean_unsafe_omissions": metrics.get("mean_unsafe_omissions"),
            "sessions": metrics.get("sessions"),
        })
    for row in rows:
        dominated_by = []
        for candidate in rows:
            if candidate is row:
                continue
            cost, reliability = row["mean_provider_input_tokens"], row["success_rate"]
            other_cost, other_reliability = candidate["mean_provider_input_tokens"], candidate["success_rate"]
            if None in {cost, reliability, other_cost, other_reliability}:
                continue
            if other_cost <= cost and other_reliability >= reliability and (
                other_cost < cost or other_reliability > reliability
            ):
                dominated_by.append(candidate["condition"])
        row["pareto_optimal"] = not dominated_by and row["mean_provider_input_tokens"] is not None and row["success_rate"] is not None
        row["dominated_by"] = dominated_by
    value = {
        "schema_version": "gdsc_pareto_report_v1", "source_report_sha256": report.get("report_sha256"),
        "cost_axis": "mean_provider_input_tokens", "reliability_axis": "success_rate",
        "rows": rows, "pareto_conditions": [row["condition"] for row in rows if row["pareto_optimal"]],
        "development_positive_evidence": bool(report.get("development_positive_evidence")),
        "formal_aaai_gate_claimed": False,
    }
    value["report_sha256"] = sha256_json(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "pareto_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ["condition", "mean_provider_input_tokens", "mean_net_token_cost", "success_rate", "mean_unsafe_omissions", "sessions", "pareto_optimal", "dominated_by"]
    with (args.output / "pareto_report.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({**row, "dominated_by": json.dumps(row["dominated_by"])})
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
