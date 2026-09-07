"""Definitions moved from ``tracegraph.lifecycle_annotation``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..capture import estimate_tokens
from ..graph import TraceGraph
from ..liveness import EventSpan
from ..phase5_offline import policy_text
from ..schema import EdgeType, Node, NodeType
from ..trajectory_artifacts import sha256_json

from .annotation_constants import (
    CURRENT_TARGET_NEEDS as CURRENT_TARGET_NEEDS,
    DISPOSITIONS as DISPOSITIONS,
    OBLIGATIONS as OBLIGATIONS,
    TERMINAL_REASONS as TERMINAL_REASONS,
    _CALL_TYPES as _CALL_TYPES,
)



def _node_order(node: Node) -> tuple[int, int, str]:
    ordinal = node.metadata.get("source_message_ordinal")
    return (node.step_id, int(ordinal) if isinstance(ordinal, int) else 0, node.node_id)


def _tool_name(node: Node) -> str:
    content = node.content if isinstance(node.content, Mapping) else {}
    return str(node.metadata.get("tool_name") or content.get("tool_name") or "")


def _arguments(node: Node) -> Any:
    content = node.content if isinstance(node.content, Mapping) else {}
    return content.get("arguments", {})


def _event_view(node: Node, opaque_id: str) -> dict[str, Any]:
    view: dict[str, Any] = {
        "event_id": opaque_id,
        "event_type": node.node_type.value,
        "sequence_step": node.step_id,
    }
    if node.node_type in _CALL_TYPES:
        view.update({"tool_name": _tool_name(node), "arguments": _arguments(node)})
    else:
        view["content"] = node.content
    return view


def _digest_rank(seed: str, item: str) -> str:
    return hashlib.sha256(f"{seed}\0{item}".encode()).hexdigest()


def _opaque_map(ids: Sequence[str], *, seed: str, prefix: str) -> dict[str, str]:
    ranked = sorted(set(ids), key=lambda item: (_digest_rank(seed, item), item))
    width = max(3, len(str(len(ranked))))
    return {item: f"{prefix}{index:0{width}d}" for index, item in enumerate(ranked, 1)}


def complete_tool_spans(prefix: TraceGraph) -> tuple[EventSpan, ...]:
    """Return one deterministic judging unit per complete tool call/result."""

    cutoff_step = int(prefix.metadata["cutoff_step"])
    visible = {
        node.node_id for node in prefix.nodes.values() if node.step_id <= cutoff_step
    }
    spans: list[EventSpan] = []
    for call in prefix.find_nodes(node_types=_CALL_TYPES):
        if call.node_id not in visible:
            continue
        results = sorted(
            (
                prefix.nodes[edge.target]
                for edge in prefix.outgoing(call.node_id)
                if edge.edge_type in {EdgeType.PRODUCES, EdgeType.FAILED_WITH}
                and edge.target in visible
            ),
            key=_node_order,
        )
        if not results:
            continue
        members = (call, *results)
        ordinals = [
            int(node.metadata["source_message_ordinal"])
            for node in members
            if isinstance(node.metadata.get("source_message_ordinal"), int)
        ]
        call_id = call.metadata.get("call_id")
        spans.append(
            EventSpan.create(
                span_type="complete_tool_call",
                node_ids=[node.node_id for node in members],
                message_ordinals=ordinals,
                call_ids=[str(call_id)] if call_id else (),
                raw_refs=[str(node.raw_ref) for node in members if node.raw_ref],
            )
        )
    return tuple(
        sorted(
            spans,
            key=lambda item: (
                min(item.message_ordinals) if item.message_ordinals else math.inf,
                item.span_id,
            ),
        )
    )


def _current_target(prefix: TraceGraph, event_map: Mapping[str, str]) -> dict[str, Any]:
    candidates = [
        node
        for node in prefix.nodes.values()
        if node.node_type in {NodeType.GOAL, NodeType.SUBGOAL}
        or node.metadata.get("source") == "user_message"
    ]
    if not candidates:
        return {"event_id": None, "content": ""}
    target = max(candidates, key=_node_order)
    return {"event_id": event_map[target.node_id], "content": target.content}


def response_function_schema(expected_span_ids: Sequence[str]) -> dict[str, Any]:
    label = {
        "type": "object",
        "properties": {
            "span_id": {"type": "string", "enum": list(expected_span_ids)},
            "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
            "terminal_reason": {"type": "string", "enum": list(TERMINAL_REASONS)},
            "relation_target_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(expected_span_ids)},
                "uniqueItems": True,
            },
            "obligations": {
                "type": "array",
                "items": {"type": "string", "enum": list(OBLIGATIONS)},
                "uniqueItems": True,
            },
            "evidence_event_ids": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            },
            "reactivation_risk": {"type": "boolean"},
        },
        "required": [
            "span_id",
            "disposition",
            "terminal_reason",
            "relation_target_ids",
            "obligations",
            "evidence_event_ids",
            "reactivation_risk",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "function",
        "function": {
            "name": "submit_lifecycle_labels",
            "description": "Submit one lifecycle judgment for every supplied tool span.",
            "parameters": {
                "type": "object",
                "properties": {"labels": {"type": "array", "items": label}},
                "required": ["labels"],
                "additionalProperties": False,
            },
        },
    }


def response_relation_function_schema(
    expected_span_ids: Sequence[str],
) -> dict[str, Any]:
    label = {
        "type": "object",
        "properties": {
            "span_id": {
                "type": "string",
                "enum": list(expected_span_ids),
            },
            "terminal_reason": {
                "type": "string",
                "enum": list(TERMINAL_REASONS),
                "description": (
                    "Lifecycle reason, not disposition. Superseded belongs here."
                ),
            },
            "current_target_need": {
                "type": "string",
                "enum": list(CURRENT_TARGET_NEEDS),
                "description": (
                    "Need for this exact span: required, useful, not_needed, or uncertain."
                ),
            },
            "relation_target_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(expected_span_ids)},
                "uniqueItems": True,
            },
            "obligations": {
                "type": "array",
                "items": {"type": "string", "enum": list(OBLIGATIONS)},
                "uniqueItems": True,
            },
            "evidence_event_ids": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            },
            "reactivation_risk": {
                "type": "boolean",
            },
        },
        "required": [
            "span_id",
            "terminal_reason",
            "current_target_need",
            "relation_target_ids",
            "obligations",
            "evidence_event_ids",
            "reactivation_risk",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "function",
        "function": {
            "name": "submit_lifecycle_relations",
            "description": (
                "Submit lifecycle relations for every span; never submit disposition."
            ),
            "parameters": {
                "type": "object",
                "properties": {"labels": {"type": "array", "items": label}},
                "required": ["labels"],
                "additionalProperties": False,
            },
        },
    }


def response_relation_boolean_function_schema(
    expected_span_ids: Sequence[str],
) -> dict[str, Any]:
    label = {
        "type": "object",
        "properties": {
            "span_id": {"type": "string", "enum": list(expected_span_ids)},
            "terminal_reason": {
                "type": "string",
                "enum": list(TERMINAL_REASONS),
                "description": "Lifecycle reason. Superseded belongs only here.",
            },
            "required_for_current_target": {"type": "boolean"},
            "requirement_uncertain": {"type": "boolean"},
            "relation_target_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(expected_span_ids)},
                "uniqueItems": True,
            },
            "obligations": {
                "type": "array",
                "items": {"type": "string", "enum": list(OBLIGATIONS)},
                "uniqueItems": True,
            },
            "evidence_event_ids": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            },
            "reactivation_risk": {"type": "boolean"},
        },
        "required": [
            "span_id",
            "terminal_reason",
            "required_for_current_target",
            "requirement_uncertain",
            "relation_target_ids",
            "obligations",
            "evidence_event_ids",
            "reactivation_risk",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "function",
        "function": {
            "name": "submit_lifecycle_relations",
            "description": "Submit relations and boolean retention evidence for every span.",
            "parameters": {
                "type": "object",
                "properties": {"labels": {"type": "array", "items": label}},
                "required": ["labels"],
                "additionalProperties": False,
            },
        },
    }


def annotation_response_function_schema(
    expected_span_ids: Sequence[str], config: Mapping[str, Any]
) -> dict[str, Any]:
    protocol = config.get("annotation", {}).get(
        "label_protocol", "direct_disposition_v1"
    )
    if protocol == "direct_disposition_v1":
        return response_function_schema(expected_span_ids)
    if protocol == "relation_first_v1":
        return response_relation_function_schema(expected_span_ids)
    if protocol == "relation_first_boolean_v2":
        return response_relation_boolean_function_schema(expected_span_ids)
    raise ValueError(f"unsupported Phase 5.2 label protocol: {protocol}")


@dataclass(frozen=True, slots=True)
class PreparedAnnotationRequest:
    request_id: str
    prefix_id: str
    pass_id: str
    split: str
    span_count: int
    request: dict[str, Any]
    mapping: dict[str, Any]
    estimated_input_tokens: int
    request_sha256: str

    def to_index_row(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "prefix_id": self.prefix_id,
            "pass_id": self.pass_id,
            "split": self.split,
            "span_count": self.span_count,
            "estimated_input_tokens": self.estimated_input_tokens,
            "request_sha256": self.request_sha256,
        }


@dataclass(frozen=True, slots=True)
class AnnotationBudget:
    request_count: int
    prompt_tokens: int
    output_tokens: int
    request_count_max: int
    prompt_tokens_max: int
    output_tokens_max: int

    @classmethod
    def from_ledger(
        cls, ledger: Sequence[Mapping[str, Any]], *, limits: Mapping[str, Any]
    ) -> "AnnotationBudget":
        return cls(
            request_count=len(ledger),
            prompt_tokens=sum(int(item["usage"]["prompt_tokens"]) for item in ledger),
            output_tokens=sum(int(item["usage"]["completion_tokens"]) for item in ledger),
            request_count_max=int(limits["request_count_hard_max"]),
            prompt_tokens_max=int(limits["estimated_input_tokens_hard_max"]),
            output_tokens_max=int(limits["actual_output_tokens_hard_max"]),
        )

    def assert_can_submit(self) -> None:
        if self.request_count >= self.request_count_max:
            raise RuntimeError("global request-count hard cap reached")
        if self.prompt_tokens >= self.prompt_tokens_max:
            raise RuntimeError("actual prompt-token hard cap reached")
        if self.output_tokens >= self.output_tokens_max:
            raise RuntimeError("actual output-token hard cap reached")


def remaining_attempt_numbers(
    existing_attempts: int, *, retry_per_request_max: int = 1
) -> tuple[int, ...]:
    if existing_attempts < 0 or retry_per_request_max < 0:
        raise ValueError("attempt counts cannot be negative")
    return tuple(range(existing_attempts + 1, retry_per_request_max + 2))


def extract_function_arguments(
    response: Mapping[str, Any], *, function_name: str = "submit_lifecycle_labels"
) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("provider response must contain exactly one choice")
    message = choices[0].get("message", {})
    calls = message.get("tool_calls")
    matching = [
        item
        for item in calls or ()
        if item.get("function", {}).get("name") == function_name
    ]
    if len(matching) != 1:
        raise ValueError("required lifecycle label function was not called exactly once")
    arguments = matching[0]["function"].get("arguments")
    if isinstance(arguments, str):
        value = json.loads(arguments)
    elif isinstance(arguments, dict):
        value = arguments
    else:
        raise ValueError("function arguments are not a JSON object")
    if not isinstance(value, dict):
        raise ValueError("function arguments must decode to a JSON object")
    return value


def _split_for_task(task_id: str, config: Mapping[str, Any]) -> str:
    split = config["split"]
    if task_id in set(map(str, split["development_task_ids"])):
        return "development"
    if task_id in set(map(str, split["calibration_task_ids"])):
        return "calibration"
    if task_id in set(map(str, split["held_out_task_ids"])):
        return "held_out"
    raise ValueError(f"task is outside the frozen Phase 5.2 split: {task_id}")
