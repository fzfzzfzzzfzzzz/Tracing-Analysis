"""Blind, prefix-only machine annotation protocol for Phase 5.2.

This module deliberately stops at development evidence.  A model label is never
converted into an EventGraph edge or a provider-sendable pruning decision.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capture import estimate_tokens
from .graph import TraceGraph
from .liveness import EventSpan
from .phase5_offline import policy_text
from .schema import EdgeType, Node, NodeType
from .trajectory_artifacts import sha256_json


DISPOSITIONS = (
    "live_critical",
    "live_noncritical",
    "safe_to_evict",
    "uncertain",
)
TERMINAL_REASONS = (
    "active",
    "consumed",
    "superseded",
    "invalidated",
    "resolved",
    "duplicate",
    "audit_required",
    "unknown",
)
OBLIGATIONS = ("policy", "confirmation", "retry", "receipt", "audit")
CURRENT_TARGET_NEEDS = ("required", "useful", "not_needed", "uncertain")
TERMINAL_SAFE_REASONS = (
    "consumed",
    "superseded",
    "invalidated",
    "resolved",
    "duplicate",
)
_FORBIDDEN_INPUT_KEYS = {
    "reward",
    "outcome",
    "task_success",
    "future_suffix",
    "future_events",
    "f5_label",
    "phase5_label",
    "phase51_label",
    "prune_result",
    "pruning_result",
    "token_gain",
    "token_savings",
    "treatment",
}
_CALL_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_phase52_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("schema_version") != "phase52_lifecycle_modeling_config_v1":
        raise ValueError("unsupported Phase 5.2 configuration")
    model = config.get("model", {})
    api_model = str(model.get("api_model", ""))
    if (
        model.get("temperature") != 0.0
        or model.get("thinking") != "disabled"
        or model.get("max_output_tokens") != 4096
        or not model.get("fallback_forbidden")
    ):
        raise ValueError("Phase 5.2 model protocol drift")
    if api_model == "glm-4.7-flash":
        if model.get("report_identity") != "zai/glm-4.7-flash" or not model.get(
            "paid_use_forbidden"
        ):
            raise ValueError("Phase 5.2 free model protocol drift")
    elif api_model == "glm-5.2":
        if (
            model.get("report_identity") != "zai/glm-5.2"
            or model.get("paid_use_forbidden") is not False
            or model.get("paid_use_authorized_by_user") is not True
            or model.get("condition_id") != "e1_glm52_pseudolabel_v1"
        ):
            raise ValueError("Phase 5.2 paid GLM-5.2 protocol drift")
    elif api_model == "qwen3.7-plus":
        condition_id = model.get("condition_id")
        label_protocol = config.get("annotation", {}).get(
            "label_protocol", "direct_disposition_v1"
        )
        if (
            model.get("provider") != "dashscope"
            or model.get("report_identity")
            != "aliyun-bailian/qwen3.7-plus"
            or model.get("enable_thinking") is not False
            or model.get("paid_use_forbidden") is not False
            or model.get("paid_use_authorized_by_user") is not True
        ):
            raise ValueError("Phase 5.2 paid Qwen3.7-Plus protocol drift")
        if condition_id == "e2_qwen37plus_pseudolabel_v1":
            if label_protocol != "direct_disposition_v1":
                raise ValueError("Phase 5.2 Qwen e2 label protocol drift")
        elif condition_id == "e3_qwen37plus_relation_first_v2":
            if (
                label_protocol != "relation_first_v1"
                or config.get("annotation", {}).get("repair_retry_mode")
                != "validation_feedback"
            ):
                raise ValueError("Phase 5.2 Qwen e3 label protocol drift")
        elif condition_id == "e3_qwen37plus_relation_first_v3":
            if (
                label_protocol != "relation_first_boolean_v2"
                or config.get("annotation", {}).get("repair_retry_mode")
                != "validation_feedback"
            ):
                raise ValueError("Phase 5.2 Qwen e3 boolean protocol drift")
        else:
            raise ValueError("Phase 5.2 paid Qwen condition drift")
    else:
        raise ValueError("Phase 5.2 model protocol drift")
    governance = config.get("governance", {})
    required_false = (
        "machine_labels_are_human_gold",
        "machine_labels_may_generate_hard_dead",
        "predictions_may_mutate_event_graph",
        "offline_projection_may_be_sent",
        "external_behavior_experiment_authorized",
        "scheme_b_authorized",
        "historical_93_is_lifecycle_gate",
    )
    if any(governance.get(key) is not False for key in required_false):
        raise ValueError("unsafe Phase 5.2 governance configuration")
    specs = config.get("tool_effect_specs", ())
    names = [str(item.get("tool_name")) for item in specs]
    if len(specs) != 15 or len(names) != len(set(names)):
        raise ValueError("Phase 5.2 requires exactly 15 unique ToolEffectSpecs")
    return config


def config_sha256(config: Mapping[str, Any]) -> str:
    return sha256_json(dict(config))


def assert_prefix_only_payload(
    value: Any,
    *,
    allowed_event_ids: set[str] | None = None,
    allowed_span_ids: set[str] | None = None,
) -> None:
    """Reject outcome leakage and dangling IDs before any network operation."""

    observed_events: set[str] = set()
    observed_spans: set[str] = set()

    def visit(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key).strip().lower()
                if key in _FORBIDDEN_INPUT_KEYS:
                    raise ValueError(f"forbidden annotation input key at {path}.{raw_key}")
                if key in {"event_id", "source_event_id", "target_event_id"}:
                    observed_events.add(str(child))
                if key == "span_id":
                    observed_spans.add(str(child))
                visit(child, f"{path}.{raw_key}")
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")

    visit(value, "request")
    if allowed_event_ids is not None and not observed_events.issubset(allowed_event_ids):
        unknown = sorted(observed_events.difference(allowed_event_ids))
        raise ValueError(f"unknown event id in annotation request: {unknown[:3]}")
    if allowed_span_ids is not None and observed_spans != allowed_span_ids:
        raise ValueError("annotation request span IDs do not match the frozen mapping")


def prepare_validation_feedback_request(
    request: Mapping[str, Any], *, validation_error: str
) -> dict[str, Any]:
    """Create a deterministic prefix-only correction request after validation failure."""

    error = validation_error.strip()
    if not error:
        raise ValueError("validation feedback cannot be empty")
    repaired = json.loads(canonical_json(request))
    messages = repaired.get("messages")
    if not isinstance(messages, list):
        raise ValueError("annotation request messages must be an array")
    messages.append(
        {
            "role": "user",
            "content": (
                "VALIDATION_REPAIR: The previous function arguments were rejected by "
                f"deterministic validation. Error: {error}. Return the complete JSON "
                "function arguments again for every supplied span. Correct only the "
                "schema violation, use exactly the allowed enum values, and do not add "
                "or omit labels."
            ),
        }
    )
    assert_prefix_only_payload(repaired)
    return repaired


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


_SYSTEM_PROMPT = """You label historical tool-exchange spans using only the supplied prefix.
Return exactly one label for every span through submit_lifecycle_labels.

safe_to_evict means the entire call/result span is no longer needed for the current target because prefix-visible evidence proves it was fully consumed, superseded, invalidated, resolved, or duplicated. Do not guess. Use uncertain when entity identity, field coverage, success, relation direction, or future reactivation is ambiguous.

live_critical covers policy, explicit confirmation, unresolved retry/error, side-effect receipt, audit, or evidence still required for the current target. live_noncritical is useful but not critical. A side-effect receipt or a span carrying policy/confirmation/audit obligations must never be safe_to_evict. relation_target_ids name the later span(s) that establish a terminal relation. evidence_event_ids must come only from the supplied event log. Keep both ID arrays sorted and unique."""


_RELATION_SYSTEM_PROMPT = """Identify lifecycle relations using only the supplied prefix. Return one JSON-compatible label per span through submit_lifecycle_relations.

Never output disposition; code derives it. terminal_reason is only one of active, consumed, superseded, invalidated, resolved, duplicate, audit_required, or unknown. Superseded belongs only in terminal_reason.

current_target_need is required, useful, not_needed, or uncertain. Use not_needed only when prefix evidence proves this exact span is no longer needed. Use uncertain for ambiguous identity, coverage, success, relation, or reuse.

Put policy, confirmation, unresolved retry, receipt, and audit only in obligations. relation_target_ids are later proving spans. evidence_event_ids must be supplied events. Sort and deduplicate both ID arrays."""


_RELATION_BOOLEAN_SYSTEM_PROMPT = """Identify lifecycle relations using only the supplied prefix. Return one JSON-compatible label per span through submit_lifecycle_relations.

Never output disposition. terminal_reason is active, consumed, superseded, invalidated, resolved, duplicate, audit_required, or unknown. Superseded belongs only in terminal_reason.

required_for_current_target and requirement_uncertain are booleans, never strings. Set required_for_current_target=true only when the current target still needs this exact historical span. Set requirement_uncertain=true when that need cannot be proven either way.

Put policy, confirmation, unresolved retry, receipt, and audit only in obligations. relation_target_ids are later proving spans. evidence_event_ids must be supplied events. Sort and deduplicate both ID arrays."""


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


def prepare_annotation_request(
    *,
    prefix: TraceGraph,
    prefix_row: Mapping[str, Any],
    tool_schemas: Sequence[Mapping[str, Any]],
    pass_id: str,
    config: Mapping[str, Any],
) -> PreparedAnnotationRequest:
    if pass_id not in {"pass_a", "pass_b"}:
        raise ValueError(f"unknown annotation pass: {pass_id}")
    prefix_id = str(prefix_row["prefix_id"])
    spans = list(complete_tool_spans(prefix))
    if not spans:
        raise ValueError("model request cannot be created for a zero-span prefix")
    span_seed = hashlib.sha256(f"{prefix_id}pass_b".encode()).hexdigest()
    if pass_id == "pass_b":
        spans.sort(key=lambda item: (_digest_rank(span_seed, item.span_id), item.span_id))
    # Policy is supplied once in the dedicated policy field.  Repeating the
    # often-long constraint node in the event log would consume ~0.68M tokens
    # across two passes without adding evidence.
    visible_nodes = [
        node
        for node in sorted(prefix.nodes.values(), key=_node_order)
        if node.node_type != NodeType.CONSTRAINT
    ]
    event_ids = [node.node_id for node in visible_nodes]
    event_map = _opaque_map(event_ids, seed=f"{prefix_id}\0{pass_id}\0events", prefix="E")
    span_map = {
        span.span_id: f"S{index:03d}" for index, span in enumerate(spans, start=1)
    }
    visible = set(event_map)
    events = [_event_view(node, event_map[node.node_id]) for node in visible_nodes]
    relations = [
        {
            "relation": edge.edge_type.value,
            "source_event_id": event_map[edge.source],
            "target_event_id": event_map[edge.target],
        }
        for edge in sorted(
            prefix.edges.values(),
            key=lambda item: (item.source, item.target, item.edge_type.value, item.edge_id),
        )
        if edge.source in visible and edge.target in visible
    ]
    span_views = [
        {
            "span_id": span_map[span.span_id],
            "event_ids": [event_map[event_id] for event_id in span.node_ids],
        }
        for span in spans
    ]
    annotation_input = {
        "schema_version": "phase52_blind_prefix_input_v1",
        "prefix_ref": hashlib.sha256(prefix_id.encode()).hexdigest()[:20],
        "current_target": _current_target(prefix, event_map),
        "policy": list(policy_text(prefix)),
        "tool_schemas": [dict(item) for item in tool_schemas],
        "event_log": events,
        "event_relations": relations,
        "tool_spans_to_label": span_views,
    }
    allowed_events = set(event_map.values())
    allowed_spans = set(span_map.values())
    assert_prefix_only_payload(
        annotation_input,
        allowed_event_ids=allowed_events,
        allowed_span_ids=allowed_spans,
    )
    function_schema = annotation_response_function_schema(
        tuple(view["span_id"] for view in span_views), config
    )
    function_name = str(function_schema["function"]["name"])
    protocol = config.get("annotation", {}).get(
        "label_protocol", "direct_disposition_v1"
    )
    request: dict[str, Any] = {
        "model": config["model"]["api_model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    _RELATION_SYSTEM_PROMPT
                    if protocol == "relation_first_v1"
                    else (
                        _RELATION_BOOLEAN_SYSTEM_PROMPT
                        if protocol == "relation_first_boolean_v2"
                        else _SYSTEM_PROMPT
                    )
                ),
            },
            {"role": "user", "content": canonical_json(annotation_input)},
        ],
        "tools": [function_schema],
        "tool_choice": {
            "type": "function",
            "function": {"name": function_name},
        },
        "temperature": config["model"]["temperature"],
        "max_tokens": config["model"]["max_output_tokens"],
        "stream": False,
    }
    if config["model"].get("provider") == "dashscope":
        request["enable_thinking"] = config["model"]["enable_thinking"]
    else:
        request["thinking"] = {"type": config["model"]["thinking"]}
    request_hash = sha256_json(request)
    mapping = {
        "schema_version": "phase52_opaque_mapping_v1",
        "prefix_id": prefix_id,
        "pass_id": pass_id,
        "request_sha256": request_hash,
        "events": [
            {"event_id": original, "opaque_event_id": opaque}
            for original, opaque in sorted(event_map.items())
        ],
        "spans": [
            {"span_id": original, "opaque_span_id": opaque}
            for original, opaque in sorted(span_map.items())
        ],
    }
    mapping["mapping_sha256"] = sha256_json(mapping)
    request_id = f"{pass_id}_{hashlib.sha256(prefix_id.encode()).hexdigest()[:20]}"
    return PreparedAnnotationRequest(
        request_id=request_id,
        prefix_id=prefix_id,
        pass_id=pass_id,
        split=_split_for_task(str(prefix_row["task_id"]), config),
        span_count=len(spans),
        request=request,
        mapping=mapping,
        estimated_input_tokens=estimate_tokens(request),
        request_sha256=request_hash,
    )


def validate_machine_labels(
    value: Any,
    *,
    expected_span_ids: set[str],
    allowed_event_ids: set[str],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping) or set(value) != {"labels"}:
        raise ValueError("machine response must contain only labels")
    raw_labels = value["labels"]
    if not isinstance(raw_labels, list):
        raise ValueError("machine response labels must be an array")
    required = {
        "span_id",
        "disposition",
        "terminal_reason",
        "relation_target_ids",
        "obligations",
        "evidence_event_ids",
        "reactivation_risk",
    }
    parsed: list[dict[str, Any]] = []
    for raw in raw_labels:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError("machine label has missing or additional fields")
        span_id = str(raw["span_id"])
        disposition = str(raw["disposition"])
        reason = str(raw["terminal_reason"])
        targets = tuple(sorted(set(map(str, raw["relation_target_ids"]))))
        obligations = tuple(sorted(set(map(str, raw["obligations"]))))
        evidence = tuple(sorted(set(map(str, raw["evidence_event_ids"]))))
        if span_id not in expected_span_ids:
            raise ValueError(f"unknown machine-label span ID: {span_id}")
        if disposition not in DISPOSITIONS:
            raise ValueError(
                f"invalid disposition enum for {span_id}: {disposition!r}; "
                f"allowed={list(DISPOSITIONS)!r}"
            )
        if reason not in TERMINAL_REASONS:
            raise ValueError(
                f"invalid terminal_reason enum for {span_id}: {reason!r}; "
                f"allowed={list(TERMINAL_REASONS)!r}"
            )
        if not set(targets).issubset(expected_span_ids):
            raise ValueError("machine label contains an unknown relation target")
        if not set(obligations).issubset(OBLIGATIONS):
            raise ValueError("machine label contains an unknown obligation")
        if not set(evidence).issubset(allowed_event_ids):
            raise ValueError("machine label contains an unknown evidence event")
        if not isinstance(raw["reactivation_risk"], bool):
            raise ValueError("reactivation_risk must be boolean")
        parsed.append(
            {
                "span_id": span_id,
                "disposition": disposition,
                "terminal_reason": reason,
                "relation_target_ids": list(targets),
                "obligations": list(obligations),
                "evidence_event_ids": list(evidence),
                "reactivation_risk": raw["reactivation_risk"],
            }
        )
    observed = [item["span_id"] for item in parsed]
    if len(observed) != len(set(observed)) or set(observed) != expected_span_ids:
        raise ValueError("machine response has missing or duplicate span labels")
    return tuple(sorted(parsed, key=lambda item: item["span_id"]))


def derive_relation_first_disposition(
    *,
    terminal_reason: str,
    current_target_need: str,
    obligations: Sequence[str],
    reactivation_risk: bool,
) -> str:
    """Fail-closed deterministic mapping from lifecycle evidence to disposition."""

    protected = set(obligations) & set(OBLIGATIONS)
    if protected or terminal_reason == "audit_required":
        return "live_critical"
    if reactivation_risk or terminal_reason == "unknown":
        return "uncertain"
    if terminal_reason in TERMINAL_SAFE_REASONS:
        if current_target_need == "not_needed":
            return "safe_to_evict"
        if current_target_need == "required":
            return "live_critical"
        if current_target_need == "useful":
            return "live_noncritical"
        return "uncertain"
    if terminal_reason == "active":
        if current_target_need == "required":
            return "live_critical"
        if current_target_need == "useful":
            return "live_noncritical"
    return "uncertain"


def validate_relation_first_labels(
    value: Any,
    *,
    expected_span_ids: set[str],
    allowed_event_ids: set[str],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping) or set(value) != {"labels"}:
        raise ValueError("machine response must contain only labels")
    raw_labels = value["labels"]
    if not isinstance(raw_labels, list):
        raise ValueError("machine response labels must be an array")
    required = {
        "span_id",
        "terminal_reason",
        "current_target_need",
        "relation_target_ids",
        "obligations",
        "evidence_event_ids",
        "reactivation_risk",
    }
    parsed: list[dict[str, Any]] = []
    for raw in raw_labels:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError("machine relation label has missing or additional fields")
        span_id = str(raw["span_id"])
        reason = str(raw["terminal_reason"])
        need = str(raw["current_target_need"])
        targets = tuple(sorted(set(map(str, raw["relation_target_ids"]))))
        obligations = tuple(sorted(set(map(str, raw["obligations"]))))
        evidence = tuple(sorted(set(map(str, raw["evidence_event_ids"]))))
        if span_id not in expected_span_ids:
            raise ValueError(f"unknown machine-label span ID: {span_id}")
        if reason not in TERMINAL_REASONS:
            raise ValueError(
                f"invalid terminal_reason enum for {span_id}: {reason!r}; "
                f"allowed={list(TERMINAL_REASONS)!r}"
            )
        if need not in CURRENT_TARGET_NEEDS:
            raise ValueError(
                f"invalid current_target_need enum for {span_id}: {need!r}; "
                f"allowed={list(CURRENT_TARGET_NEEDS)!r}"
            )
        if not set(targets).issubset(expected_span_ids):
            raise ValueError("machine relation label contains an unknown relation target")
        if not set(obligations).issubset(OBLIGATIONS):
            raise ValueError("machine relation label contains an unknown obligation")
        if not set(evidence).issubset(allowed_event_ids):
            raise ValueError("machine relation label contains an unknown evidence event")
        if not isinstance(raw["reactivation_risk"], bool):
            raise ValueError("reactivation_risk must be boolean")
        parsed.append(
            {
                "span_id": span_id,
                "disposition": derive_relation_first_disposition(
                    terminal_reason=reason,
                    current_target_need=need,
                    obligations=obligations,
                    reactivation_risk=raw["reactivation_risk"],
                ),
                "terminal_reason": reason,
                "current_target_need": need,
                "relation_target_ids": list(targets),
                "obligations": list(obligations),
                "evidence_event_ids": list(evidence),
                "reactivation_risk": raw["reactivation_risk"],
                "disposition_provenance": "deterministic_relation_first_v1",
            }
        )
    observed = [item["span_id"] for item in parsed]
    if len(observed) != len(set(observed)) or set(observed) != expected_span_ids:
        raise ValueError("machine response has missing or duplicate span labels")
    return tuple(sorted(parsed, key=lambda item: item["span_id"]))


def derive_relation_boolean_disposition(
    *,
    terminal_reason: str,
    required_for_current_target: bool,
    requirement_uncertain: bool,
    obligations: Sequence[str],
    reactivation_risk: bool,
) -> str:
    """Fail closed while deriving liveness from relations and boolean evidence."""

    if set(obligations) & set(OBLIGATIONS) or terminal_reason == "audit_required":
        return "live_critical"
    if required_for_current_target:
        return "live_critical"
    if requirement_uncertain or reactivation_risk or terminal_reason == "unknown":
        return "uncertain"
    if terminal_reason in TERMINAL_SAFE_REASONS:
        return "safe_to_evict"
    if terminal_reason == "active":
        return "live_noncritical"
    return "uncertain"


def validate_relation_boolean_labels(
    value: Any,
    *,
    expected_span_ids: set[str],
    allowed_event_ids: set[str],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping) or set(value) != {"labels"}:
        raise ValueError("machine response must contain only labels")
    raw_labels = value["labels"]
    if not isinstance(raw_labels, list):
        raise ValueError("machine response labels must be an array")
    required = {
        "span_id",
        "terminal_reason",
        "required_for_current_target",
        "requirement_uncertain",
        "relation_target_ids",
        "obligations",
        "evidence_event_ids",
        "reactivation_risk",
    }
    parsed: list[dict[str, Any]] = []
    for raw in raw_labels:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError("machine boolean relation label has missing or additional fields")
        span_id = str(raw["span_id"])
        reason = str(raw["terminal_reason"])
        targets = tuple(sorted(set(map(str, raw["relation_target_ids"]))))
        obligations = tuple(sorted(set(map(str, raw["obligations"]))))
        evidence = tuple(sorted(set(map(str, raw["evidence_event_ids"]))))
        needed = raw["required_for_current_target"]
        need_uncertain = raw["requirement_uncertain"]
        risk = raw["reactivation_risk"]
        if span_id not in expected_span_ids:
            raise ValueError(f"unknown machine-label span ID: {span_id}")
        if reason not in TERMINAL_REASONS:
            raise ValueError(
                f"invalid terminal_reason enum for {span_id}: {reason!r}; "
                f"allowed={list(TERMINAL_REASONS)!r}"
            )
        for name, item in (
            ("required_for_current_target", needed),
            ("requirement_uncertain", need_uncertain),
            ("reactivation_risk", risk),
        ):
            if not isinstance(item, bool):
                raise ValueError(f"{name} must be boolean for {span_id}")
        if not set(targets).issubset(expected_span_ids):
            raise ValueError("machine boolean label contains an unknown relation target")
        if not set(obligations).issubset(OBLIGATIONS):
            raise ValueError("machine boolean label contains an unknown obligation")
        if not set(evidence).issubset(allowed_event_ids):
            raise ValueError("machine boolean label contains an unknown evidence event")
        parsed.append(
            {
                "span_id": span_id,
                "disposition": derive_relation_boolean_disposition(
                    terminal_reason=reason,
                    required_for_current_target=needed,
                    requirement_uncertain=need_uncertain,
                    obligations=obligations,
                    reactivation_risk=risk,
                ),
                "terminal_reason": reason,
                "required_for_current_target": needed,
                "requirement_uncertain": need_uncertain,
                "relation_target_ids": list(targets),
                "obligations": list(obligations),
                "evidence_event_ids": list(evidence),
                "reactivation_risk": risk,
                "disposition_provenance": (
                    "deterministic_relation_first_boolean_v2"
                ),
            }
        )
    observed = [item["span_id"] for item in parsed]
    if len(observed) != len(set(observed)) or set(observed) != expected_span_ids:
        raise ValueError("machine response has missing or duplicate span labels")
    return tuple(sorted(parsed, key=lambda item: item["span_id"]))


def remap_labels_to_original(
    labels: Sequence[Mapping[str, Any]], mapping: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    spans = {item["opaque_span_id"]: item["span_id"] for item in mapping["spans"]}
    events = {item["opaque_event_id"]: item["event_id"] for item in mapping["events"]}
    return tuple(
        sorted(
            (
                {
                    **dict(label),
                    "span_id": spans[str(label["span_id"])],
                    "relation_target_ids": sorted(
                        spans[str(item)] for item in label["relation_target_ids"]
                    ),
                    "evidence_event_ids": sorted(
                        events[str(item)] for item in label["evidence_event_ids"]
                    ),
                }
                for label in labels
            ),
            key=lambda item: item["span_id"],
        )
    )


def consensus_labels(
    pass_a: Sequence[Mapping[str, Any]], pass_b: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    a_map = {str(item["span_id"]): item for item in pass_a}
    b_map = {str(item["span_id"]): item for item in pass_b}
    if set(a_map) != set(b_map):
        raise ValueError("annotation passes do not cover the same spans")
    rows: list[dict[str, Any]] = []
    for span_id in sorted(a_map):
        a = a_map[span_id]
        b = b_map[span_id]
        fields = ["disposition", "terminal_reason", "relation_target_ids", "obligations"]
        relation_first = "current_target_need" in a or "current_target_need" in b
        relation_boolean = (
            "required_for_current_target" in a or "required_for_current_target" in b
        )
        if relation_first:
            fields.append("current_target_need")
        if relation_boolean:
            fields.extend(
                ["required_for_current_target", "requirement_uncertain"]
            )
        agreed = all(
            field in a and field in b and a[field] == b[field] for field in fields
        )
        if agreed:
            result = {
                "disposition": a["disposition"],
                "terminal_reason": a["terminal_reason"],
                "relation_target_ids": list(a["relation_target_ids"]),
                "obligations": list(a["obligations"]),
                "evidence_event_ids": sorted(
                    set(a["evidence_event_ids"]) | set(b["evidence_event_ids"])
                ),
                "reactivation_risk": bool(
                    a["reactivation_risk"] or b["reactivation_risk"]
                ),
            }
            if relation_first:
                result.update(
                    {
                        "current_target_need": a["current_target_need"],
                        "disposition_provenance": (
                            "deterministic_relation_first_v1"
                        ),
                    }
                )
            if relation_boolean:
                result.update(
                    {
                        "required_for_current_target": a[
                            "required_for_current_target"
                        ],
                        "requirement_uncertain": a["requirement_uncertain"],
                        "disposition_provenance": (
                            "deterministic_relation_first_boolean_v2"
                        ),
                    }
                )
        else:
            result = {
                "disposition": "uncertain",
                "terminal_reason": "unknown",
                "relation_target_ids": [],
                "obligations": sorted(
                    set(a["obligations"]) | set(b["obligations"])
                ),
                "evidence_event_ids": sorted(
                    set(a["evidence_event_ids"]) | set(b["evidence_event_ids"])
                ),
                "reactivation_risk": True,
            }
            if relation_first:
                result.update(
                    {
                        "current_target_need": "uncertain",
                        "disposition_provenance": (
                            "deterministic_relation_first_v1"
                        ),
                    }
                )
            if relation_boolean:
                result.update(
                    {
                        "required_for_current_target": False,
                        "requirement_uncertain": True,
                        "disposition_provenance": (
                            "deterministic_relation_first_boolean_v2"
                        ),
                    }
                )
        rows.append({"span_id": span_id, "machine_consensus": agreed, **result})
    return tuple(rows)


def cohen_kappa_binary(a_values: Sequence[bool], b_values: Sequence[bool]) -> float:
    if len(a_values) != len(b_values) or not a_values:
        raise ValueError("binary kappa requires two non-empty equal-length samples")
    observed = sum(a == b for a, b in zip(a_values, b_values, strict=True)) / len(a_values)
    a_counts = Counter(a_values)
    b_counts = Counter(b_values)
    expected = sum(
        (a_counts[value] / len(a_values)) * (b_counts[value] / len(b_values))
        for value in (False, True)
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)
