"""Validation and expansion of reproducible live-experiment matrices."""

from __future__ import annotations

import json
import re
from typing import Any

from .phase3_gates import evaluate_p2_status

from .capture import TOKEN_ACCOUNTING_VERSION

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
    "acon_official",
    "acon_official_with_failure_cards",
    "ours_without_graph_edges",
    "ours_without_lifecycle_states",
    "ours_without_failure_retention",
    "ours_without_constraint_retention",
    "raw_hard_failure_retention",
    "full_ours",
    "decision_state_compiler",
    "acon_official_with_gdsc_state",
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
    inter_run_delay_seconds = float(config.get("inter_run_delay_seconds", 0.0))
    if inter_run_delay_seconds < 0:
        raise ValueError("inter_run_delay_seconds must be non-negative")
    cost_per_session = float(config.get("estimated_cost_per_session_usd", 0.0))
    free_provider = bool(config.get("free_provider", False))
    if cost_per_session < 0 or (cost_per_session == 0 and not free_provider):
        raise ValueError(
            "estimated_cost_per_session_usd must be positive unless "
            "free_provider=true"
        )
    pricing_snapshot = config.get("pricing_snapshot") or {}
    if free_provider:
        if not isinstance(pricing_snapshot, dict):
            raise ValueError("free_provider requires a pricing_snapshot object")
        required_pricing = {
            "source_url",
            "checked_at",
            "input_usd_per_mtok",
            "output_usd_per_mtok",
        }
        missing_pricing = sorted(required_pricing - set(pricing_snapshot))
        if missing_pricing:
            raise ValueError(
                "free_provider pricing_snapshot is missing: "
                + ", ".join(missing_pricing)
            )
        if float(pricing_snapshot["input_usd_per_mtok"]) != 0 or float(
            pricing_snapshot["output_usd_per_mtok"]
        ) != 0:
            raise ValueError("free_provider requires zero input/output pricing")

    agent_model = str(config.get("agent_model", ""))
    user_model = str(config.get("user_model", ""))
    evaluator_model = str(config.get("evaluator_model", ""))
    if not agent_model or not user_model:
        raise ValueError("agent_model and user_model are required")
    token_accounting = str(
        config.get("token_accounting", TOKEN_ACCOUNTING_VERSION)
    )
    if token_accounting != TOKEN_ACCOUNTING_VERSION:
        raise ValueError(
            "matrix token_accounting must match the runtime implementation: "
            f"{TOKEN_ACCOUNTING_VERSION}"
        )

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
        run_label = str(entry.get("run_label") or manager)
        if not _SAFE_ID.fullmatch(run_label):
            raise ValueError(
                f"condition {manager!r} run_label must be a safe identifier"
            )
        condition_key = (manager, budget)
        if condition_key in seen_conditions:
            raise ValueError(f"duplicate condition: {manager}/{budget}")
        seen_conditions.add(condition_key)
        conditions.append(
            {"manager": manager, "budget": budget, "run_label": run_label}
        )

    normalize_user_stop = bool(config.get("normalize_user_stop", False))
    runs: list[dict[str, Any]] = []
    for condition in conditions:
        budget_slug = re.sub(r"[^A-Za-z0-9_-]", "_", condition["budget"])
        for domain in domains:
            for task_id in domain["task_ids"]:
                task_slug = re.sub(r"[^A-Za-z0-9_-]", "_", task_id)
                run_slug = (
                    f"{matrix_id}_{domain['name']}_{task_slug}_"
                    f"{condition['run_label']}_b{budget_slug}"
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
                        "evaluator_model": evaluator_model,
                        "normalize_user_stop": normalize_user_stop,
                        "trials": trials,
                        "base_seed": base_seed,
                        "max_steps": max_steps,
                        "timeout_seconds": timeout_seconds,
                        "token_accounting": token_accounting,
                        "save_to": run_slug,
                        "trace_output_dir": f"outputs/tau3_live/{matrix_id}/{run_slug}",
                    }
                )

    session_count = len(runs) * trials
    max_external_sessions = int(config.get("max_external_sessions", session_count))
    if max_external_sessions <= 0:
        raise ValueError("max_external_sessions must be positive")
    if session_count > max_external_sessions:
        raise ValueError(
            f"planned {session_count} sessions exceed max_external_sessions="
            f"{max_external_sessions}"
        )
    return {
        "schema_version": "1.0",
        "matrix_id": matrix_id,
        "description": str(config.get("description", "")),
        "agent_model": agent_model,
        "user_model": user_model,
        "evaluator_model": evaluator_model,
        "normalize_user_stop": normalize_user_stop,
        "token_accounting": token_accounting,
        "paired_invariants": {
            "base_seed": base_seed,
            "trials": trials,
            "max_steps": max_steps,
            "timeout_seconds": timeout_seconds,
            "inter_run_delay_seconds": inter_run_delay_seconds,
            "token_accounting": token_accounting,
            "evaluator_model": evaluator_model,
            "task_ids_by_domain": {item["name"]: item["task_ids"] for item in domains},
        },
        "inter_run_delay_seconds": inter_run_delay_seconds,
        "conditions": conditions,
        "run_count": len(runs),
        "session_count": session_count,
        "estimated_cost_per_session_usd": cost_per_session,
        "estimated_total_cost_usd": round(session_count * cost_per_session, 6),
        "free_provider": free_provider,
        "pricing_snapshot": pricing_snapshot,
        "max_external_sessions": max_external_sessions,
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


def require_phase3_p4_go(report: dict[str, Any] | None) -> None:
    """Fail closed before any P4 expansion matrix is executed."""

    if not isinstance(report, dict):
        raise ValueError("P4 execution requires a phase-three gate report")
    if not bool((report.get("p4") or {}).get("go_gate_passed")):
        blockers = (report.get("p4") or {}).get("blockers") or ["unknown"]
        raise ValueError(
            "P4 execution is not authorized by the phase-three Go gate: "
            + ", ".join(str(item) for item in blockers)
        )


def require_phase3_p2_construct_gate(report: dict[str, Any] | None) -> None:
    """Fail closed before a formal P3 matrix is executed."""

    status = evaluate_p2_status(report)
    if not status.get("passed"):
        raise ValueError(
            "formal P3 execution requires a passing P2 human construct report: "
            + json.dumps(status, ensure_ascii=False, sort_keys=True)
        )


def require_codex_provisional_p2(report: dict[str, Any] | None) -> None:
    """Authorize only the explicitly non-formal Codex-labelled P3 lane."""

    if not isinstance(report, dict):
        raise ValueError("provisional P3 execution requires a Codex P2 report")
    checks = {
        "complete": bool(report.get("complete")),
        "chain_count_at_least_60": int(report.get("chain_count") or 0) >= 60,
        "codex_provenance": report.get("annotation_provenance")
        == "codex_provisional",
        "not_human_gold": not bool(report.get("human_independent_annotations")),
        "no_unresolved_adjudications": int(
            report.get("unresolved_adjudications") or 0
        )
        == 0,
    }
    if not all(checks.values()):
        raise ValueError(
            "provisional P3 execution requires a structurally complete, explicitly "
            "Codex-labelled P2 report: "
            + json.dumps(checks, ensure_ascii=False, sort_keys=True)
        )


def require_gdsc_stage_gate(
    report: dict[str, Any] | None,
    stage: str,
) -> None:
    """Fail closed before a GDSC external stage unless its frozen gate passed."""

    if not isinstance(report, dict):
        raise ValueError(f"{stage} execution requires a GDSC gate report")
    stage_report = (report.get("stages") or {}).get(stage) or {}
    if not bool(stage_report.get("passed")):
        blockers = stage_report.get("blockers") or ["unknown"]
        raise ValueError(
            f"{stage} is not authorized by the GDSC gate: "
            + ", ".join(str(item) for item in blockers)
        )
