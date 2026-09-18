"""Phase 6 controlled metrics and paired statistical summaries."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _rate(rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
    values = [bool(row[field]) for row in rows if row.get(field) is not None]
    return sum(values) / len(values) if values else None


def summarize_manager_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_manager: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_manager[str(row["manager_id"])].append(row)
    result: dict[str, Any] = {}
    for manager_id, manager_rows in sorted(by_manager.items()):
        reactivate = [row for row in manager_rows if row["fork_type"] == "REACTIVATE"]
        distractor = [row for row in manager_rows if row["fork_type"] == "DISTRACTOR"]
        result[manager_id] = {
            "episodes": len(manager_rows),
            "current_task_success_rate": _rate(manager_rows, "current_task_success"),
            "reactivate_success_rate": _rate(reactivate, "current_task_success"),
            "continue_success_rate": _rate(
                [row for row in manager_rows if row["fork_type"] == "CONTINUE"],
                "current_task_success",
            ),
            "distractor_success_rate": _rate(distractor, "current_task_success"),
            "required_subgraph_recall_mean": _mean(
                [float(row["required_subgraph_recall"]) for row in reactivate]
            ),
            "required_subgraph_precision_mean": _mean(
                [float(row["required_subgraph_precision"]) for row in reactivate]
            ),
            "anchor_accuracy_mean": _mean(
                [float(row["anchor_accuracy"]) for row in reactivate]
            ),
            "distractor_false_reactivation_rate": _rate(
                distractor, "distractor_false_reactivation"
            ),
            "unsafe_eviction_node_rate_max": max(
                (float(row["unsafe_eviction_node_rate"]) for row in manager_rows),
                default=0.0,
            ),
            "unsafe_eviction_token_rate_max": max(
                (float(row["unsafe_eviction_token_rate"]) for row in manager_rows),
                default=0.0,
            ),
            "dormant_eviction_node_coverage_mean": _mean(
                [float(row["dormant_eviction_node_coverage"]) for row in manager_rows]
            ),
            "dormant_eviction_token_coverage_mean": _mean(
                [float(row["dormant_eviction_token_coverage"]) for row in manager_rows]
            ),
            "pinned_evidence_recall_min": min(
                (float(row["pinned_evidence_recall"]) for row in manager_rows),
                default=1.0,
            ),
            "protocol_valid_rate": _rate(manager_rows, "protocol_valid"),
            "archive_integrity_rate": _rate(manager_rows, "archive_integrity"),
            "active_context_tokens_median": _median(
                [float(row["active_context_tokens"]) for row in manager_rows]
            ),
            "serialized_episode_tokens_median": _median(
                [float(row["serialized_episode_tokens"]) for row in manager_rows]
            ),
            "reactivation_tokens_mean": _mean(
                [float(row["reactivation_tokens"]) for row in manager_rows]
            ),
            "reacquisition_tool_calls_total": sum(
                int(row["reacquisition_tool_calls"]) for row in manager_rows
            ),
            "reacquisition_observation_tokens_total": sum(
                int(row["reacquisition_observation_tokens"]) for row in manager_rows
            ),
            "superseded_as_current_errors": sum(
                int(bool(row["superseded_as_current_error"])) for row in manager_rows
            ),
            "duplicate_side_effect_count": sum(
                int(row["duplicate_side_effect_count"]) for row in manager_rows
            ),
            "provider_requests": sum(int(row.get("provider_requests", 0)) for row in manager_rows),
        }
    return result


def _cluster_values(
    rows: Sequence[Mapping[str, Any]],
    manager_id: str,
    field: str,
    *,
    fork_type: str | None = None,
) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row["manager_id"] != manager_id:
            continue
        if fork_type is not None and row["fork_type"] != fork_type:
            continue
        values[str(row["prefix_id"])].append(float(row[field]))
    return {key: statistics.fmean(items) for key, items in values.items()}


def paired_cluster_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    left_manager: str,
    right_manager: str,
    field: str,
    *,
    fork_type: str | None = None,
    samples: int = 10_000,
    seed: int = 20260830,
) -> dict[str, Any]:
    if samples <= 0:
        raise ValueError("bootstrap samples must be positive")
    left = _cluster_values(rows, left_manager, field, fork_type=fork_type)
    right = _cluster_values(rows, right_manager, field, fork_type=fork_type)
    keys = sorted(set(left).intersection(right))
    if not keys:
        raise ValueError("paired bootstrap has no shared prefix clusters")
    differences = [left[key] - right[key] for key in keys]
    rng = random.Random(seed)
    estimates = []
    for _ in range(samples):
        estimates.append(
            statistics.fmean(differences[rng.randrange(len(differences))] for _ in keys)
        )
    estimates.sort()
    lower = estimates[max(0, math.floor(0.025 * samples))]
    upper = estimates[min(samples - 1, math.ceil(0.975 * samples) - 1)]
    return {
        "left_manager": left_manager,
        "right_manager": right_manager,
        "field": field,
        "fork_type": fork_type,
        "clusters": len(keys),
        "paired_mean_difference": statistics.fmean(differences),
        "paired_median_difference": statistics.median(differences),
        "bootstrap_samples": samples,
        "ci95": [lower, upper],
        "seed": seed,
    }


def exact_paired_sign_pvalue(differences: Sequence[float]) -> float:
    nonzero = [value for value in differences if value != 0]
    n = len(nonzero)
    if n == 0:
        return 1.0
    positive = sum(value > 0 for value in nonzero)
    tail = min(positive, n - positive)
    probability = sum(math.comb(n, value) for value in range(tail + 1)) / (2**n)
    return min(1.0, 2.0 * probability)


def holm_adjust(pvalues: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for index, (name, value) in enumerate(ordered):
        current = min(1.0, (total - index) * float(value))
        running = max(running, current)
        adjusted[name] = running
    return {name: adjusted[name] for name in sorted(adjusted)}


def statistical_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    bootstrap_samples: int = 10_000,
    seed: int = 20260830,
) -> dict[str, Any]:
    comparisons = {
        "m5_vs_m4_recall": (
            "M5_lifecycle_causal_reactivation",
            "M4_lifecycle_flat_reactivation",
            "required_subgraph_recall",
            "REACTIVATE",
        ),
        "m5_vs_m4_precision": (
            "M5_lifecycle_causal_reactivation",
            "M4_lifecycle_flat_reactivation",
            "required_subgraph_precision",
            "REACTIVATE",
        ),
        "m5_vs_m3_reactivate_success": (
            "M5_lifecycle_causal_reactivation",
            "M3_lifecycle_eviction_only",
            "current_task_success",
            "REACTIVATE",
        ),
        "m5_vs_m0_active_tokens": (
            "M5_lifecycle_causal_reactivation",
            "M0_full_history",
            "active_context_tokens",
            None,
        ),
    }
    reports: dict[str, Any] = {}
    pvalues: dict[str, float] = {}
    for offset, (name, (left, right, field, fork_type)) in enumerate(comparisons.items()):
        report = paired_cluster_bootstrap(
            rows,
            left,
            right,
            field,
            fork_type=fork_type,
            samples=bootstrap_samples,
            seed=seed + offset,
        )
        left_values = _cluster_values(rows, left, field, fork_type=fork_type)
        right_values = _cluster_values(rows, right, field, fork_type=fork_type)
        keys = sorted(set(left_values).intersection(right_values))
        pvalue = exact_paired_sign_pvalue(
            [left_values[key] - right_values[key] for key in keys]
        )
        report["sign_test_pvalue"] = pvalue
        reports[name] = report
        pvalues[name] = pvalue
    adjusted = holm_adjust(pvalues)
    for name, value in adjusted.items():
        reports[name]["holm_adjusted_pvalue"] = value
    return {
        "schema_version": "phase6_statistical_report_v1",
        "cluster_unit": "prefix_id",
        "comparisons": reports,
        "multiplicity": "Holm",
    }
