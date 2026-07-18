"""Select failure-rich benchmark tasks from saved live simulations."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Any


def _parse_content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _messages(simulation: dict[str, Any]) -> list[dict[str, Any]]:
    messages = simulation.get("messages")
    if messages is None:
        messages = simulation.get("trajectory", simulation.get("traj", []))
    if not isinstance(messages, list):
        return []
    flattened: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        tool_messages = item.get("tool_messages")
        if isinstance(tool_messages, list):
            flattened.extend(
                entry for entry in tool_messages if isinstance(entry, dict)
            )
        else:
            flattened.append(item)
    return flattened


def _task_sort_key(task_id: str) -> tuple[int, int | str]:
    try:
        return (0, int(task_id))
    except ValueError:
        return (1, task_id)


def _signature(call: dict[str, Any]) -> tuple[str, str]:
    function = call.get("function")
    if not isinstance(function, dict):
        function = {}
    name = str(call.get("name") or function.get("name") or "unknown_tool")
    arguments = call.get("arguments", function.get("arguments", {}))
    arguments = _parse_content(arguments)
    if not isinstance(arguments, dict):
        arguments = {"value": arguments}
    return (
        name,
        json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str),
    )


def _is_error(message: dict[str, Any]) -> bool:
    content = message.get("content")
    legacy_error = isinstance(content, str) and content.lstrip().lower().startswith(
        ("error:", "error ")
    )
    structured_error = False
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        structured_error = isinstance(parsed, dict) and bool(parsed.get("error"))
    elif isinstance(content, dict):
        structured_error = bool(content.get("error"))
    return bool(message.get("error")) or legacy_error or structured_error


def _simulation_failure_metrics(simulation: dict[str, Any]) -> dict[str, Any]:
    calls_by_id: dict[str, tuple[str, str]] = {}
    failed_signatures: set[tuple[str, str]] = set()
    tool_calls = 0
    errors = 0
    retries = 0
    resolves = 0
    messages = _messages(simulation)
    for message in messages:
        tool_call_items = message.get("tool_calls") or message.get("function_calls") or []
        if isinstance(tool_call_items, dict):
            tool_call_items = [tool_call_items]
        for call in tool_call_items:
            if not isinstance(call, dict):
                continue
            tool_calls += 1
            signature = _signature(call)
            call_id = str(call.get("id") or f"anonymous_{tool_calls}")
            calls_by_id[call_id] = signature
            if signature in failed_signatures:
                retries += 1
        if str(message.get("role") or "").lower() != "tool":
            continue
        call_id = str(message.get("id") or message.get("tool_call_id") or "")
        signature = calls_by_id.get(call_id)
        if _is_error(message):
            errors += 1
            if signature is not None:
                failed_signatures.add(signature)
        elif signature is not None and signature in failed_signatures:
            resolves += 1
            failed_signatures.remove(signature)
    reward_info = simulation.get("reward_info")
    if not isinstance(reward_info, dict):
        reward_info = {}
    reward = reward_info.get("reward", simulation.get("reward"))
    return {
        "task_id": str(simulation.get("task_id")),
        "trial": int(simulation.get("trial") or 0),
        "tool_calls": tool_calls,
        "message_count": len(messages),
        "errors": errors,
        "retries": retries,
        "resolves": resolves,
        "task_success": bool(
            isinstance(reward, (int, float)) and abs(float(reward) - 1.0) <= 1e-6
        ),
        "termination_reason": str(simulation.get("termination_reason") or ""),
    }


def analyze_failure_rich_tasks(
    payloads: dict[str, dict[str, Any]],
    *,
    split_membership: dict[str, dict[str, set[str]]] | None = None,
    top_per_domain: int = 5,
) -> dict[str, Any]:
    """Rank tasks using observed error/retry signals in saved full trajectories."""

    if top_per_domain <= 0:
        raise ValueError("top_per_domain must be positive")
    domain_results: dict[str, Any] = {}
    selected_tasks: dict[str, list[str]] = {}
    for domain, payload in sorted(payloads.items()):
        simulations = payload.get("simulations") or []
        task_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for simulation in simulations:
            if isinstance(simulation, dict):
                metrics = _simulation_failure_metrics(simulation)
                task_rows[metrics["task_id"]].append(metrics)
        aggregates = []
        for task_id, rows in sorted(
            task_rows.items(), key=lambda item: _task_sort_key(item[0])
        ):
            split = None
            if split_membership and domain in split_membership:
                for name, task_ids in split_membership[domain].items():
                    if task_id in task_ids:
                        split = name
                        break
            aggregate = {
                "domain": domain,
                "task_id": task_id,
                "split": split,
                "sessions": len(rows),
                "successes": sum(row["task_success"] for row in rows),
                "task_success_rate": statistics.fmean(
                    float(row["task_success"]) for row in rows
                ),
                "error_sessions": sum(row["errors"] > 0 for row in rows),
                "error_session_rate": statistics.fmean(
                    float(row["errors"] > 0) for row in rows
                ),
                "error_count": sum(row["errors"] for row in rows),
                "retry_sessions": sum(row["retries"] > 0 for row in rows),
                "retry_count": sum(row["retries"] for row in rows),
                "resolve_sessions": sum(row["resolves"] > 0 for row in rows),
                "resolve_count": sum(row["resolves"] for row in rows),
                "mean_tool_calls": statistics.fmean(
                    row["tool_calls"] for row in rows
                ),
                "mean_message_count": statistics.fmean(
                    row["message_count"] for row in rows
                ),
            }
            aggregate["failure_rich"] = aggregate["error_sessions"] > 0
            aggregates.append(aggregate)
        aggregates.sort(
            key=lambda row: (
                -row["retry_sessions"],
                -row["error_sessions"],
                -row["error_count"],
                -row["mean_tool_calls"],
                _task_sort_key(row["task_id"]),
            )
        )
        selected = [
            row["task_id"]
            for row in aggregates
            if row["failure_rich"]
        ][:top_per_domain]
        selected_tasks[domain] = selected
        domain_results[domain] = {
            "simulation_count": len(simulations),
            "task_count": len(aggregates),
            "failure_rich_task_count": sum(
                row["failure_rich"] for row in aggregates
            ),
            "retry_task_count": sum(row["retry_sessions"] > 0 for row in aggregates),
            "selected_tasks": selected,
            "tasks": aggregates,
        }
    return {
        "schema_version": "1.0",
        "ranking_policy": (
            "Observed retry-session count, then error-session count, total errors, "
            "mean tool calls, and numeric task id. Error/retry detection mirrors "
            "the TraceGraph τ importer and uses saved full-trajectory messages."
        ),
        "interpretation_warning": (
            "Historical model failures are a task-selection signal, not a guarantee "
            "that another model or context manager will reproduce the same errors."
        ),
        "top_per_domain": top_per_domain,
        "selected_tasks": selected_tasks,
        "domains": domain_results,
    }
