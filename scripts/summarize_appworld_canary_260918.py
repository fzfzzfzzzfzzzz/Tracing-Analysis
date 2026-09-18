"""Aggregate the frozen five-task AppWorld canary from official evaluations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT
    / "data"
    / "external_benchmark_freezes"
    / "appworld_260918"
    / "canary_task_ids.jsonl"
)
OUTPUT = ROOT / "outputs" / "appworld_external_canary_260918"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def official_score(text: str) -> float:
    match = re.search(r"aggregate\s*\|\s*([0-9.]+)", text)
    if not match:
        raise ValueError("official AppWorld aggregate score not found")
    return float(match.group(1)) / 100.0


def usage_from_jsonl(path: Path) -> dict[str, int]:
    rows = load_jsonl(path)
    return {
        "requests": len(rows),
        "input_tokens": sum(int(row["prompt_tokens"]) for row in rows),
        "output_tokens": sum(int(row["completion_tokens"]) for row in rows),
        "total_tokens": sum(
            int(row["prompt_tokens"]) + int(row["completion_tokens"])
            for row in rows
        ),
    }


def main() -> None:
    frozen_ids = [str(row["task_id"]) for row in load_jsonl(FREEZE)]
    records = []
    for task_id in frozen_ids:
        if task_id == "23cf851_1":
            task_dir = OUTPUT / "full_history_qwen38_v2" / f"task_{task_id}"
            evaluation = (task_dir / "official_evaluation.log").read_text(
                encoding="utf-8"
            )
            usage = usage_from_jsonl(task_dir / "provider_usage.jsonl")
            record = {
                "task_id": task_id,
                "source_run": "full_history_qwen38_v2",
                "official_task_goal_completion": official_score(evaluation),
                "internal_success": None,
                "iterations": usage["requests"],
                "usage": usage,
                "receipt_status": "recovered_from_official_log_and_provider_usage",
            }
        else:
            task_dir = OUTPUT / "full_history_qwen38_v3" / f"task_{task_id}"
            receipt = json.loads(
                (task_dir / "run_receipt.json").read_text(encoding="utf-8")
            )
            results = receipt["results"]
            token_usage = results["token_usage"]
            record = {
                "task_id": task_id,
                "source_run": "full_history_qwen38_v3",
                "official_task_goal_completion": official_score(
                    receipt["official_evaluation"]["stdout"]
                ),
                "internal_success": bool(results["success"]),
                "iterations": int(results["iterations"]),
                "usage": {
                    "requests": int(token_usage["total_requests"]),
                    "input_tokens": int(token_usage["total_input_tokens"]),
                    "output_tokens": int(token_usage["total_output_tokens"]),
                    "total_tokens": int(token_usage["total_tokens"]),
                },
                "receipt_status": "complete",
            }
        records.append(record)

    official_successes = sum(
        row["official_task_goal_completion"] == 1.0 for row in records
    )
    aggregate = {
        "schema_version": "appworld_external_canary_aggregate_v1",
        "status": "completed",
        "benchmark": "AppWorld",
        "method": "full_history",
        "model": "Qwen3.8-27B-rev-1d4bf0f",
        "development_only": True,
        "metric_source": "official AppWorld evaluator",
        "task_count": len(records),
        "official_successes": official_successes,
        "official_success_rate": official_successes / len(records),
        "usage": {
            key: sum(int(row["usage"][key]) for row in records)
            for key in ("requests", "input_tokens", "output_tokens", "total_tokens")
        },
        "tasks": records,
    }
    output = OUTPUT / "full_history_canary_summary.json"
    output.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
