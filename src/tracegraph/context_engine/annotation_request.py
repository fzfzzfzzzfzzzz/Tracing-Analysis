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
    DISPOSITIONS as DISPOSITIONS,
    OBLIGATIONS as OBLIGATIONS,
    TERMINAL_REASONS as TERMINAL_REASONS,
    _RELATION_BOOLEAN_SYSTEM_PROMPT as _RELATION_BOOLEAN_SYSTEM_PROMPT,
    _RELATION_SYSTEM_PROMPT as _RELATION_SYSTEM_PROMPT,
    _SYSTEM_PROMPT as _SYSTEM_PROMPT,
)



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


# Imported after definitions so mutually-referential helpers initialize safely.
from .annotation_config import (
    assert_prefix_only_payload as assert_prefix_only_payload,
    canonical_json as canonical_json,
)

from .annotation_schema import (
    PreparedAnnotationRequest as PreparedAnnotationRequest,
    _current_target as _current_target,
    _digest_rank as _digest_rank,
    _event_view as _event_view,
    _node_order as _node_order,
    _opaque_map as _opaque_map,
    _split_for_task as _split_for_task,
    annotation_response_function_schema as annotation_response_function_schema,
    complete_tool_spans as complete_tool_spans,
)
