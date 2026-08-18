"""Aggregation and fail-closed attainability logic for GDSC R2.1."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [
        float(row[key])
        for row in rows
        if row.get(key) is not None
    ]
    return statistics.median(values) if values else None


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    return (
        statistics.fmean(float(bool(row.get(key))) for row in rows)
        if rows
        else 0.0
    )


def summarize_cost_attribution(
    rows: Sequence[Mapping[str, Any]],
    *,
    threshold: float = 0.30,
) -> dict[str, Any]:
    """Summarize exact request rows and make a conservative binary decision.

    ``fixed_floor_max_reduction`` is an optimistic upper bound obtained by
    removing all dynamic history while retaining the full policy and native
    tool schemas.  If even this bound misses the threshold, the target is
    unreachable under the frozen constraints.  A reachable decision requires
    an explicitly serialized, hard-covering constructive request.
    """

    if not 0 < threshold < 1:
        raise ValueError("threshold must be in (0, 1)")
    if not rows:
        raise ValueError("cost attribution rows are empty")
    point_ids = [str(row.get("decision_point_id") or "") for row in rows]
    if not all(point_ids) or len(point_ids) != len(set(point_ids)):
        raise ValueError("decision_point_id values must be unique and non-empty")

    med_fixed = _median(rows, "fixed_floor_max_reduction")
    med_constructive = _median(rows, "constructive_hard_floor_reduction")
    request_match = _rate(rows, "request_hash_matches_baseline")
    runtime_request_match = _rate(rows, "runtime_prompt_hash_matches")
    raw_match = _rate(rows, "raw_cost_matches_baseline")
    compiled_match = _rate(rows, "compiled_cost_matches_baseline")
    policy_once = _rate(rows, "policy_exposed_exactly_once")
    tools_once = _rate(rows, "tool_schema_top_level_exact")
    hard_coverage = _rate(rows, "constructive_hard_coverage")

    blockers: list[str] = []
    if request_match != 1.0:
        blockers.append("request_hash_mismatch")
    if runtime_request_match != 1.0:
        blockers.append("runtime_prompt_hash_mismatch")
    if raw_match != 1.0 or compiled_match != 1.0:
        blockers.append("baseline_cost_mismatch")
    if policy_once != 1.0:
        blockers.append("policy_not_exposed_exactly_once")
    if tools_once != 1.0:
        blockers.append("tool_schema_not_exact")

    if blockers:
        decision = "measurement_invalid"
    elif med_fixed is not None and med_fixed < threshold:
        decision = "unreachable_under_frozen_fixed_cost"
    elif (
        med_constructive is not None
        and med_constructive >= threshold
        and hard_coverage == 1.0
    ):
        decision = "reachable_with_verified_constructive_request"
    else:
        decision = "indeterminate_stop"
        blockers.append("constructive_reachability_not_proven")

    by_domain: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_domain[str(row.get("domain") or "unknown")].append(row)
    domain_summary = {
        domain: {
            "decision_points": len(domain_rows),
            "median_raw_serialized_tokens": _median(
                domain_rows, "raw_serialized_tokens"
            ),
            "median_compiled_serialized_tokens": _median(
                domain_rows, "compiled_serialized_tokens"
            ),
            "median_current_reduction": _median(
                domain_rows, "current_serialized_reduction"
            ),
            "median_fixed_floor_tokens": _median(
                domain_rows, "raw_fixed_policy_tools_tokens"
            ),
            "median_fixed_floor_max_reduction": _median(
                domain_rows, "fixed_floor_max_reduction"
            ),
            "median_constructive_hard_floor_reduction": _median(
                domain_rows, "constructive_hard_floor_reduction"
            ),
        }
        for domain, domain_rows in sorted(by_domain.items())
    }
    return {
        "schema_version": "gdsc_cost_attribution_summary_v1",
        "threshold": threshold,
        "decision_point_count": len(rows),
        "unique_decision_point_count": len(set(point_ids)),
        "median_raw_serialized_tokens": _median(rows, "raw_serialized_tokens"),
        "median_compiled_serialized_tokens": _median(
            rows, "compiled_serialized_tokens"
        ),
        "median_current_serialized_reduction": _median(
            rows, "current_serialized_reduction"
        ),
        "median_fixed_floor_tokens": _median(
            rows, "raw_fixed_policy_tools_tokens"
        ),
        "median_fixed_floor_max_reduction": med_fixed,
        "median_constructive_hard_floor_tokens": _median(
            rows, "constructive_hard_floor_tokens"
        ),
        "median_constructive_hard_floor_reduction": med_constructive,
        "request_hash_match_rate": request_match,
        "runtime_prompt_hash_match_rate": runtime_request_match,
        "raw_cost_match_rate": raw_match,
        "compiled_cost_match_rate": compiled_match,
        "policy_exposed_exactly_once_rate": policy_once,
        "tool_schema_top_level_exact_rate": tools_once,
        "constructive_hard_coverage_rate": hard_coverage,
        "domains": domain_summary,
        "attainability_decision": decision,
        "diagnostic_gate_passed": decision in {
            "unreachable_under_frozen_fixed_cost",
            "reachable_with_verified_constructive_request",
        },
        "blockers": blockers,
    }
