"""Definitions moved from ``tracegraph.paired``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .paired_constants import (
    _INFRASTRUCTURE_TERMINATIONS as _INFRASTRUCTURE_TERMINATIONS,
    _NORMAL_TERMINATIONS as _NORMAL_TERMINATIONS,
)



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
                "agent_provider_input_tokens": _provider_usage_sum(
                    simulation,
                    role="assistant",
                    keys=("prompt_tokens", "input_tokens", "input_token_count"),
                ),
                "agent_provider_generation_calls": _provider_usage_count(
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
                "user_provider_input_tokens": _provider_usage_sum(
                    simulation,
                    role="user",
                    keys=("prompt_tokens", "input_tokens", "input_token_count"),
                ),
                "user_provider_output_tokens": _provider_usage_sum(
                    simulation,
                    role="user",
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
    domains = sorted({row["domain"] for row in session_rows})
    domain_condition_metrics = {
        manager: {
            domain: _condition_metrics(
                [
                    row
                    for row in session_rows
                    if row["manager"] == manager and row["domain"] == domain
                ]
            )
            for domain in domains
        }
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
        agent_provider_input_token_deltas: list[float] = []
        agent_provider_input_per_call_deltas: list[float] = []
        protocol_closed_token_deltas: list[float] = []
        repeated_invalid_action_deltas: list[float] = []
        recovery_step_deltas: list[float] = []
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
            if (
                reference.get("agent_provider_input_tokens") is not None
                and candidate.get("agent_provider_input_tokens") is not None
            ):
                agent_provider_input_token_deltas.append(
                    float(candidate["agent_provider_input_tokens"])
                    - float(reference["agent_provider_input_tokens"])
                )
                reference_calls = int(
                    reference.get("agent_provider_generation_calls") or 0
                )
                candidate_calls = int(
                    candidate.get("agent_provider_generation_calls") or 0
                )
                if reference_calls and candidate_calls:
                    agent_provider_input_per_call_deltas.append(
                        float(candidate["agent_provider_input_tokens"])
                        / candidate_calls
                        - float(reference["agent_provider_input_tokens"])
                        / reference_calls
                    )
            if (
                reference.get("total_protocol_closed_message_tokens") is not None
                and candidate.get("total_protocol_closed_message_tokens") is not None
            ):
                protocol_closed_token_deltas.append(
                    float(candidate["total_protocol_closed_message_tokens"])
                    - float(reference["total_protocol_closed_message_tokens"])
                )
            if (
                reference.get("repeated_invalid_action_count") is not None
                and candidate.get("repeated_invalid_action_count") is not None
            ):
                repeated_invalid_action_deltas.append(
                    float(candidate["repeated_invalid_action_count"])
                    - float(reference["repeated_invalid_action_count"])
                )
            if (
                reference.get("mean_recovery_steps") is not None
                and candidate.get("mean_recovery_steps") is not None
            ):
                recovery_step_deltas.append(
                    float(candidate["mean_recovery_steps"])
                    - float(reference["mean_recovery_steps"])
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
            "mean_agent_provider_input_tokens_delta": _mean(
                agent_provider_input_token_deltas
            ),
            "agent_provider_input_token_delta_bootstrap": _paired_bootstrap(
                agent_provider_input_token_deltas,
                samples=bootstrap_samples,
                seed=bootstrap_seed,
            ),
            "mean_agent_provider_input_tokens_per_call_delta": _mean(
                agent_provider_input_per_call_deltas
            ),
            "agent_provider_input_tokens_per_call_delta_bootstrap": (
                _paired_bootstrap(
                    agent_provider_input_per_call_deltas,
                    samples=bootstrap_samples,
                    seed=bootstrap_seed,
                )
            ),
            "mean_total_protocol_closed_message_tokens_delta": _mean(
                protocol_closed_token_deltas
            ),
            "protocol_closed_message_token_delta_bootstrap": _paired_bootstrap(
                protocol_closed_token_deltas,
                samples=bootstrap_samples,
                seed=bootstrap_seed,
            ),
            "mean_repeated_invalid_action_count_delta": _mean(
                repeated_invalid_action_deltas
            ),
            "repeated_invalid_action_delta_bootstrap": _paired_bootstrap(
                repeated_invalid_action_deltas,
                samples=bootstrap_samples,
                seed=bootstrap_seed,
            ),
            "mean_recovery_steps_delta": _mean(recovery_step_deltas),
            "recovery_step_delta_bootstrap": _paired_bootstrap(
                recovery_step_deltas,
                samples=bootstrap_samples,
                seed=bootstrap_seed,
            ),
        }
    holm_adjusted = _holm_adjust(
        {
            comparator: comparison["exact_mcnemar_p"]
            for comparator, comparison in paired_comparisons.items()
        }
    )
    for comparator, adjusted_p in holm_adjusted.items():
        paired_comparisons[comparator]["holm_adjusted_mcnemar_p"] = adjusted_p

    return {
        "schema_version": "1.0",
        "matrix_id": plan["matrix_id"],
        "complete": complete,
        "reference_manager": reference_manager,
        "definitions": {
            "task_success": "official reward equals 1 within 1e-6",
            "pass_hat_k": (
                "mean over tasks of C(successful evaluated trials, k) / "
                "C(evaluated trials, k); infrastructure sessions are excluded"
            ),
            "normal_stop": "official termination is user_stop or agent_stop",
            "infrastructure_error": "official termination is infrastructure_error",
            "infrastructure_timeout": (
                "official termination is timeout; paired analysis treats the "
                "wall-clock cutoff as infrastructure and excludes the pair"
            ),
            "pair_key": "domain + task_id + trial",
            "estimated_trajectory_tokens": "sum of all TraceGraph node token_count; this is not selected context size",
            "total_selected_context_tokens": "sum of ContextView selected_tokens over live agent turns",
            "total_protocol_closed_message_tokens": (
                "sum of raw provider-message tokens after tool-call/result closure; "
                "compact context fragments are represented separately"
            ),
            "repeated_invalid_action_count": (
                "exact-signature retried_by calls whose later result is also negative"
            ),
            "mean_recovery_steps": (
                "mean positive step distance from negative evidence to its resolved_by target"
            ),
            "agent_provider_input_tokens": (
                "sum of upstream assistant-message prompt/input usage; this is "
                "the actual agent-generation input telemetry when present"
            ),
            "agent_provider_input_tokens_per_call": (
                "agent provider input tokens divided by assistant generations; "
                "reported alongside cumulative input because conditions may "
                "produce different trajectory lengths"
            ),
            "token_accounting": (
                "content_estimate_v2 excludes provider prompt history from "
                "individual graph-node sizes and context-budget selection"
            ),
            "holm_adjusted_mcnemar_p": (
                "Holm step-down family-wise correction over comparator "
                "McNemar p-values for the selected reference manager"
            ),
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
            "missing_agent_provider_input_usage": sum(
                not row["infrastructure_error"]
                and row.get("agent_provider_input_tokens") is None
                for row in session_rows
            ),
            "token_accounting_versions": dict(
                sorted(
                    Counter(
                        str(row.get("token_accounting") or "legacy_unspecified")
                        for row in trace_rows
                    ).items()
                )
            ),
        },
        "condition_metrics": condition_metrics,
        "domain_condition_metrics": domain_condition_metrics,
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


# Imported after definitions so mutually-referential helpers initialize safely.
from .paired_records import (
    _provider_usage_count as _provider_usage_count,
    _provider_usage_sum as _provider_usage_sum,
    _read_json as _read_json,
    _reward as _reward,
    _tool_call_count as _tool_call_count,
    _trace_record as _trace_record,
)

from .paired_statistics import (
    _condition_metrics as _condition_metrics,
    _exact_mcnemar_p as _exact_mcnemar_p,
    _holm_adjust as _holm_adjust,
    _mean as _mean,
    _paired_bootstrap as _paired_bootstrap,
)
