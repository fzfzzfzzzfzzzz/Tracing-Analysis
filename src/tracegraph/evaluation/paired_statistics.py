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





def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _pass_hat_k(num_trials: int, success_count: int, k: int) -> float:
    if num_trials < k:
        raise ValueError("num_trials must be at least k")
    return math.comb(success_count, k) / math.comb(num_trials, k)


def _pass_hat_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_task: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row["infrastructure_error"]:
            by_task[(row["domain"], row["task_id"])].append(row)
    if not by_task:
        return {
            "task_count": 0,
            "minimum_evaluated_trials_per_task": 0,
            "pass_hat_ks": {},
        }
    minimum_trials = min(len(task_rows) for task_rows in by_task.values())
    values = {}
    for k in range(1, minimum_trials + 1):
        values[k] = statistics.fmean(
            _pass_hat_k(
                len(task_rows),
                sum(bool(row["task_success"]) for row in task_rows),
                k,
            )
            for task_rows in by_task.values()
        )
    return {
        "task_count": len(by_task),
        "minimum_evaluated_trials_per_task": minimum_trials,
        "pass_hat_ks": values,
    }


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
    provider_input_rows = [
        row
        for row in evaluated
        if row.get("agent_provider_input_tokens") is not None
    ]
    provider_output_rows = [
        row
        for row in evaluated
        if row.get("agent_provider_output_tokens") is not None
    ]
    provider_input_tokens = sum(
        float(row["agent_provider_input_tokens"])
        for row in provider_input_rows
    )
    provider_input_calls = sum(
        int(row["agent_provider_generation_calls"])
        for row in provider_input_rows
    )
    protocol_rows = [
        row
        for row in traces
        if row.get("total_protocol_closed_message_tokens") is not None
    ]
    recovery_rows = [
        row for row in traces if row.get("mean_recovery_steps") is not None
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
        "agent_provider_input_usage_sessions": len(provider_input_rows),
        "agent_provider_input_usage_coverage": _rate(
            len(provider_input_rows), len(evaluated)
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
        "mean_total_graph_selected_representation_tokens": _mean(
            [
                float(row["total_graph_selected_representation_tokens"])
                for row in traces
                if row.get("total_graph_selected_representation_tokens") is not None
            ]
        ),
        "mean_total_protocol_closed_message_tokens": _mean(
            [
                float(row["total_protocol_closed_message_tokens"])
                for row in protocol_rows
            ]
        ),
        "mean_repeated_failed_action_count": _mean(
            [float(row["repeated_failed_action_count"]) for row in traces]
        ),
        "mean_repeated_invalid_action_count": _mean(
            [float(row["repeated_invalid_action_count"]) for row in traces]
        ),
        "mean_recovery_steps": _mean(
            [float(row["mean_recovery_steps"]) for row in recovery_rows]
        ),
        "resolved_failure_sessions": len(recovery_rows),
        "failure_card_sessions": sum(
            int(row.get("failure_card_turns") or 0) > 0 for row in traces
        ),
        "budget_infeasible_sessions": sum(
            int(row.get("budget_infeasible_turns") or 0) > 0 for row in traces
        ),
        "mean_raw_failure_messages_selected": _mean(
            [float(row["raw_failure_messages_selected"]) for row in traces]
        ),
        "mean_agent_provider_input_tokens": _mean(
            [
                float(row["agent_provider_input_tokens"])
                for row in provider_input_rows
            ]
        ),
        "median_agent_provider_input_tokens": _median(
            [
                float(row["agent_provider_input_tokens"])
                for row in provider_input_rows
            ]
        ),
        "mean_agent_provider_output_tokens": _mean(
            [
                float(row["agent_provider_output_tokens"])
                for row in provider_output_rows
            ]
        ),
        "mean_agent_provider_input_tokens_per_call": (
            provider_input_tokens / provider_input_calls
            if provider_input_calls
            else None
        ),
        "total_actual_cost_usd": round(
            sum(float(row["total_cost_usd"]) for row in rows), 8
        ),
        **_pass_hat_metrics(rows),
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


def _holm_adjust(p_values: dict[str, float | None]) -> dict[str, float | None]:
    adjusted: dict[str, float | None] = {name: None for name in p_values}
    ranked = sorted(
        (float(value), name)
        for name, value in p_values.items()
        if value is not None
    )
    running_max = 0.0
    count = len(ranked)
    for rank, (value, name) in enumerate(ranked):
        corrected = min(1.0, (count - rank) * value)
        running_max = max(running_max, corrected)
        adjusted[name] = running_max
    return adjusted
