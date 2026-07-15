"""Structural budget selection from mandatory-context distributions."""

from __future__ import annotations

import math
import statistics
from typing import Any


def _nearest_rank(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def summarize_budget(
    *,
    budget: int,
    manager_rows: list[dict[str, Any]],
    oracle_rows: list[dict[str, Any]],
    maximum_overflow_rate: float,
) -> dict[str, Any]:
    """Summarize one budget without inventing counterfactual task outcomes."""

    if budget <= 0:
        raise ValueError("budget must be positive")
    if not 0 <= maximum_overflow_rate <= 1:
        raise ValueError("maximum_overflow_rate must be in [0, 1]")
    if not manager_rows or not oracle_rows:
        raise ValueError("manager_rows and oracle_rows must be non-empty")
    if len(manager_rows) != len(oracle_rows):
        raise ValueError("manager_rows and oracle_rows must have equal length")
    mandatory_tokens = [int(row["input_tokens"]) for row in oracle_rows]
    overflow_count = sum(value > budget for value in mandatory_tokens)
    n = len(mandatory_tokens)
    manager_tokens = [int(row["input_tokens"]) for row in manager_rows]
    manager_overflow_count = sum(value > budget for value in manager_tokens)
    manager_overflow_rate = manager_overflow_count / len(manager_tokens)
    unsafe_removals = sum(int(row["unsafe_removal_count"]) for row in manager_rows)
    minimum_constraint_retention = min(
        float(row["constraint_retention"]) for row in manager_rows
    )
    minimum_failure_retention = min(
        float(row["unresolved_failure_retention"]) for row in manager_rows
    )
    minimum_evidence_retention = min(
        float(row["evidence_retention"]) for row in manager_rows
    )
    structural_safe = (
        unsafe_removals == 0
        and minimum_constraint_retention >= 1.0
        and minimum_failure_retention >= 1.0
        and minimum_evidence_retention >= 1.0
    )
    overflow_rate = overflow_count / n
    return {
        "budget": budget,
        "sessions": len(manager_rows),
        "mandatory_context": {
            "minimum_tokens": min(mandatory_tokens),
            "median_tokens": float(statistics.median(mandatory_tokens)),
            "p95_tokens": _nearest_rank(mandatory_tokens, 0.95),
            "maximum_tokens": max(mandatory_tokens),
            "overflow_count": overflow_count,
            "overflow_rate": overflow_rate,
        },
        "full_ours": {
            "minimum_input_tokens": min(manager_tokens),
            "median_input_tokens": float(statistics.median(manager_tokens)),
            "p95_input_tokens": _nearest_rank(manager_tokens, 0.95),
            "maximum_input_tokens": max(manager_tokens),
            "mean_input_tokens": statistics.fmean(manager_tokens),
            "budget_overflow_count": manager_overflow_count,
            "budget_overflow_rate": manager_overflow_rate,
            "mean_compression_ratio": statistics.fmean(
                float(row["compression_ratio"]) for row in manager_rows
            ),
            "minimum_constraint_retention": minimum_constraint_retention,
            "minimum_unresolved_failure_retention": minimum_failure_retention,
            "minimum_evidence_retention": minimum_evidence_retention,
            "unsafe_removal_count": unsafe_removals,
        },
        "maximum_allowed_overflow_rate": maximum_overflow_rate,
        "structural_safe": structural_safe,
        "feasible": (
            structural_safe
            and overflow_rate <= maximum_overflow_rate
            and manager_overflow_rate <= maximum_overflow_rate
        ),
    }


def choose_budget(summaries: list[dict[str, Any]]) -> int | None:
    feasible = sorted(
        int(item["budget"]) for item in summaries if bool(item.get("feasible"))
    )
    return feasible[0] if feasible else None
