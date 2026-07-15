"""Validation and expansion of reproducible live-experiment matrices."""

from __future__ import annotations

import re
from typing import Any

KNOWN_DOMAINS = {
    "mock",
    "airline",
    "retail",
    "telecom",
    "telecom-workflow",
    "banking_knowledge",
}

KNOWN_MANAGERS = {
    "full_trajectory",
    "last_k",
    "token_length_pruning",
    "summary_only",
    "llm_only_pruning",
    "agentdiet_style",
    "acon_style",
    "ours_without_graph_edges",
    "ours_without_lifecycle_states",
    "ours_without_failure_retention",
    "ours_without_constraint_retention",
    "full_ours",
}

_SENSITIVE_KEY_PARTS = ("api_key", "secret", "credential", "password", "access_token")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _reject_embedded_secrets(value: Any, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                raise ValueError(f"sensitive key is forbidden in matrix config: {path}.{key}")
            _reject_embedded_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_embedded_secrets(child, f"{path}[{index}]")


def _positive_int(config: dict[str, Any], name: str) -> int:
    value = int(config.get(name, 0))
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def build_matrix_plan(config: dict[str, Any]) -> dict[str, Any]:
    """Validate a secret-free matrix config and expand it into paired runs."""

    _reject_embedded_secrets(config)
    matrix_id = str(config.get("matrix_id", ""))
    if not _SAFE_ID.fullmatch(matrix_id):
        raise ValueError("matrix_id must use only letters, digits, dot, dash, and underscore")

    trials = _positive_int(config, "trials")
    max_steps = _positive_int(config, "max_steps")
    timeout_seconds = _positive_int(config, "timeout_seconds")
    base_seed = int(config.get("base_seed", 300))
    cost_per_session = float(config.get("estimated_cost_per_session_usd", 0.0))
    if cost_per_session <= 0:
        raise ValueError("estimated_cost_per_session_usd must be positive")

    agent_model = str(config.get("agent_model", ""))
    user_model = str(config.get("user_model", ""))
    if not agent_model or not user_model:
        raise ValueError("agent_model and user_model are required")

    domain_entries = config.get("domains")
    condition_entries = config.get("conditions")
    if not isinstance(domain_entries, list) or not domain_entries:
        raise ValueError("domains must be a non-empty list")
    if not isinstance(condition_entries, list) or not condition_entries:
        raise ValueError("conditions must be a non-empty list")

    domains: list[dict[str, Any]] = []
    for entry in domain_entries:
        name = str(entry.get("name", ""))
        if name not in KNOWN_DOMAINS:
            raise ValueError(f"unknown domain: {name!r}")
        task_ids = [str(task_id) for task_id in entry.get("task_ids", [])]
        if not task_ids or any(not task_id for task_id in task_ids):
            raise ValueError(f"domain {name!r} must contain task_ids")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError(f"domain {name!r} contains duplicate task_ids")
        domains.append({"name": name, "task_ids": task_ids})

    conditions: list[dict[str, str]] = []
    seen_conditions: set[tuple[str, str]] = set()
    for entry in condition_entries:
        manager = str(entry.get("manager", ""))
        budget = str(entry.get("budget", ""))
        if manager not in KNOWN_MANAGERS:
            raise ValueError(f"unknown manager: {manager!r}")
        if not budget:
            raise ValueError(f"condition {manager!r} requires budget")
        condition_key = (manager, budget)
        if condition_key in seen_conditions:
            raise ValueError(f"duplicate condition: {manager}/{budget}")
        seen_conditions.add(condition_key)
        conditions.append({"manager": manager, "budget": budget})

    normalize_user_stop = bool(config.get("normalize_user_stop", False))
    runs: list[dict[str, Any]] = []
    for condition in conditions:
        budget_slug = re.sub(r"[^A-Za-z0-9_-]", "_", condition["budget"])
        for domain in domains:
            for task_id in domain["task_ids"]:
                task_slug = re.sub(r"[^A-Za-z0-9_-]", "_", task_id)
                run_slug = (
                    f"{matrix_id}_{domain['name']}_{task_slug}_"
                    f"{condition['manager']}_b{budget_slug}"
                )
                runs.append(
                    {
                        "run_id": run_slug,
                        "domain": domain["name"],
                        "task_id": task_id,
                        "manager": condition["manager"],
                        "budget": condition["budget"],
                        "agent_model": agent_model,
                        "user_model": user_model,
                        "normalize_user_stop": normalize_user_stop,
                        "trials": trials,
                        "base_seed": base_seed,
                        "max_steps": max_steps,
                        "timeout_seconds": timeout_seconds,
                        "save_to": run_slug,
                        "trace_output_dir": f"outputs/tau3_live/{matrix_id}/{run_slug}",
                    }
                )

    session_count = len(runs) * trials
    return {
        "schema_version": "1.0",
        "matrix_id": matrix_id,
        "description": str(config.get("description", "")),
        "agent_model": agent_model,
        "user_model": user_model,
        "normalize_user_stop": normalize_user_stop,
        "paired_invariants": {
            "base_seed": base_seed,
            "trials": trials,
            "max_steps": max_steps,
            "timeout_seconds": timeout_seconds,
            "task_ids_by_domain": {item["name"]: item["task_ids"] for item in domains},
        },
        "conditions": conditions,
        "run_count": len(runs),
        "session_count": session_count,
        "estimated_cost_per_session_usd": cost_per_session,
        "estimated_total_cost_usd": round(session_count * cost_per_session, 6),
        "gates": config.get("gates", {}),
        "interpretation_warning": str(config.get("interpretation_warning", "")),
        "runs": runs,
    }


def require_execution_budget(plan: dict[str, Any], max_estimated_cost_usd: float | None) -> None:
    """Require an explicit cap covering the plan's conservative cost estimate."""

    if max_estimated_cost_usd is None:
        raise ValueError("execution requires an explicit max_estimated_cost_usd")
    if max_estimated_cost_usd < 0:
        raise ValueError("max_estimated_cost_usd must be non-negative")
    estimated = float(plan["estimated_total_cost_usd"])
    if estimated > max_estimated_cost_usd:
        raise ValueError(
            f"estimated cost ${estimated:.4f} exceeds explicit cap "
            f"${max_estimated_cost_usd:.4f}"
        )
