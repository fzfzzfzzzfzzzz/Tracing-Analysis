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





def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _reward(simulation: dict[str, Any]) -> float | None:
    reward_info = simulation.get("reward_info")
    if not isinstance(reward_info, dict):
        return None
    value = reward_info.get("reward")
    if value is None:
        return None
    return float(value)


def _tool_call_count(simulation: dict[str, Any]) -> int:
    count = 0
    for message in simulation.get("messages") or []:
        if not isinstance(message, dict):
            continue
        calls = message.get("tool_calls") or message.get("function_calls") or []
        if isinstance(calls, dict):
            count += 1
        elif isinstance(calls, list):
            count += len(calls)
    return count


def _provider_usage_sum(
    simulation: dict[str, Any],
    *,
    role: str,
    keys: tuple[str, ...],
) -> float | None:
    values: list[float] = []
    for message in simulation.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != role:
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in keys:
            value = usage.get(key)
            if isinstance(value, (int, float)):
                values.append(float(value))
                break
    return sum(values) if values else None


def _reward_diagnostics(simulation: dict[str, Any]) -> dict[str, Any]:
    reward_info = simulation.get("reward_info")
    if not isinstance(reward_info, dict):
        reward_info = {}
    checks = [
        item
        for item in (reward_info.get("action_checks") or [])
        if isinstance(item, dict)
    ]
    diagnostics: dict[str, Any] = {}
    for tool_type in ("read", "write"):
        typed = [
            item
            for item in checks
            if item.get("tool_type") == tool_type
        ]
        diagnostics[f"expected_{tool_type}_actions"] = len(typed)
        diagnostics[f"correct_{tool_type}_actions"] = sum(
            bool(item.get("action_match")) for item in typed
        )
    db_check = reward_info.get("db_check")
    diagnostics["db_reward"] = (
        db_check.get("db_reward") if isinstance(db_check, dict) else None
    )
    for source_key, prefix in (
        ("nl_assertions", "nl_assertions"),
        ("communicate_checks", "communication_checks"),
    ):
        assertions = [
            item
            for item in (reward_info.get(source_key) or [])
            if isinstance(item, dict)
        ]
        diagnostics[f"expected_{prefix}"] = len(assertions)
        diagnostics[f"met_{prefix}"] = sum(bool(item.get("met")) for item in assertions)
    return diagnostics


def _failure_reasons(row: dict[str, Any]) -> list[str]:
    if row["task_success"]:
        return []
    reasons = []
    if row["infrastructure_error"]:
        reasons.append("infrastructure_error")
    elif not row["normal_stop"]:
        reasons.append("abnormal_termination")
    if row["correct_read_actions"] < row["expected_read_actions"]:
        reasons.append("read_action_mismatch")
    if row["correct_write_actions"] < row["expected_write_actions"]:
        reasons.append("write_action_mismatch")
    db_reward = row.get("db_reward")
    if db_reward is not None and float(db_reward) < 1.0:
        reasons.append("database_mismatch")
    if row["met_nl_assertions"] < row["expected_nl_assertions"]:
        reasons.append("natural_language_assertion_mismatch")
    if row["met_communication_checks"] < row["expected_communication_checks"]:
        reasons.append("communication_mismatch")
    if not reasons:
        reasons.append("other_official_reward_failure")
    return reasons


def _trace_record(path: Path, project_root: Path) -> dict[str, Any]:
    trace = _read_json(path)
    nodes = trace.get("nodes") or []
    token_count = sum(
        int(node.get("token_count") or 0)
        for node in nodes
        if isinstance(node, dict)
    )
    metadata = trace.get("metadata") or {}
    validation_errors = metadata.get("graph_validation_errors") or []
    try:
        relative_path = path.relative_to(project_root).as_posix()
    except ValueError:
        relative_path = path.as_posix()
    return {
        "trace_file": relative_path,
        "trace_session_id": trace.get("session_id"),
        "token_accounting": metadata.get("token_accounting"),
        "estimated_trajectory_tokens": token_count,
        "trace_node_count": len(nodes),
        "trace_edge_count": len(trace.get("edges") or []),
        "graph_validation_error_count": len(validation_errors),
        "_mtime_ns": path.stat().st_mtime_ns,
    }


def _median(values: list[int | float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _gate(
    *,
    value: float | None,
    threshold: float,
    operator: str,
    complete: bool,
) -> dict[str, Any]:
    if value is None:
        metric_passed = False
    elif operator == ">=":
        metric_passed = value >= threshold
    elif operator == "<=":
        metric_passed = value <= threshold
    else:
        raise ValueError(f"unsupported gate operator: {operator}")
    return {
        "value": value,
        "operator": operator,
        "threshold": threshold,
        "metric_passed": metric_passed,
        "passed": complete and metric_passed,
    }


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in rows if not row["infrastructure_error"]]
    traces = [
        row for row in rows if row.get("estimated_trajectory_tokens") is not None
    ]
    provider_input_rows = [
        row
        for row in evaluated
        if row.get("agent_provider_input_tokens") is not None
    ]
    return {
        "sessions": len(rows),
        "evaluated_sessions": len(evaluated),
        "successes": sum(row["task_success"] for row in evaluated),
        "task_success_rate": _rate(
            sum(row["task_success"] for row in evaluated), len(evaluated)
        ),
        "normal_stop_rate": _rate(
            sum(row["normal_stop"] for row in evaluated), len(evaluated)
        ),
        "median_tool_calls": _median([row["tool_calls"] for row in evaluated]),
        "median_estimated_trajectory_tokens": _median(
            [row["estimated_trajectory_tokens"] for row in traces]
        ),
        "median_agent_provider_input_tokens": _median(
            [
                row["agent_provider_input_tokens"]
                for row in provider_input_rows
            ]
        ),
        "total_actual_cost_usd": round(
            sum(row["total_cost_usd"] for row in rows), 8
        ),
    }
