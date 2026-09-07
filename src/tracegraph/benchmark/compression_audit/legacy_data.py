"""Definitions moved from ``tracegraph.compression_audit``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from ...capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens





def _legacy_recoverability(prefix: Mapping[str, Any]) -> str:
    family = str(prefix.get("scenario_family", ""))
    mode = str(prefix.get("reacquisition_mode", ""))
    if family == "F4_explainable_process":
        return "R0"
    if family == "F6_side_effect_audit":
        return "R3"
    return "R1" if mode == "cheap" else "R2"


def _legacy_event_role(source_id: str, node: Mapping[str, Any]) -> str:
    lowered = source_id.lower()
    if "failed-attempt:call" in lowered or "read-old-config:call" in lowered:
        return "failed_action"
    if "failed-attempt:result" in lowered or "read-old-config:result" in lowered:
        return "failure_result"
    if "switch-decision" in lowered or "update-reason" in lowered:
        return "switch_decision"
    if "successful-alternative:call" in lowered or "read-new-config:call" in lowered:
        return "replacement_action"
    if "successful-alternative:result" in lowered or "read-new-config:result" in lowered:
        return "resolution_evidence"
    if node.get("node_type") == "error":
        return "failure_result"
    if "current" in lowered:
        return "current_fact"
    return "historical_context"


def _legacy_action(node: Mapping[str, Any] | None, fallback: str) -> tuple[str, dict[str, Any]]:
    if not node:
        return fallback, {}
    content = node.get("content")
    if isinstance(content, dict):
        name = content.get("tool_name") or node.get("metadata", {}).get("tool_name")
        arguments = content.get("arguments")
        return str(name or fallback), dict(arguments or {})
    return fallback, {}


def convert_legacy_diagnostic(
    legacy_root: Path,
) -> tuple[list[PrefixRecord], list[FailureChainGold], list[QueryRecord]]:
    prefixes_raw = load_jsonl(legacy_root / "prefixes.jsonl")
    forks_raw = load_jsonl(legacy_root / "forks.jsonl")
    forks_by_prefix = {
        str(row["prefix_id"]): row
        for row in forks_raw
        if row.get("fork_type") == "REACTIVATE"
    }
    prefixes: list[PrefixRecord] = []
    gold_rows: list[FailureChainGold] = []
    queries: list[QueryRecord] = []
    for raw in prefixes_raw:
        source_prefix_id = str(raw["prefix_id"])
        prefix_id = f"legacy:{source_prefix_id}"
        source_nodes = sorted(
            raw["graph"]["nodes"],
            key=lambda item: (int(item["step_id"]), str(item["node_id"])),
        )
        id_map = {
            str(node["node_id"]): f"{prefix_id}:E{index:03d}"
            for index, node in enumerate(source_nodes, 1)
        }
        events: list[dict[str, Any]] = []
        for index, node in enumerate(source_nodes, 1):
            source_id = str(node["node_id"])
            events.append(
                {
                    "event_id": id_map[source_id],
                    "step_id": index,
                    "kind": str(node["node_type"]),
                    "content": node.get("content"),
                    "causal_role": _legacy_event_role(source_id, node),
                    "token_count": int(node.get("token_count") or 0),
                    "side_effect": bool(node.get("side_effect")),
                    **(
                        {"source_message_ordinal": int(node["metadata"]["source_message_ordinal"])}
                        if node.get("metadata", {}).get("source_message_ordinal")
                        else {}
                    ),
                    **(
                        {"call_id": str(node["metadata"]["call_id"])}
                        if node.get("metadata", {}).get("call_id")
                        else {}
                    ),
                    **(
                        {"tool_name": str(node["metadata"]["tool_name"])}
                        if node.get("metadata", {}).get("tool_name")
                        else {}
                    ),
                }
            )
        recoverability = _legacy_recoverability(raw)
        prefix = PrefixRecord(
            prefix_id=prefix_id,
            source_kind="legacy_diagnostic",
            source_ref={
                "source_prefix_id": source_prefix_id,
                "source_prefix_hash": raw["prefix_hash"],
                "source_schema_version": raw["schema_version"],
            },
            split="dev",
            failure_family=str(raw["scenario_family"]),
            task_domain="legacy_phase6",
            recoverability=recoverability,
            context_length=("short" if raw["payload_target_tokens"] <= 256 else "long"),
            budget_tokens=1024,
            events=tuple(events),
            messages=tuple(dict(item) for item in raw["messages"]),
            tool_schemas=tuple(dict(item) for item in raw["tool_schemas"]),
            environment_snapshot={
                "snapshot_id": raw["prefix_hash"],
                "deterministic": True,
                "history_reconstructable": recoverability != "R0",
                "unsafe_to_repeat_failed_action": recoverability == "R3",
                "side_effects_sandboxed": True,
                "allowed_reacquisition_tools": (
                    []
                    if recoverability == "R0"
                    else ["read_audit_log"]
                    if recoverability == "R1"
                    else ["read_audit_log", "simulate_replay", "repeat_failed_action"]
                    if recoverability == "R3"
                    else ["inspect_environment", "replay_in_sandbox", "read_audit_log"]
                ),
            },
        )
        fork = forks_by_prefix[source_prefix_id]
        original_required_source = [str(item) for item in fork["required_subgraph_event_ids"]]
        source_order = {str(item["node_id"]): index for index, item in enumerate(source_nodes)}
        required_source = sorted(original_required_source, key=lambda item: source_order[item])
        required_nodes = [
            next((item for item in source_nodes if str(item["node_id"]) == source_id), None)
            for source_id in required_source
        ]
        call_nodes = [
            item for item in required_nodes if item and item.get("node_type") == "tool_call"
        ]
        observation_nodes = [
            item
            for item in required_nodes
            if item and item.get("node_type") in {"error", "observation"}
        ]
        failed_action, failed_arguments = _legacy_action(
            call_nodes[0] if call_nodes else None, "historical_action"
        )
        replacement_action, replacement_arguments = _legacy_action(
            call_nodes[-1] if len(call_nodes) > 1 else None, "historical_resolution"
        )
        failure_node = next(
            (item for item in observation_nodes if item.get("node_type") == "error"),
            observation_nodes[0] if observation_nodes else None,
        )
        failure_content = failure_node.get("content") if failure_node else {}
        if isinstance(failure_content, dict):
            error_signature = str(
                failure_content.get("error") or fork["expected_answer_facts"][0]
            )
            diagnostic = str(
                failure_content.get("detail") or fork["expected_answer_facts"][0]
            )
        else:
            error_signature = str(fork["expected_answer_facts"][0])
            diagnostic = str(failure_content or fork["expected_answer_facts"][0])
        decision_node = next(
            (item for item in required_nodes if item and item.get("node_type") == "decision"),
            None,
        )
        switch = str(
            decision_node.get("content") if decision_node else fork["expected_answer_facts"][0]
        )
        resolution_node = observation_nodes[-1] if observation_nodes else None
        resolution = str(
            resolution_node.get("content") if resolution_node else fork["expected_answer_facts"][0]
        )
        ordered = tuple(id_map[item] for item in required_source if item in id_map)
        chain_applicable = str(raw["scenario_family"]) in {
            "F1_shell_switch",
            "F2_failed_approach",
        }
        current_ids = tuple(
            event["event_id"] for event in events if event["causal_role"] == "current_fact"
        )

        def evidence_ids(node: Mapping[str, Any] | None) -> tuple[str, ...]:
            return (id_map[str(node["node_id"])],) if node else ()

        first_call = call_nodes[0] if call_nodes else None
        replacement_call_node = call_nodes[-1] if len(call_nodes) > 1 else None
        current_fact = next(
            (
                canonical_json(event["content"])
                for event in reversed(events)
                if event["event_id"] in current_ids
            ),
            "",
        )
        gold = FailureChainGold(
            prefix_id=prefix_id,
            failed_action=failed_action,
            failed_arguments=failed_arguments,
            error_signature=error_signature,
            diagnostic_evidence=diagnostic,
            switch_decision=switch,
            replacement_action=replacement_action,
            replacement_arguments=replacement_arguments,
            resolution_evidence=resolution,
            ordered_event_ids=ordered,
            evidence_by_field={
                "failed_action": evidence_ids(first_call),
                "failed_arguments": evidence_ids(first_call),
                "failure_cause": evidence_ids(failure_node),
                "diagnostic_evidence": evidence_ids(failure_node),
                "switch_decision": evidence_ids(decision_node),
                "replacement_action": evidence_ids(replacement_call_node),
                "replacement_arguments": evidence_ids(replacement_call_node),
                "resolution_evidence": evidence_ids(resolution_node),
                "ordered_event_ids": ordered,
                "current_fact": current_ids,
            },
            recoverability=recoverability,
            current_fact=current_fact,
            current_event_ids=current_ids,
            source_event_ids={id_map[key]: key for key in id_map},
            chain_applicable=chain_applicable,
            annotation={
                "gold_source": "legacy_reactivate_fork",
                "expected_answer_facts": list(fork["expected_answer_facts"]),
                "human_annotated": False,
                "original_required_source_event_ids": original_required_source,
                "non_failure_legacy_diagnostic": not chain_applicable,
            },
        )
        prefixes.append(prefix)
        gold_rows.append(gold)
        queries.extend(build_queries(prefix))
    return prefixes, gold_rows, queries


# Imported after definitions so mutually-referential helpers initialize safely.
from .controlled import (
    build_queries as build_queries,
)

from .io import (
    canonical_json as canonical_json,
    load_jsonl as load_jsonl,
)

from .models import (
    FailureChainGold as FailureChainGold,
    PrefixRecord as PrefixRecord,
    QueryRecord as QueryRecord,
)
