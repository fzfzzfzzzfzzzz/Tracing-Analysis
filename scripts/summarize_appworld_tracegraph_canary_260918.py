#!/usr/bin/env python3
"""Verify and summarize the exposed AppWorld TraceGraph canary comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tracegraph.integrations.appworld_memory import (
    messages_sha256,
    project_appworld_history,
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("outputs/appworld_external_canary_260918"),
    )
    parser.add_argument("--run-label", default="tracegraph_b8192_qwen38_v2")
    parser.add_argument("--budget", type=int, default=8192)
    args = parser.parse_args()

    baseline_summary = load_json(args.root / "full_history_canary_summary.json")
    baseline_by_task = {item["task_id"]: item for item in baseline_summary["tasks"]}
    tasks: list[dict[str, Any]] = []

    for task_id in baseline_by_task:
        task_dir = args.root / args.run_label / f"task_{task_id}"
        history = load_json(task_dir / "llm_history.json")[0]
        system, body = history[0], history[1:]
        views = load_jsonl(task_dir / "tracegraph_context_views.jsonl")
        usage = load_jsonl(task_dir / "provider_usage.jsonl")
        receipt = load_json(task_dir / "run_receipt.json")
        official = load_json(task_dir / "official_evaluation.json")
        results = receipt["results"]

        hash_checks: list[dict[str, Any]] = []
        for call_index, (view, provider) in enumerate(zip(views, usage), start=1):
            projection = project_appworld_history(
                body[: 2 * call_index - 1],
                budget=args.budget,
                session_id=f"appworld_{task_id}",
            )
            projection_match = projection.projected_sha256 == view["projected_sha256"]
            request_match = (
                messages_sha256([system, *projection.messages])
                == provider["request_sha256"]
            )
            hash_checks.append(
                {
                    "call_index": call_index,
                    "projection_hash_match": projection_match,
                    "provider_request_hash_match": request_match,
                }
            )

        trace_usage = {
            "requests": len(usage),
            "request_attempts": sum(len(item.get("attempts", [])) for item in usage),
            "prompt_tokens": sum(item["prompt_tokens"] for item in usage),
            "completion_tokens": sum(item["completion_tokens"] for item in usage),
        }
        trace_usage["total_tokens"] = (
            trace_usage["prompt_tokens"] + trace_usage["completion_tokens"]
        )
        baseline_raw = baseline_by_task[task_id]["usage"]
        baseline_usage = {
            "requests": baseline_raw["requests"],
            "prompt_tokens": baseline_raw["input_tokens"],
            "completion_tokens": baseline_raw["output_tokens"],
            "total_tokens": baseline_raw["total_tokens"],
        }
        deltas = {
            key: trace_usage[key] - baseline_usage[key]
            for key in ("requests", "prompt_tokens", "completion_tokens", "total_tokens")
        }
        delta_percent = {
            key: round(100.0 * deltas[key] / baseline_usage[key], 3)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        official_success = bool(official["individual"][task_id]["success"])
        tasks.append(
            {
                "task_id": task_id,
                "official_success": official_success,
                "full_history_official_success": (
                    baseline_by_task[task_id]["official_task_goal_completion"] == 1.0
                ),
                "iterations": results["iterations"],
                "full_history_iterations": baseline_by_task[task_id]["iterations"],
                "usage": trace_usage,
                "full_history_usage": baseline_usage,
                "delta": deltas,
                "delta_percent": delta_percent,
                "compression": {
                    "compressed_turns": sum(
                        item["projected_message_count"] < item["source_message_count"]
                        for item in views
                    ),
                    "turn_count": len(views),
                    "max_source_messages": max(
                        item["source_message_count"] for item in views
                    ),
                    "min_retained_message_fraction": round(
                        min(
                            item["projected_message_count"]
                            / item["source_message_count"]
                            for item in views
                        ),
                        6,
                    ),
                    "projection_budget_infeasible_turns": sum(
                        bool(item.get("projection_budget_infeasible")) for item in views
                    ),
                    "provider_prompt_over_content_budget_turns": sum(
                        max(
                            (
                                attempt.get("prompt_tokens", 0)
                                for attempt in item.get("attempts", [])
                            ),
                            default=item["prompt_tokens"],
                        )
                        > args.budget
                        for item in usage
                    ),
                    "max_provider_prompt_tokens": max(
                        max(
                            (
                                attempt.get("prompt_tokens", 0)
                                for attempt in item.get("attempts", [])
                            ),
                            default=item["prompt_tokens"],
                        )
                        for item in usage
                    ),
                },
                "verification": {
                    "context_views": len(views),
                    "provider_receipts": len(usage),
                    "all_hashes_reconstructed": (
                        len(views) == len(usage) == len(hash_checks)
                        and all(
                            item["projection_hash_match"]
                            and item["provider_request_hash_match"]
                            for item in hash_checks
                        )
                    ),
                    "graph_validation_error_count": sum(
                        len(
                            item["graph"]["metadata"].get(
                                "graph_validation_errors", []
                            )
                        )
                        for item in views
                    ),
                    "hash_checks": hash_checks,
                },
            }
        )

    trace_total = {
        key: sum(task["usage"][key] for task in tasks)
        for key in ("requests", "prompt_tokens", "completion_tokens", "total_tokens")
    }
    baseline_total = {
        key: sum(task["full_history_usage"][key] for task in tasks)
        for key in ("requests", "prompt_tokens", "completion_tokens", "total_tokens")
    }
    aggregate_delta = {
        key: trace_total[key] - baseline_total[key]
        for key in trace_total
    }
    aggregate_delta_percent = {
        key: round(100.0 * aggregate_delta[key] / baseline_total[key], 3)
        for key in trace_total
    }
    output = {
        "schema_version": "appworld_tracegraph_canary_comparison_v1",
        "status": "completed",
        "development_only": True,
        "benchmark": "AppWorld",
        "model": "Qwen3.8-27B-rev-1d4bf0f",
        "method": "tracegraph",
        "content_estimator_budget": args.budget,
        "provider_token_budget_exact": False,
        "run_label": args.run_label,
        "task_count": len(tasks),
        "official_successes": sum(task["official_success"] for task in tasks),
        "official_success_rate": sum(task["official_success"] for task in tasks)
        / len(tasks),
        "full_history_official_successes": sum(
            task["full_history_official_success"] for task in tasks
        ),
        "full_history_official_success_rate": sum(
            task["full_history_official_success"] for task in tasks
        )
        / len(tasks),
        "paired_outcomes": {
            "both_success": sum(
                task["official_success"] and task["full_history_official_success"]
                for task in tasks
            ),
            "tracegraph_only_success": sum(
                task["official_success"] and not task["full_history_official_success"]
                for task in tasks
            ),
            "full_history_only_success": sum(
                not task["official_success"] and task["full_history_official_success"]
                for task in tasks
            ),
            "both_failure": sum(
                not task["official_success"]
                and not task["full_history_official_success"]
                for task in tasks
            ),
        },
        "usage": trace_total,
        "full_history_usage": baseline_total,
        "delta": aggregate_delta,
        "delta_percent": aggregate_delta_percent,
        "verification": {
            "all_tasks_hash_reconstructed": all(
                task["verification"]["all_hashes_reconstructed"] for task in tasks
            ),
            "graph_validation_error_count": sum(
                task["verification"]["graph_validation_error_count"] for task in tasks
            ),
            "projection_budget_infeasible_turns": sum(
                task["compression"]["projection_budget_infeasible_turns"]
                for task in tasks
            ),
        },
        "implementation": {
            "projection_module": "src/tracegraph/integrations/appworld_memory.py",
            "projection_module_sha256": file_sha256(
                Path("src/tracegraph/integrations/appworld_memory.py")
            ),
            "runner": "scripts/run_qwen38_appworld_canary_server_260918.py",
            "runner_sha256": file_sha256(
                Path("scripts/run_qwen38_appworld_canary_server_260918.py")
            ),
        },
        "tasks": tasks,
    }
    output_path = args.root / args.run_label / "aggregate_summary.json"
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
