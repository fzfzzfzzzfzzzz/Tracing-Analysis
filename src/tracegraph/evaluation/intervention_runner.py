"""Definitions moved from ``tracegraph.interventions``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from ..archive import ArchiveStore
from ..capture import ToolExecutor, estimate_tokens
from ..context import ContextView, build_context_managers
from ..failure_cards import build_failure_cards
from ..graph import TraceGraph
from ..lifecycle import LifecycleEngine
from ..message_protocol import project_context_items_to_messages
from ..schema import FailureClass, Node, NodeType, ToolStatus, utc_now





def _failure_visible(view: ContextView) -> bool:
    return any(
        item.node_type == NodeType.ERROR
        or (
            item.node_type == NodeType.SUMMARY
            and item.reason.startswith("failure_card")
        )
        for item in view.items
    )


def _input_accounting(
    graph: TraceGraph,
    messages: list[dict[str, Any]],
    view: ContextView,
) -> dict[str, int]:
    ordinals, fragments = project_context_items_to_messages(
        messages,
        view.items,
        graph.nodes,
    )
    selected_messages = [
        message
        for ordinal, message in enumerate(messages, start=1)
        if ordinal in ordinals
    ]
    return {
        "selected_representation_tokens": view.selected_tokens,
        "protocol_closed_message_tokens": sum(
            estimate_tokens(message) for message in selected_messages
        ),
        # This is exact for the deterministic local controller's serialized
        # input, not a claim about an external LLM tokenizer.
        "actual_provider_input_tokens": estimate_tokens(
            {
                "system": "deterministic_p1_controller_v1",
                "messages": selected_messages,
                "active_trace_context": fragments,
            }
        ),
    }


def _sum_accounting(total: dict[str, int], current: dict[str, int]) -> None:
    for key, value in current.items():
        total[key] += value


def _run_one(
    spec: InterventionSpec,
    manager_name: str,
    *,
    budget: int,
    archive: ArchiveStore,
) -> tuple[dict[str, Any], TraceGraph]:
    (
        graph,
        executor,
        messages,
        initial_latest,
        tool_name,
        corrected_arguments,
        expected_expiry,
    ) = _initial_trace(spec, archive)
    graph.metadata["evaluated_context_manager"] = manager_name
    manager = build_context_managers()[manager_name]
    manager_budget = None if manager_name == "full_trajectory" else budget

    totals = {
        "selected_representation_tokens": 0,
        "protocol_closed_message_tokens": 0,
        "actual_provider_input_tokens": 0,
    }
    first_view = manager.select(graph, budget=manager_budget)
    _sum_accounting(totals, _input_accounting(graph, messages, first_view))
    failure_visible = _failure_visible(first_view)
    repeated_invalid_action = 0
    recovery_steps = 1
    fallback_intervention_used = 0

    selected_card_items = [
        item
        for item in first_view.items
        if item.node_type == NodeType.SUMMARY
        and item.reason.startswith("failure_card")
    ]
    card_precision = None
    if manager_name == "full_ours":
        card_precision = float(
            len(selected_card_items) == 1
            and initial_latest.node_id in selected_card_items[0].source_node_ids
        )

    if not failure_visible:
        repeated_invalid_action = 1
        recovery_steps = 2
        fallback_intervention_used = 1
        failed_arguments = (
            {"record": spec.entity_id}
            if spec.intervention_kind == "malformed_then_valid"
            else (
                {"record_id": spec.entity_id}
                if spec.intervention_kind == "alternative_tool_completion"
                else {"record_id": spec.entity_id, "page": 0}
            )
        )
        _, repeated = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=failed_arguments,
            step_id=initial_latest.step_id + 1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload("repeated invalid action", spec),
        )
        if spec.intervention_kind == "malformed_then_valid":
            repeated.metadata["failure_class"] = FailureClass.MALFORMED.value
            repeated.metadata["malformed_call"] = True
        messages.append(
            {
                "role": "user",
                "content": "Safety monitor: apply the known admissible correction now.",
            }
        )
        LifecycleEngine().apply(graph)
        second_view = manager.select(graph, budget=manager_budget)
        _sum_accounting(totals, _input_accounting(graph, messages, second_view))

    recovery_tool = (
        "fallback_fetch"
        if spec.intervention_kind == "alternative_tool_completion"
        else tool_name
    )
    recovery_step = max(node.step_id for node in graph.nodes.values()) + 1
    recovery_call, _ = _record_result(
        executor,
        messages,
        tool_name=recovery_tool,
        arguments=corrected_arguments,
        step_id=recovery_step,
        status=ToolStatus.SUCCESS,
        payload={"status": "completed", "entity_id": spec.entity_id},
    )
    if spec.intervention_kind == "malformed_then_valid":
        recovery_call.metadata["arguments_valid"] = True
    if spec.intervention_kind == "alternative_tool_completion":
        scope = initial_latest.lifecycle_profile.scope.get("operation_key")
        if scope:
            graph.metadata["completed_operation_scopes"] = [scope]
    LifecycleEngine().apply(graph)
    cards_after, expiry_events = build_failure_cards(graph, ttl_steps=8)
    observed_expiry = {
        event.get("expiry_trigger")
        for event in expiry_events
        if event.get("expiry_trigger") is not None
    }
    expiry_correctness = None
    if manager_name == "full_ours":
        expiry_correctness = float(
            not cards_after and expected_expiry in observed_expiry
        )

    graph.metadata.update(
        {
            "task_success": 1.0,
            "policy_violation": 0.0,
            "normal_stop": True,
            "repeated_invalid_action": repeated_invalid_action,
            "recovery_steps": recovery_steps,
            "fallback_intervention_used": fallback_intervention_used,
        }
    )
    row = {
        **asdict(spec),
        "manager": manager_name,
        "budget": manager_budget,
        "failure_visible": int(failure_visible),
        "repeated_invalid_action": repeated_invalid_action,
        "recovery_steps": recovery_steps,
        "normal_stop": 1,
        "policy_violation": 0,
        "task_success": 1,
        "fallback_intervention_used": fallback_intervention_used,
        **totals,
        "provider_kind": "deterministic_local_controller",
        "provider_usage_scope": "exact_serialized_controller_input",
        "card_precision_controlled_gold": card_precision,
        "expiry_correctness_controlled_gold": expiry_correctness,
        "expected_expiry_trigger": expected_expiry,
        "observed_expiry_triggers": sorted(observed_expiry),
        "active_card_count_after_completion": len(cards_after),
        "graph_validation_errors": graph.validate(),
    }
    return row, graph


# Imported after definitions so mutually-referential helpers initialize safely.
from .intervention_scenarios import (
    InterventionSpec as InterventionSpec,
    _diagnostic_payload as _diagnostic_payload,
    _initial_trace as _initial_trace,
    _record_result as _record_result,
)
