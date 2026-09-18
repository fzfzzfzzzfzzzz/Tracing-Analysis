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

from .intervention_constants import (
    P1_INTERVENTION_KINDS as P1_INTERVENTION_KINDS,
)



@dataclass(frozen=True, slots=True)
class InterventionSpec:
    intervention_id: str
    intervention_kind: str
    task_index: int
    seed: int
    entity_id: str


@dataclass(frozen=True, slots=True)
class InterventionConfig:
    tasks_per_kind: int = 8
    base_seed: int = 4100
    budget: int = 512

    def __post_init__(self) -> None:
        if not 5 <= self.tasks_per_kind <= 10:
            raise ValueError("P1 requires 5-10 fixed tasks per intervention kind")
        if self.budget <= 0:
            raise ValueError("budget must be positive")


def build_intervention_specs(config: InterventionConfig) -> list[InterventionSpec]:
    """Return the frozen deterministic P1 task/seed matrix."""

    specs: list[InterventionSpec] = []
    ordinal = 0
    for kind in P1_INTERVENTION_KINDS:
        for task_index in range(config.tasks_per_kind):
            seed = config.base_seed + ordinal
            specs.append(
                InterventionSpec(
                    intervention_id=f"p1_{kind}_{task_index:02d}",
                    intervention_kind=kind,
                    task_index=task_index,
                    seed=seed,
                    entity_id=f"E-{task_index:03d}",
                )
            )
            ordinal += 1
    return specs


def _tool_message(call: Node, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.node_id,
                "function": {
                    "name": call.metadata.get("tool_name"),
                    "arguments": arguments,
                },
            }
        ],
    }


def _result_message(call: Node, result: Node) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.node_id,
        "content": result.content,
    }


def _attach_message_ordinals(
    call: Node,
    result: Node,
    arguments: dict[str, Any],
    messages: list[dict[str, Any]],
) -> None:
    call.metadata["source_message_ordinal"] = len(messages) + 1
    messages.append(_tool_message(call, arguments))
    result.metadata["source_message_ordinal"] = len(messages) + 1
    messages.append(_result_message(call, result))


def _diagnostic_payload(error: str, spec: InterventionSpec) -> dict[str, Any]:
    # The long raw diagnostic makes the P1 token intervention identifiable:
    # card conditions keep the actionable cause while raw conditions replay it.
    return {
        "error": error,
        "diagnostic_blob": f"{spec.intervention_id}:" + "x" * (720 + spec.task_index * 8),
    }


def _record_result(
    executor: ToolExecutor,
    messages: list[dict[str, Any]],
    *,
    tool_name: str,
    arguments: dict[str, Any],
    step_id: int,
    status: ToolStatus,
    payload: Any,
) -> tuple[Node, Node]:
    call, result = executor.record_result(
        tool_name=tool_name,
        arguments=arguments,
        step_id=step_id,
        status=status,
        payload=payload,
    )
    _attach_message_ordinals(call, result, arguments, messages)
    return call, result


def _initial_trace(
    spec: InterventionSpec,
    archive: ArchiveStore,
) -> tuple[
    TraceGraph,
    ToolExecutor,
    list[dict[str, Any]],
    Node,
    str,
    dict[str, Any],
    str,
]:
    graph = TraceGraph(
        session_id=spec.intervention_id,
        metadata={
            "source": "phase3_p1_controlled_intervention",
            "synthetic": True,
            "controlled_ground_truth": True,
            "intervention_kind": spec.intervention_kind,
            "seed": spec.seed,
        },
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Complete {spec.intervention_kind} for {spec.entity_id}.",
        }
    ]
    graph.create_node(
        NodeType.GOAL,
        messages[0]["content"],
        0,
        token_count=estimate_tokens(messages[0]["content"]),
        metadata={"source_message_ordinal": 1},
    )
    executor = ToolExecutor(graph, archive)
    tool_name = "update_record"
    corrected_arguments: dict[str, Any]
    expected_expiry: str

    if spec.intervention_kind == "argument_correction":
        invalid = {"record_id": spec.entity_id, "page": 0}
        corrected_arguments = {"record_id": spec.entity_id, "page": 1}
        _, latest = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=invalid,
            step_id=1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload("page 0 is invalid; use page 1", spec),
        )
        latest.metadata["next_admissible_correction"] = json.dumps(
            corrected_arguments, sort_keys=True
        )
        expected_expiry = "resolved"
    elif spec.intervention_kind == "latest_failure_only":
        first_arguments = {"record_id": spec.entity_id, "page": 0}
        second_arguments = {"record_id": spec.entity_id, "page": 1}
        corrected_arguments = {"record_id": spec.entity_id, "page": 2}
        _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=first_arguments,
            step_id=1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload("page 0 is invalid; try page 1", spec),
        )
        _, latest = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=second_arguments,
            step_id=2,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload("page 1 is stale; use page 2", spec),
        )
        latest.metadata["next_admissible_correction"] = json.dumps(
            corrected_arguments, sort_keys=True
        )
        expected_expiry = "resolved"
    elif spec.intervention_kind == "alternative_tool_completion":
        tool_name = "primary_fetch"
        invalid = {"record_id": spec.entity_id}
        corrected_arguments = {"record_id": spec.entity_id}
        _, latest = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=invalid,
            step_id=1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload(
                "primary service unavailable; use fallback_fetch", spec
            ),
        )
        latest.metadata["next_admissible_correction"] = "use fallback_fetch"
        expected_expiry = "alternative_completed"
    elif spec.intervention_kind == "malformed_then_valid":
        invalid = {"record": spec.entity_id}
        corrected_arguments = {"record_id": spec.entity_id, "format": "json"}
        call, latest = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=invalid,
            step_id=1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload(
                "invalid argument: missing record_id and format", spec
            ),
        )
        call.metadata["arguments_valid"] = False
        latest.metadata["failure_class"] = FailureClass.MALFORMED.value
        latest.metadata["malformed_call"] = True
        latest.metadata["next_admissible_correction"] = json.dumps(
            corrected_arguments, sort_keys=True
        )
        expected_expiry = "corrected_syntax"
    else:
        raise ValueError(f"unknown intervention kind: {spec.intervention_kind}")

    messages.append(
        {
            "role": "user",
            "content": "Continue safely using the available failure evidence.",
        }
    )
    LifecycleEngine().apply(graph)
    return (
        graph,
        executor,
        messages,
        latest,
        tool_name,
        corrected_arguments,
        expected_expiry,
    )
