"""Definitions moved from ``tracegraph.stage1``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import json
import math
import shutil
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from .stage1_constants import (
    _INFRASTRUCTURE_TERMINATION as _INFRASTRUCTURE_TERMINATION,
    _NORMAL_TERMINATIONS as _NORMAL_TERMINATIONS,
)



def analyze_stage1_plan(
    plan: dict[str, Any],
    *,
    project_root: Path,
    results_root: Path,
) -> dict[str, Any]:
    """Aggregate official simulations and TraceGraph traces for one matrix plan.

    Runs are sequential and single-concurrency. Within each run, official
    simulations are ordered by trial and trace directories by completion mtime,
    then paired in that order. Counts must match before gates are evaluable.
    """

    project_root = project_root.resolve()
    results_root = results_root.resolve()
    expected_sessions = int(plan["session_count"])
    expected_runs = int(plan["run_count"])
    session_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    missing_result_files: list[str] = []
    malformed_sessions: list[str] = []

    for run in plan.get("runs") or []:
        run_id = str(run["run_id"])
        expected_trials = int(run["trials"])
        result_file = results_root / str(run["save_to"]) / "results.json"
        if not result_file.exists():
            missing_result_files.append(result_file.as_posix())
            simulations: list[dict[str, Any]] = []
        else:
            result_payload = _read_json(result_file)
            simulations = [
                item
                for item in (result_payload.get("simulations") or [])
                if isinstance(item, dict)
            ]
        simulations.sort(
            key=lambda item: (
                int(item.get("trial") or 0),
                str(item.get("start_time") or ""),
                str(item.get("id") or ""),
            )
        )

        trace_root = project_root / str(run["trace_output_dir"])
        trace_records = [
            _trace_record(path, project_root)
            for path in trace_root.glob("*/trace.json")
        ] if trace_root.exists() else []
        trace_records.sort(key=lambda item: (item["_mtime_ns"], item["trace_file"]))

        for index, simulation in enumerate(simulations):
            reward = _reward(simulation)
            simulation_id = str(simulation.get("id") or "")
            if reward is None or not simulation_id:
                malformed_sessions.append(f"{run_id}:trial={simulation.get('trial')}")
            termination = str(simulation.get("termination_reason") or "")
            trace = trace_records[index] if index < len(trace_records) else {}
            row = {
                "run_id": run_id,
                "domain": str(run["domain"]),
                "task_id": str(run["task_id"]),
                "trial": int(simulation.get("trial") or 0),
                "seed": simulation.get("seed"),
                "simulation_id": simulation_id,
                "reward": reward,
                "task_success": bool(
                    reward is not None and math.isclose(reward, 1.0, abs_tol=1e-6)
                ),
                "termination_reason": termination,
                "normal_stop": termination in _NORMAL_TERMINATIONS,
                "infrastructure_error": termination == _INFRASTRUCTURE_TERMINATION,
                "tool_calls": _tool_call_count(simulation),
                "message_count": len(simulation.get("messages") or []),
                "agent_provider_input_tokens": _provider_usage_sum(
                    simulation,
                    role="assistant",
                    keys=("prompt_tokens", "input_tokens", "input_token_count"),
                ),
                "agent_provider_output_tokens": _provider_usage_sum(
                    simulation,
                    role="assistant",
                    keys=(
                        "completion_tokens",
                        "output_tokens",
                        "output_token_count",
                    ),
                ),
                "duration_seconds": simulation.get("duration"),
                "agent_cost_usd": float(simulation.get("agent_cost") or 0.0),
                "user_cost_usd": float(simulation.get("user_cost") or 0.0),
            }
            row["total_cost_usd"] = row["agent_cost_usd"] + row["user_cost_usd"]
            row.update(_reward_diagnostics(simulation))
            row.update({key: value for key, value in trace.items() if not key.startswith("_")})
            row["failure_reasons"] = _failure_reasons(row)
            session_rows.append(row)

        run_rows.append(
            {
                "run_id": run_id,
                "domain": str(run["domain"]),
                "task_id": str(run["task_id"]),
                "expected_trials": expected_trials,
                "observed_simulations": len(simulations),
                "observed_traces": len(trace_records),
                "result_file_present": result_file.exists(),
                "complete": (
                    result_file.exists()
                    and len(simulations) == expected_trials
                    and len(trace_records) == expected_trials
                ),
            }
        )

    observed_sessions = len(session_rows)
    observed_runs = sum(row["result_file_present"] for row in run_rows)
    evaluated_rows = [row for row in session_rows if not row["infrastructure_error"]]
    trace_rows = [
        row for row in session_rows if row.get("estimated_trajectory_tokens") is not None
    ]
    complete = (
        len(run_rows) == expected_runs
        and observed_runs == expected_runs
        and all(row["complete"] for row in run_rows)
        and observed_sessions == expected_sessions
        and len(trace_rows) == expected_sessions
        and not missing_result_files
        and not malformed_sessions
    )

    evaluated_count = len(evaluated_rows)
    infrastructure_count = sum(row["infrastructure_error"] for row in session_rows)
    graph_validation_error_count = sum(
        int(row.get("graph_validation_error_count") or 0) for row in trace_rows
    )
    zero_token_trace_count = sum(
        int(row.get("estimated_trajectory_tokens") or 0) <= 0 for row in trace_rows
    )
    complete = complete and graph_validation_error_count == 0 and zero_token_trace_count == 0
    metrics = {
        "task_success_rate": _rate(
            sum(row["task_success"] for row in evaluated_rows), evaluated_count
        ),
        "normal_stop_rate": _rate(
            sum(row["normal_stop"] for row in evaluated_rows), evaluated_count
        ),
        "median_tool_calls": _median([row["tool_calls"] for row in evaluated_rows]),
        "median_estimated_trajectory_tokens": _median(
            [row["estimated_trajectory_tokens"] for row in trace_rows]
        ),
        "median_agent_provider_input_tokens": _median(
            [
                row["agent_provider_input_tokens"]
                for row in evaluated_rows
                if row.get("agent_provider_input_tokens") is not None
            ]
        ),
        "infrastructure_error_rate": _rate(infrastructure_count, observed_sessions),
        "total_actual_cost_usd": round(
            sum(row["total_cost_usd"] for row in session_rows), 8
        ),
        "median_duration_seconds": _median(
            [
                float(row["duration_seconds"])
                for row in evaluated_rows
                if row["duration_seconds"] is not None
            ]
        ),
    }

    domain_metrics = {
        domain: _group_metrics(
            [row for row in session_rows if row["domain"] == domain]
        )
        for domain in sorted({row["domain"] for row in session_rows})
    }
    task_metrics = [
        {
            "domain": domain,
            "task_id": task_id,
            **_group_metrics(
                [
                    row
                    for row in session_rows
                    if row["domain"] == domain and row["task_id"] == task_id
                ]
            ),
        }
        for domain, task_id in sorted(
            {(row["domain"], row["task_id"]) for row in session_rows}
        )
    ]

    configured_gates = plan.get("gates") or {}
    gates = {
        "minimum_task_success_rate": _gate(
            value=metrics["task_success_rate"],
            threshold=float(configured_gates["minimum_task_success_rate"]),
            operator=">=",
            complete=complete,
        ),
        "minimum_normal_stop_rate": _gate(
            value=metrics["normal_stop_rate"],
            threshold=float(configured_gates["minimum_normal_stop_rate"]),
            operator=">=",
            complete=complete,
        ),
        "minimum_median_tool_calls": _gate(
            value=metrics["median_tool_calls"],
            threshold=float(configured_gates["minimum_median_tool_calls"]),
            operator=">=",
            complete=complete,
        ),
        "minimum_median_estimated_tokens": _gate(
            value=metrics["median_estimated_trajectory_tokens"],
            threshold=float(configured_gates["minimum_median_estimated_tokens"]),
            operator=">=",
            complete=complete,
        ),
        "maximum_infrastructure_error_rate": _gate(
            value=metrics["infrastructure_error_rate"],
            threshold=float(configured_gates["maximum_infrastructure_error_rate"]),
            operator="<=",
            complete=complete,
        ),
    }
    overall_pass = complete and all(item["passed"] for item in gates.values())
    state = "pass" if overall_pass else ("fail" if complete else "incomplete")
    return {
        "schema_version": "1.0",
        "matrix_id": plan["matrix_id"],
        "state": state,
        "complete": complete,
        "overall_pass": overall_pass,
        "definitions": {
            "task_success": "official reward equals 1 within 1e-6",
            "normal_stop": "official termination is user_stop or agent_stop",
            "infrastructure_error": "official termination is infrastructure_error",
            "estimated_trajectory_tokens": "sum of TraceGraph node token_count",
            "token_accounting": (
                "content_estimate_v2 excludes provider prompt history from "
                "individual graph-node sizes"
            ),
            "agent_provider_input_tokens": (
                "sum of upstream assistant-message prompt/input usage; actual "
                "agent-generation input telemetry when present"
            ),
            "rate_denominator": "evaluated simulations exclude infrastructure_error",
            "trace_alignment": "single-concurrency trial order paired with trace completion order",
        },
        "counts": {
            "expected_runs": expected_runs,
            "observed_runs": observed_runs,
            "expected_sessions": expected_sessions,
            "observed_sessions": observed_sessions,
            "evaluated_sessions": evaluated_count,
            "observed_traces": len(trace_rows),
            "infrastructure_errors": infrastructure_count,
            "graph_validation_errors": graph_validation_error_count,
            "zero_token_traces": zero_token_trace_count,
            "malformed_sessions": len(malformed_sessions),
            "token_accounting_versions": dict(
                sorted(
                    Counter(
                        str(row.get("token_accounting") or "legacy_unspecified")
                        for row in trace_rows
                    ).items()
                )
            ),
        },
        "metrics": metrics,
        "termination_reasons": dict(
            sorted(Counter(row["termination_reason"] for row in session_rows).items())
        ),
        "failure_reason_counts": dict(
            sorted(
                Counter(
                    reason
                    for row in session_rows
                    for reason in row["failure_reasons"]
                ).items()
            )
        ),
        "domain_metrics": domain_metrics,
        "task_metrics": task_metrics,
        "gates": gates,
        "missing_result_files": missing_result_files,
        "malformed_session_ids": malformed_sessions,
        "runs": run_rows,
        "sessions": session_rows,
    }


def write_stage1_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stage1_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rows = report.get("sessions") or []
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with (output_dir / "stage1_sessions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def materialize_trace_graphs(
    report: dict[str, Any], *, project_root: Path, output_dir: Path
) -> list[Path]:
    """Copy aligned live traces into a stable flat directory for offline runs."""

    project_root = project_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for row in report.get("sessions") or []:
        relative = row.get("trace_file")
        if not relative:
            continue
        source = (project_root / str(relative)).resolve()
        if source != project_root and project_root not in source.parents:
            raise ValueError(f"trace path escapes project root: {relative}")
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = output_dir / f"{row['run_id']}_trial{int(row['trial'])}.json"
        payload = _read_json(source)
        metadata = payload.setdefault("metadata", {})
        metadata.update(
            {
                "matrix_id": report["matrix_id"],
                "run_id": row["run_id"],
                "domain": row["domain"],
                "task_id": row["task_id"],
                "trial": row["trial"],
                "official_simulation_id": row["simulation_id"],
                "reward": row["reward"],
                "task_success": float(row["task_success"]),
                "termination_reason": row["termination_reason"],
                "agent_cost_usd": row["agent_cost_usd"],
                "user_cost_usd": row["user_cost_usd"],
                "duration_seconds": row["duration_seconds"],
                "evaluated_context_manager": "full_trajectory",
                "experiment_provenance": "glm_stage1_live_full_trajectory",
            }
        )
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(destination)
    return written


def materialize_trace_archives(
    report: dict[str, Any], *, project_root: Path, output_dir: Path
) -> list[Path]:
    """Merge per-simulation content-addressed archives without changing objects."""

    project_root = project_root.resolve()
    written: dict[Path, Path] = {}
    for row in report.get("sessions") or []:
        relative = row.get("trace_file")
        if not relative:
            continue
        trace_file = (project_root / str(relative)).resolve()
        if trace_file != project_root and project_root not in trace_file.parents:
            raise ValueError(f"trace path escapes project root: {relative}")
        source_objects = trace_file.parent / "archive" / "objects"
        if not source_objects.exists():
            continue
        for source in source_objects.glob("*/*.json"):
            destination = output_dir / "objects" / source.parent.name / source.name
            if destination in written:
                if source.read_bytes() != written[destination].read_bytes():
                    raise ValueError(f"conflicting archive object: {source.name}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                raise ValueError(f"conflicting archive object: {source.name}")
            if not destination.exists():
                shutil.copyfile(source, destination)
            written[destination] = source
    return sorted(written)


# Imported after definitions so mutually-referential helpers initialize safely.
from .stage1_records import (
    _failure_reasons as _failure_reasons,
    _gate as _gate,
    _group_metrics as _group_metrics,
    _median as _median,
    _provider_usage_sum as _provider_usage_sum,
    _rate as _rate,
    _read_json as _read_json,
    _reward as _reward,
    _reward_diagnostics as _reward_diagnostics,
    _tool_call_count as _tool_call_count,
    _trace_record as _trace_record,
)
