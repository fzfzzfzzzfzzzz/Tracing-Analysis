"""Auditable aggregation for paired live context-manager matrices."""

from __future__ import annotations

import csv
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


_NORMAL_TERMINATIONS = {"user_stop", "agent_stop"}
_INFRASTRUCTURE_TERMINATIONS = {"infrastructure_error", "timeout"}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _reward(simulation: dict[str, Any]) -> float | None:
    reward_info = simulation.get("reward_info")
    if not isinstance(reward_info, dict) or reward_info.get("reward") is None:
        return None
    return float(reward_info["reward"])


def _tool_call_count(simulation: dict[str, Any]) -> int:
    count = 0
    for message in simulation.get("messages") or []:
        if not isinstance(message, dict):
            continue
        calls = message.get("tool_calls") or message.get("function_calls") or []
        count += 1 if isinstance(calls, dict) else len(calls)
    return count


def _trace_record(path: Path, project_root: Path) -> dict[str, Any]:
    trace = _read_json(path)
    nodes = trace.get("nodes") or []
    metadata = trace.get("metadata") or {}
    context_views_path = path.parent / "context_views.jsonl"
    context_views = []
    if context_views_path.exists():
        for line in context_views_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                context_views.append(value)
    selected_context_tokens = [
        float(view["selected_tokens"])
        for view in context_views
        if isinstance(view.get("selected_tokens"), (int, float))
    ]
    context_compression_ratios = [
        float(view["compression_ratio"])
        for view in context_views
        if isinstance(view.get("compression_ratio"), (int, float))
    ]
    try:
        relative_path = path.relative_to(project_root).as_posix()
    except ValueError:
        relative_path = path.as_posix()
    return {
        "trace_file": relative_path,
        "trace_session_id": trace.get("session_id"),
        "source_simulation_id": metadata.get("simulation_id"),
        "estimated_trajectory_tokens": sum(
            int(node.get("token_count") or 0)
            for node in nodes
            if isinstance(node, dict)
        ),
        "trace_node_count": len(nodes),
        "trace_edge_count": len(trace.get("edges") or []),
        "graph_validation_error_count": len(
            metadata.get("graph_validation_errors") or []
        ),
        "context_view_count": len(context_views),
        "total_selected_context_tokens": (
            sum(selected_context_tokens) if selected_context_tokens else None
        ),
        "mean_selected_context_tokens": _mean(selected_context_tokens),
        "median_selected_context_tokens": _median(selected_context_tokens),
        "maximum_selected_context_tokens": (
            max(selected_context_tokens) if selected_context_tokens else None
        ),
        "mean_context_compression_ratio": _mean(
            context_compression_ratios
        ),
        "_mtime_ns": path.stat().st_mtime_ns,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _condition_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in rows if not row["infrastructure_error"]]
    traces = [
        row for row in rows if row.get("estimated_trajectory_tokens") is not None
    ]
    selected_context_rows = [
        row
        for row in rows
        if row.get("total_selected_context_tokens") is not None
    ]
    return {
        "sessions": len(rows),
        "evaluated_sessions": len(evaluated),
        "successes": sum(row["task_success"] for row in evaluated),
        "task_success_rate": _rate(
            sum(row["task_success"] for row in evaluated), len(evaluated)
        ),
        "normal_stops": sum(row["normal_stop"] for row in evaluated),
        "normal_stop_rate": _rate(
            sum(row["normal_stop"] for row in evaluated), len(evaluated)
        ),
        "infrastructure_errors": sum(row["infrastructure_error"] for row in rows),
        "infrastructure_error_rate": _rate(
            sum(row["infrastructure_error"] for row in rows), len(rows)
        ),
        "median_tool_calls": _median(
            [float(row["tool_calls"]) for row in evaluated]
        ),
        "mean_estimated_trajectory_tokens": _mean(
            [float(row["estimated_trajectory_tokens"]) for row in traces]
        ),
        "median_estimated_trajectory_tokens": _median(
            [float(row["estimated_trajectory_tokens"]) for row in traces]
        ),
        "mean_total_selected_context_tokens": _mean(
            [
                float(row["total_selected_context_tokens"])
                for row in selected_context_rows
            ]
        ),
        "median_total_selected_context_tokens": _median(
            [
                float(row["total_selected_context_tokens"])
                for row in selected_context_rows
            ]
        ),
        "mean_turn_selected_context_tokens": _mean(
            [
                float(row["mean_selected_context_tokens"])
                for row in selected_context_rows
                if row.get("mean_selected_context_tokens") is not None
            ]
        ),
        "mean_context_compression_ratio": _mean(
            [
                float(row["mean_context_compression_ratio"])
                for row in selected_context_rows
                if row.get("mean_context_compression_ratio") is not None
            ]
        ),
        "total_actual_cost_usd": round(
            sum(float(row["total_cost_usd"]) for row in rows), 8
        ),
    }


def _exact_mcnemar_p(reference_only: int, comparator_only: int) -> float | None:
    discordant = reference_only + comparator_only
    if discordant == 0:
        return None
    tail = min(reference_only, comparator_only)
    probability = sum(
        math.comb(discordant, value) for value in range(tail + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * probability)


def _paired_bootstrap(
    deltas: list[float], *, samples: int, seed: int
) -> dict[str, float | int | None]:
    if not deltas:
        return {
            "samples": samples,
            "seed": seed,
            "mean_delta": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    rng = random.Random(seed)
    estimates = []
    size = len(deltas)
    for _ in range(samples):
        estimates.append(
            statistics.fmean(deltas[rng.randrange(size)] for _ in range(size))
        )
    estimates.sort()
    low_index = max(0, math.floor(0.025 * (samples - 1)))
    high_index = min(samples - 1, math.ceil(0.975 * (samples - 1)))
    return {
        "samples": samples,
        "seed": seed,
        "mean_delta": statistics.fmean(deltas),
        "ci95_low": estimates[low_index],
        "ci95_high": estimates[high_index],
    }


def analyze_live_matrix(
    plan: dict[str, Any],
    *,
    project_root: Path,
    results_root: Path,
    reference_manager: str = "full_trajectory",
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 300,
) -> dict[str, Any]:
    """Aggregate official rewards and compute task+trial paired comparisons."""

    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    project_root = project_root.resolve()
    results_root = results_root.resolve()
    expected_runs = int(plan["run_count"])
    expected_sessions = int(plan["session_count"])
    session_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    missing_result_files: list[str] = []
    malformed_sessions: list[str] = []

    for run in plan.get("runs") or []:
        run_id = str(run["run_id"])
        manager = str(run["manager"])
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
        trace_records = (
            [
                _trace_record(path, project_root)
                for path in trace_root.glob("*/trace.json")
            ]
            if trace_root.exists()
            else []
        )
        trace_records.sort(key=lambda item: (item["_mtime_ns"], item["trace_file"]))
        simulation_ids = {
            str(simulation.get("id") or "")
            for simulation in simulations
            if simulation.get("id")
        }
        trace_by_simulation_id: dict[str, dict[str, Any]] = {}
        legacy_trace_records: list[dict[str, Any]] = []
        for trace_record in trace_records:
            source_simulation_id = str(
                trace_record.get("source_simulation_id") or ""
            )
            if (
                source_simulation_id
                and source_simulation_id in simulation_ids
                and source_simulation_id not in trace_by_simulation_id
            ):
                trace_by_simulation_id[source_simulation_id] = trace_record
            else:
                legacy_trace_records.append(trace_record)
        legacy_trace_iterator = iter(legacy_trace_records)

        for simulation in simulations:
            reward = _reward(simulation)
            simulation_id = str(simulation.get("id") or "")
            termination = str(simulation.get("termination_reason") or "")
            infrastructure_error = termination in _INFRASTRUCTURE_TERMINATIONS
            if (reward is None and not infrastructure_error) or not simulation_id:
                malformed_sessions.append(
                    f"{run_id}:trial={simulation.get('trial')}"
                )
            trace = trace_by_simulation_id.get(simulation_id)
            if trace is None:
                trace = next(legacy_trace_iterator, {})
            row = {
                "run_id": run_id,
                "manager": manager,
                "budget": str(run["budget"]),
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
                "infrastructure_error": infrastructure_error,
                "tool_calls": _tool_call_count(simulation),
                "message_count": len(simulation.get("messages") or []),
                "duration_seconds": simulation.get("duration"),
                "agent_cost_usd": float(simulation.get("agent_cost") or 0.0),
                "user_cost_usd": float(simulation.get("user_cost") or 0.0),
            }
            row["total_cost_usd"] = (
                row["agent_cost_usd"] + row["user_cost_usd"]
            )
            row.update(
                {key: value for key, value in trace.items() if not key.startswith("_")}
            )
            session_rows.append(row)

        run_rows.append(
            {
                "run_id": run_id,
                "manager": manager,
                "budget": str(run["budget"]),
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

    trace_rows = [
        row for row in session_rows if row.get("estimated_trajectory_tokens") is not None
    ]
    graph_validation_error_count = sum(
        int(row.get("graph_validation_error_count") or 0) for row in trace_rows
    )
    zero_token_trace_count = sum(
        int(row.get("estimated_trajectory_tokens") or 0) <= 0 for row in trace_rows
    )
    complete = (
        len(run_rows) == expected_runs
        and sum(row["result_file_present"] for row in run_rows) == expected_runs
        and all(row["complete"] for row in run_rows)
        and len(session_rows) == expected_sessions
        and len(trace_rows) == expected_sessions
        and not missing_result_files
        and not malformed_sessions
        and graph_validation_error_count == 0
        and zero_token_trace_count == 0
    )

    managers = sorted({row["manager"] for row in session_rows})
    condition_metrics = {
        manager: _condition_metrics(
            [row for row in session_rows if row["manager"] == manager]
        )
        for manager in managers
    }

    by_manager_key = {
        (
            row["manager"],
            row["domain"],
            row["task_id"],
            int(row["trial"]),
        ): row
        for row in session_rows
    }
    task_trial_keys = sorted(
        {
            (row["domain"], row["task_id"], int(row["trial"]))
            for row in session_rows
            if row["manager"] == reference_manager
        }
    )
    paired_comparisons: dict[str, Any] = {}
    for comparator in managers:
        if comparator == reference_manager:
            continue
        reference_only = 0
        comparator_only = 0
        both_success = 0
        neither_success = 0
        excluded_pairs = 0
        deltas: list[float] = []
        selected_context_token_deltas: list[float] = []
        for domain, task_id, trial in task_trial_keys:
            reference = by_manager_key.get(
                (reference_manager, domain, task_id, trial)
            )
            candidate = by_manager_key.get((comparator, domain, task_id, trial))
            if (
                reference is None
                or candidate is None
                or reference["infrastructure_error"]
                or candidate["infrastructure_error"]
            ):
                excluded_pairs += 1
                continue
            reference_success = bool(reference["task_success"])
            comparator_success = bool(candidate["task_success"])
            deltas.append(float(comparator_success) - float(reference_success))
            if (
                reference.get("total_selected_context_tokens") is not None
                and candidate.get("total_selected_context_tokens") is not None
            ):
                selected_context_token_deltas.append(
                    float(candidate["total_selected_context_tokens"])
                    - float(reference["total_selected_context_tokens"])
                )
            if reference_success and comparator_success:
                both_success += 1
            elif reference_success:
                reference_only += 1
            elif comparator_success:
                comparator_only += 1
            else:
                neither_success += 1
        paired_comparisons[comparator] = {
            "reference_manager": reference_manager,
            "eligible_pairs": len(deltas),
            "excluded_pairs": excluded_pairs,
            "both_success": both_success,
            "reference_only_success": reference_only,
            "comparator_only_success": comparator_only,
            "neither_success": neither_success,
            "success_rate_delta": _mean(deltas),
            "exact_mcnemar_p": _exact_mcnemar_p(
                reference_only, comparator_only
            ),
            "paired_bootstrap": _paired_bootstrap(
                deltas, samples=bootstrap_samples, seed=bootstrap_seed
            ),
            "mean_total_selected_context_tokens_delta": _mean(
                selected_context_token_deltas
            ),
            "selected_context_token_delta_bootstrap": _paired_bootstrap(
                selected_context_token_deltas,
                samples=bootstrap_samples,
                seed=bootstrap_seed,
            ),
        }

    return {
        "schema_version": "1.0",
        "matrix_id": plan["matrix_id"],
        "complete": complete,
        "reference_manager": reference_manager,
        "definitions": {
            "task_success": "official reward equals 1 within 1e-6",
            "normal_stop": "official termination is user_stop or agent_stop",
            "infrastructure_error": "official termination is infrastructure_error",
            "infrastructure_timeout": (
                "official termination is timeout; paired analysis treats the "
                "wall-clock cutoff as infrastructure and excludes the pair"
            ),
            "pair_key": "domain + task_id + trial",
            "estimated_trajectory_tokens": "sum of all TraceGraph node token_count; this is not selected context size",
            "total_selected_context_tokens": "sum of ContextView selected_tokens over live agent turns",
        },
        "counts": {
            "expected_runs": expected_runs,
            "observed_runs": sum(
                row["result_file_present"] for row in run_rows
            ),
            "expected_sessions": expected_sessions,
            "observed_sessions": len(session_rows),
            "observed_traces": len(trace_rows),
            "infrastructure_errors": sum(
                row["infrastructure_error"] for row in session_rows
            ),
            "graph_validation_errors": graph_validation_error_count,
            "zero_token_traces": zero_token_trace_count,
            "malformed_sessions": len(malformed_sessions),
        },
        "condition_metrics": condition_metrics,
        "paired_comparisons": paired_comparisons,
        "termination_reasons": dict(
            sorted(Counter(row["termination_reason"] for row in session_rows).items())
        ),
        "missing_result_files": missing_result_files,
        "malformed_session_ids": malformed_sessions,
        "interpretation_warning": (
            str(plan.get("interpretation_warning") or "")
            + " Infrastructure-error pairs are excluded. Small pilot confidence "
            "intervals are descriptive and do not replace the preregistered final analysis."
        ).strip(),
        "runs": run_rows,
        "sessions": session_rows,
    }


def write_live_matrix_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "live_matrix_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rows = report.get("sessions") or []
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with (output_dir / "live_matrix_sessions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)
