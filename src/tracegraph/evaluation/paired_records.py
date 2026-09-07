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


def _provider_usage_count(
    simulation: dict[str, Any],
    *,
    role: str,
    keys: tuple[str, ...],
) -> int:
    count = 0
    for message in simulation.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != role:
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        if any(isinstance(usage.get(key), (int, float)) for key in keys):
            count += 1
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
    graph_representation_tokens = [
        float(view.get("metadata", {}).get("graph_selected_representation_tokens"))
        for view in context_views
        if isinstance(
            view.get("metadata", {}).get("graph_selected_representation_tokens"),
            (int, float),
        )
    ]
    protocol_closed_tokens = [
        float(view.get("metadata", {}).get("protocol_closed_message_tokens"))
        for view in context_views
        if isinstance(
            view.get("metadata", {}).get("protocol_closed_message_tokens"),
            (int, float),
        )
    ]
    failure_card_counts = [
        int(view.get("metadata", {}).get("failure_card_count"))
        for view in context_views
        if isinstance(
            view.get("metadata", {}).get("failure_card_count"),
            (int, float),
        )
    ]
    budget_infeasible_turns = sum(
        bool(view.get("metadata", {}).get("budget_infeasible", False))
        for view in context_views
    )
    raw_failure_messages_selected = sum(
        int(view.get("metadata", {}).get("raw_failure_messages_selected") or 0)
        for view in context_views
    )
    node_by_id = {
        str(node.get("node_id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("node_id")
    }
    edges = [edge for edge in (trace.get("edges") or []) if isinstance(edge, dict)]
    negative_result_ids = {
        node_id
        for node_id, node in node_by_id.items()
        if node.get("node_type") == "error"
        or (node.get("metadata") or {}).get("semantic_outcome")
        in {"negative", "policy_denied", "test_failed"}
    }
    results_by_call: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.get("edge_type") in {"failed_with", "produces"}:
            results_by_call[str(edge.get("source"))].append(str(edge.get("target")))
    repeated_failed_actions = 0
    repeated_invalid_actions = 0
    for edge in edges:
        if edge.get("edge_type") != "retried_by":
            continue
        later_call_id = str(edge.get("target"))
        later_failed = any(
            result_id in negative_result_ids
            for result_id in results_by_call.get(later_call_id, ())
        )
        if not later_failed:
            continue
        repeated_failed_actions += 1
        if (edge.get("metadata") or {}).get("match_type") == "exact_signature":
            repeated_invalid_actions += 1
    recovery_steps = []
    for edge in edges:
        if edge.get("edge_type") != "resolved_by":
            continue
        source = node_by_id.get(str(edge.get("source")), {})
        target = node_by_id.get(str(edge.get("target")), {})
        if isinstance(source.get("step_id"), int) and isinstance(target.get("step_id"), int):
            recovery_steps.append(max(1, int(target["step_id"]) - int(source["step_id"])))
    try:
        relative_path = path.relative_to(project_root).as_posix()
    except ValueError:
        relative_path = path.as_posix()
    return {
        "trace_file": relative_path,
        "trace_session_id": trace.get("session_id"),
        "source_simulation_id": metadata.get("simulation_id"),
        "token_accounting": metadata.get("token_accounting"),
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
        "total_graph_selected_representation_tokens": (
            sum(graph_representation_tokens) if graph_representation_tokens else None
        ),
        "total_protocol_closed_message_tokens": (
            sum(protocol_closed_tokens) if protocol_closed_tokens else None
        ),
        "failure_card_turns": sum(count > 0 for count in failure_card_counts),
        "maximum_active_failure_cards": (
            max(failure_card_counts) if failure_card_counts else None
        ),
        "budget_infeasible_turns": budget_infeasible_turns,
        "raw_failure_messages_selected": raw_failure_messages_selected,
        "repeated_failed_action_count": repeated_failed_actions,
        "repeated_invalid_action_count": repeated_invalid_actions,
        "resolved_failure_count": len(recovery_steps),
        "mean_recovery_steps": _mean([float(value) for value in recovery_steps]),
        "_mtime_ns": path.stat().st_mtime_ns,
    }


# Imported after definitions so mutually-referential helpers initialize safely.
from .paired_statistics import (
    _mean as _mean,
    _median as _median,
)
