#!/usr/bin/env python3
"""Build a task-stratified composite plan from an original and repair matrix.

The composite keeps unaffected tasks from the original matrix and substitutes
every condition for explicitly repaired tasks.  This preserves within-task
pairing while making the mixed evaluator provenance impossible to overlook.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_IDENTITY_FIELDS = ("domain", "task_id", "manager", "budget")
PAIRED_INVARIANT_FIELDS = (
    "domain",
    "task_id",
    "manager",
    "budget",
    "agent_model",
    "user_model",
    "normalize_user_stop",
    "trials",
    "base_seed",
    "max_steps",
    "timeout_seconds",
    "token_accounting",
)


def _run_key(run: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(run[field]) for field in RUN_IDENTITY_FIELDS)


def build_composite_plan(
    original: dict[str, Any],
    repair: dict[str, Any],
    *,
    repair_task_ids: set[str],
    matrix_id: str,
    original_plan_path: str,
    repair_plan_path: str,
) -> dict[str, Any]:
    if not repair_task_ids:
        raise ValueError("repair_task_ids must not be empty")

    replacement_runs = {
        _run_key(run): run
        for run in repair.get("runs", [])
        if str(run.get("task_id")) in repair_task_ids
    }
    if not replacement_runs:
        raise ValueError("repair plan contains no requested repair tasks")

    selected_runs: list[dict[str, Any]] = []
    substituted_keys: set[tuple[str, ...]] = set()
    for original_run in original.get("runs", []):
        task_id = str(original_run.get("task_id"))
        if task_id not in repair_task_ids:
            selected_runs.append(copy.deepcopy(original_run))
            continue

        key = _run_key(original_run)
        replacement = replacement_runs.get(key)
        if replacement is None:
            raise ValueError(f"missing repair run for {key}")
        for field in PAIRED_INVARIANT_FIELDS:
            if original_run.get(field) != replacement.get(field):
                raise ValueError(
                    f"repair run changes paired invariant {field!r} for {key}: "
                    f"{original_run.get(field)!r} != {replacement.get(field)!r}"
                )
        selected_runs.append(copy.deepcopy(replacement))
        substituted_keys.add(key)

    unused_replacements = set(replacement_runs) - substituted_keys
    if unused_replacements:
        raise ValueError(
            "repair plan has runs absent from the original matrix: "
            f"{sorted(unused_replacements)}"
        )

    selected_task_ids = {str(run["task_id"]) for run in selected_runs}
    missing_tasks = repair_task_ids - selected_task_ids
    if missing_tasks:
        raise ValueError(f"requested repair tasks are absent: {sorted(missing_tasks)}")

    composite = copy.deepcopy(original)
    composite["schema_version"] = "1.1"
    composite["matrix_id"] = matrix_id
    composite["description"] = (
        "Task-stratified repaired composite: unaffected tasks come from the "
        "original P3 matrix; all four conditions for repaired tasks come from "
        "the balanced evaluator-fix rerun."
    )
    composite["composite"] = True
    composite["composite_sources"] = [
        {
            "role": "original_unaffected_tasks",
            "plan": original_plan_path,
            "matrix_id": original.get("matrix_id"),
            "task_ids": sorted(selected_task_ids - repair_task_ids),
            "evaluator_model": original.get("evaluator_model", "upstream_default"),
        },
        {
            "role": "balanced_repair_tasks",
            "plan": repair_plan_path,
            "matrix_id": repair.get("matrix_id"),
            "task_ids": sorted(repair_task_ids),
            "evaluator_model": repair.get("evaluator_model"),
        },
    ]
    composite["evaluator_policy"] = (
        "task_stratified: evaluator may differ between task strata but is "
        "identical across all paired conditions within each task"
    )
    composite["evaluator_model_by_task"] = {
        task_id: (
            repair.get("evaluator_model")
            if task_id in repair_task_ids
            else original.get("evaluator_model", "upstream_default")
        )
        for task_id in sorted(selected_task_ids)
    }
    composite.pop("evaluator_model", None)
    composite["runs"] = selected_runs
    composite["run_count"] = len(selected_runs)
    composite["session_count"] = sum(int(run["trials"]) for run in selected_runs)
    composite["generated_at"] = datetime.now(timezone.utc).isoformat()
    composite["source_config"] = {
        "original_plan": original_plan_path,
        "repair_plan": repair_plan_path,
        "repair_task_ids": sorted(repair_task_ids),
    }
    warnings = [
        str(original.get("interpretation_warning", "")).strip(),
        str(repair.get("interpretation_warning", "")).strip(),
        (
            "REPAIRED COMPOSITE: this is not one uninterrupted run. Pool only "
            "paired within-task deltas; retain task-stratified evaluator provenance."
        ),
    ]
    composite["interpretation_warning"] = " ".join(
        warning for warning in warnings if warning
    )
    return composite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-plan", required=True, type=Path)
    parser.add_argument("--repair-plan", required=True, type=Path)
    parser.add_argument("--repair-task-id", action="append", required=True)
    parser.add_argument("--matrix-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    original = json.loads(args.original_plan.read_text(encoding="utf-8"))
    repair = json.loads(args.repair_plan.read_text(encoding="utf-8"))
    composite = build_composite_plan(
        original,
        repair,
        repair_task_ids={str(task_id) for task_id in args.repair_task_id},
        matrix_id=args.matrix_id,
        original_plan_path=args.original_plan.as_posix(),
        repair_plan_path=args.repair_plan.as_posix(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(composite, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "matrix_id": composite["matrix_id"],
                "run_count": composite["run_count"],
                "session_count": composite["session_count"],
                "repair_task_ids": sorted(str(task_id) for task_id in args.repair_task_id),
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
