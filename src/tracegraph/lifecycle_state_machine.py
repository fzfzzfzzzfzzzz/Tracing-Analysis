"""Symbolic entity/field lifecycle replay for the Phase 5.2 Scheme A pilot."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .decision_state import stable_digest
from .graph import TraceGraph
from .lifecycle_annotation import complete_tool_spans
from .liveness import EventSpan, _group_spans
from .schema import EdgeType, Node, NodeType, SemanticOutcome


_CALL_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}
_NEGATIVE = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    return value


def _flatten(value: Any) -> tuple[Any, ...]:
    if isinstance(value, Mapping):
        return tuple(item for key in sorted(value, key=str) for item in _flatten(value[key]))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(item for child in value for item in _flatten(child))
    return (value,)


@dataclass(frozen=True, slots=True)
class ToolEffectSpec:
    tool_name: str
    effect_type: str
    entity_type: str
    entity_keys: tuple[str, ...]
    read_scope: tuple[str, ...]
    write_scope: tuple[str, ...]
    snapshot: str
    receipt_required: bool
    invalidation_scope: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ToolEffectSpec":
        spec = cls(
            tool_name=str(value["tool_name"]),
            effect_type=str(value["effect_type"]),
            entity_type=str(value["entity_type"]),
            entity_keys=tuple(map(str, value["entity_keys"])),
            read_scope=tuple(map(str, value["read_scope"])),
            write_scope=tuple(map(str, value["write_scope"])),
            snapshot=str(value["snapshot"]),
            receipt_required=bool(value["receipt_required"]),
            invalidation_scope=tuple(map(str, value["invalidation_scope"])),
        )
        if spec.effect_type not in {"read", "lookup", "query", "write", "handoff"}:
            raise ValueError(f"unsupported tool effect type: {spec.effect_type}")
        if spec.snapshot not in {"complete", "partial", "none"}:
            raise ValueError(f"unsupported snapshot kind: {spec.snapshot}")
        if spec.effect_type in {"write", "handoff"} and not spec.receipt_required:
            raise ValueError("write/handoff ToolEffectSpec must retain its receipt")
        return spec


def load_tool_effect_registry(config: Mapping[str, Any]) -> dict[str, ToolEffectSpec]:
    specs = [ToolEffectSpec.from_dict(item) for item in config["tool_effect_specs"]]
    registry = {item.tool_name: item for item in specs}
    if len(registry) != 15:
        raise ValueError("Phase 5.2 ToolEffectSpec registry must contain 15 tools")
    return registry


@dataclass(frozen=True, slots=True)
class LifecyclePrediction:
    span_id: str
    disposition: str
    terminal_reason: str
    source_event_id: str
    target_event_ids: tuple[str, ...]
    entity_type: str
    entity_key: tuple[tuple[str, Any], ...]
    field_scope: tuple[str, ...]
    verifier: str
    confidence: float
    obligations: tuple[str, ...]
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.disposition not in {
            "live_critical",
            "live_noncritical",
            "safe_to_evict",
            "uncertain",
        }:
            raise ValueError("invalid LifecyclePrediction disposition")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("invalid LifecyclePrediction confidence")
        if self.disposition == "safe_to_evict" and set(self.obligations) & {
            "policy",
            "confirmation",
            "receipt",
            "audit",
        }:
            raise ValueError("protected obligation cannot be predicted safe")

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "disposition": self.disposition,
            "terminal_reason": self.terminal_reason,
            "source_event_id": self.source_event_id,
            "target_event_ids": list(self.target_event_ids),
            "entity_type": self.entity_type,
            "entity_key": [[key, value] for key, value in self.entity_key],
            "field_scope": list(self.field_scope),
            "verifier": self.verifier,
            "confidence": self.confidence,
            "obligations": list(self.obligations),
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class _Exchange:
    span: EventSpan
    call: Node | None
    result: Node | None
    spec: ToolEffectSpec | None
    arguments: Mapping[str, Any]
    entity_key: tuple[tuple[str, Any], ...]
    successful: bool

    @property
    def source_event_id(self) -> str:
        return self.result.node_id if self.result is not None else self.span.node_ids[-1]


def _tool_name(node: Node) -> str:
    content = node.content if isinstance(node.content, Mapping) else {}
    return str(node.metadata.get("tool_name") or content.get("tool_name") or "")


def _arguments(node: Node) -> Mapping[str, Any]:
    content = node.content if isinstance(node.content, Mapping) else {}
    value = content.get("arguments")
    return value if isinstance(value, Mapping) else {}


def _successful(node: Node | None) -> bool:
    if node is None or node.node_type != NodeType.OBSERVATION:
        return False
    status = str(node.metadata.get("status") or "success")
    outcome = str(node.metadata.get("semantic_outcome") or "")
    return status == "success" and outcome not in _NEGATIVE


def _entity_key(spec: ToolEffectSpec, arguments: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    if any(key not in arguments or arguments[key] in (None, "") for key in spec.entity_keys):
        return ()
    return tuple((key, _canonical(arguments[key])) for key in spec.entity_keys)


def _exchanges(
    graph: TraceGraph, registry: Mapping[str, ToolEffectSpec]
) -> tuple[_Exchange, ...]:
    spans = list(complete_tool_spans(graph))
    spans.sort(
        key=lambda item: (
            min(item.message_ordinals) if item.message_ordinals else 0,
            item.span_id,
        )
    )
    values: list[_Exchange] = []
    for span in spans:
        calls = [graph.nodes[item] for item in span.node_ids if graph.nodes[item].node_type in _CALL_TYPES]
        call = calls[0] if len(calls) == 1 else None
        results: list[Node] = []
        if call is not None:
            results = [
                graph.nodes[edge.target]
                for edge in graph.outgoing(call.node_id)
                if edge.edge_type in {EdgeType.PRODUCES, EdgeType.FAILED_WITH}
                and edge.target in span.node_ids
            ]
        result = results[0] if len(results) == 1 else None
        name = _tool_name(call) if call is not None else ""
        spec = registry.get(name)
        arguments = _arguments(call) if call is not None else {}
        values.append(
            _Exchange(
                span=span,
                call=call,
                result=result,
                spec=spec,
                arguments=arguments,
                entity_key=_entity_key(spec, arguments) if spec is not None else (),
                successful=_successful(result),
            )
        )
    return tuple(values)


def _same_entity(left: _Exchange, right: _Exchange) -> bool:
    return bool(
        left.spec is not None
        and right.spec is not None
        and left.spec.entity_type == right.spec.entity_type
        and left.entity_key == right.entity_key
        and (left.entity_key or not left.spec.entity_keys)
    )


def _complete_scalar(result: Node | None) -> Any | None:
    if result is None:
        return None
    value = result.content
    if isinstance(value, (str, int, float, bool)) and not isinstance(value, bool):
        return value
    if isinstance(value, Mapping) and len(value) == 1:
        candidate = next(iter(value.values()))
        if isinstance(candidate, (str, int, float)) and not isinstance(candidate, bool):
            return candidate
    return None


def _prediction(
    exchange: _Exchange,
    *,
    disposition: str,
    reason: str,
    targets: Sequence[str] = (),
    verifier: str,
    confidence: float,
    obligations: Sequence[str] = (),
    provenance: Sequence[str] = (),
) -> LifecyclePrediction:
    spec = exchange.spec
    return LifecyclePrediction(
        span_id=exchange.span.span_id,
        disposition=disposition,
        terminal_reason=reason,
        source_event_id=exchange.source_event_id,
        target_event_ids=tuple(sorted(set(map(str, targets)))),
        entity_type=spec.entity_type if spec else "unknown",
        entity_key=exchange.entity_key,
        field_scope=tuple(spec.read_scope or spec.write_scope) if spec else (),
        verifier=verifier,
        confidence=confidence,
        obligations=tuple(sorted(set(map(str, obligations)))),
        provenance=tuple(sorted(set(map(str, provenance)))),
    )


def replay_lifecycle_state_machine(
    graph: TraceGraph,
    *,
    registry: Mapping[str, ToolEffectSpec],
    referenced_event_ids: Sequence[str] = (),
) -> tuple[LifecyclePrediction, ...]:
    """Replay one strict prefix without modifying it or creating graph edges."""

    graph_before = stable_digest(graph.to_dict())
    exchanges = _exchanges(graph, registry)
    referenced = set(map(str, referenced_event_ids))
    predictions: list[LifecyclePrediction] = []
    field_versions: dict[tuple[str, str], dict[str, int]] = {}
    for index, current in enumerate(exchanges):
        spec = current.spec
        later = exchanges[index + 1 :]
        if spec is None or current.call is None or current.result is None:
            predictions.append(
                _prediction(
                    current,
                    disposition="uncertain",
                    reason="unknown",
                    verifier="unknown_or_ambiguous_tool_span_fail_closed_v1",
                    confidence=0.0,
                    provenance=("registry_or_span_shape_incomplete",),
                )
            )
            continue
        entity_identity = (spec.entity_type, stable_digest(current.entity_key))
        versions = field_versions.setdefault(entity_identity, {})
        version_provenance = tuple(
            f"{field}:v{versions.get(field, 0)}" for field in spec.read_scope
        )

        if set(current.span.node_ids) & referenced:
            predictions.append(
                _prediction(
                    current,
                    disposition="live_critical",
                    reason="active",
                    verifier="explicit_query_reactivation_v1",
                    confidence=1.0,
                    obligations=("audit",),
                    provenance=(*version_provenance, "explicit_event_reference"),
                )
            )
            continue
        if spec.receipt_required or current.call.side_effect:
            for field in spec.write_scope:
                versions[field] = versions.get(field, 0) + int(current.successful)
            predictions.append(
                _prediction(
                    current,
                    disposition="live_critical",
                    reason="audit_required",
                    verifier="side_effect_receipt_retention_v1",
                    confidence=1.0,
                    obligations=("receipt", "audit"),
                    provenance=("receipt_required",),
                )
            )
            continue
        if not current.successful:
            retry = next(
                (
                    item
                    for item in later
                    if item.spec is not None
                    and item.spec.tool_name == spec.tool_name
                    and item.entity_key == current.entity_key
                    and item.successful
                ),
                None,
            )
            if retry is None:
                predictions.append(
                    _prediction(
                        current,
                        disposition="live_critical",
                        reason="active",
                        verifier="unresolved_retry_retention_v1",
                        confidence=1.0,
                        obligations=("retry",),
                        provenance=("no_successful_retry_in_prefix",),
                    )
                )
            else:
                predictions.append(
                    _prediction(
                        current,
                        disposition="safe_to_evict",
                        reason="resolved",
                        targets=(retry.span.span_id,),
                        verifier="successful_retry_resolution_v1",
                        confidence=1.0,
                        provenance=(retry.source_event_id,),
                    )
                )
            continue

        scalar = _complete_scalar(current.result)
        if scalar is not None:
            consumer = next(
                (
                    item
                    for item in later
                    if scalar in _flatten(item.arguments)
                ),
                None,
            )
            if consumer is not None:
                predictions.append(
                    _prediction(
                        current,
                        disposition="safe_to_evict",
                        reason="consumed",
                        targets=(consumer.span.span_id,),
                        verifier="complete_scalar_use_def_consumption_v1",
                        confidence=1.0,
                        provenance=(consumer.source_event_id,),
                    )
                )
                continue

        replacement = next(
            (
                item
                for item in later
                if item.spec is not None
                and item.spec.tool_name == spec.tool_name
                and _same_entity(current, item)
                and item.successful
                and spec.snapshot == "complete"
                and item.spec.snapshot == "complete"
            ),
            None,
        )
        if replacement is not None:
            predictions.append(
                _prediction(
                    current,
                    disposition="safe_to_evict",
                    reason="superseded",
                    targets=(replacement.span.span_id,),
                    verifier="complete_snapshot_supersession_v1",
                    confidence=1.0,
                    provenance=(replacement.source_event_id, *version_provenance),
                )
            )
            continue

        invalidator = next(
            (
                item
                for item in later
                if item.spec is not None
                and item.spec.effect_type == "write"
                and item.successful
                and _same_entity(current, item)
                and (
                    "*" in spec.read_scope
                    or bool(set(spec.read_scope) & set(item.spec.invalidation_scope))
                )
            ),
            None,
        )
        if invalidator is not None:
            fully_invalidated = "*" not in spec.read_scope and set(spec.read_scope).issubset(
                invalidator.spec.invalidation_scope if invalidator.spec else ()
            )
            predictions.append(
                _prediction(
                    current,
                    disposition="safe_to_evict" if fully_invalidated else "uncertain",
                    reason="invalidated" if fully_invalidated else "unknown",
                    targets=(invalidator.span.span_id,),
                    verifier=(
                        "complete_field_invalidation_v1"
                        if fully_invalidated
                        else "partial_invalidation_fail_closed_v1"
                    ),
                    confidence=1.0 if fully_invalidated else 0.0,
                    provenance=(invalidator.source_event_id, *version_provenance),
                )
            )
            continue

        predictions.append(
            _prediction(
                current,
                disposition="live_noncritical",
                reason="active",
                verifier="no_terminal_relation_found_v1",
                confidence=1.0,
                provenance=version_provenance,
            )
        )
    if stable_digest(graph.to_dict()) != graph_before:
        raise RuntimeError("Phase 5.2 state machine mutated the EventGraph")
    return tuple(sorted(predictions, key=lambda item: item.span_id))


def build_forbidden_offline_projection(
    graph: TraceGraph,
    predictions: Sequence[LifecyclePrediction],
) -> dict[str, Any]:
    """Build a cost-only message projection carrying an explicit send prohibition."""

    safe_candidates = {
        item.span_id for item in predictions if item.disposition == "safe_to_evict"
    }
    call_spans = {item.span_id: item for item in complete_tool_spans(graph)}
    protocol_spans = [
        item
        for item in _group_spans(graph, int(graph.metadata["cutoff_step"]))
        if item.span_type == "tool_exchange"
    ]
    removable_groups: list[tuple[EventSpan, set[str]]] = []
    for protocol_span in protocol_spans:
        member_calls = {
            span_id
            for span_id, call_span in call_spans.items()
            if set(call_span.node_ids) & set(protocol_span.node_ids)
        }
        if member_calls and member_calls.issubset(safe_candidates):
            removable_groups.append((protocol_span, member_calls))
    safe = {span_id for _, member_calls in removable_groups for span_id in member_calls}
    removed_ordinals = {
        ordinal for protocol_span, _ in removable_groups for ordinal in protocol_span.message_ordinals
    }
    retained = [
        message
        for ordinal, message in _messages_with_ordinals(graph)
        if ordinal not in removed_ordinals
    ]
    projection: dict[str, Any] = {
        "schema_version": "phase52_forbidden_offline_projection_v1",
        "never_send_to_provider": True,
        "source_graph_hash": stable_digest(graph.to_dict()),
        "evicted_span_ids": sorted(safe),
        "deferred_safe_span_ids": sorted(safe_candidates - safe),
        "removed_message_ordinals": sorted(removed_ordinals),
        "messages": retained,
    }
    projection["projection_sha256"] = stable_digest(projection)
    return projection


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _messages_with_ordinals(graph: TraceGraph) -> tuple[tuple[int, dict[str, Any]], ...]:
    grouped: defaultdict[int, list[Node]] = defaultdict(list)
    for node in graph.nodes.values():
        ordinal = node.metadata.get("source_message_ordinal")
        if isinstance(ordinal, int) and ordinal > 0:
            grouped[ordinal].append(node)
    messages: list[tuple[int, dict[str, Any]]] = []
    for ordinal, nodes in sorted(grouped.items()):
        ordered = sorted(nodes, key=lambda item: (item.node_type.value, item.node_id))
        results = [
            node for node in ordered if node.node_type in {NodeType.OBSERVATION, NodeType.ERROR}
        ]
        if results:
            messages.extend(
                (
                    ordinal,
                    {
                        "role": "tool",
                        "tool_call_id": str(node.metadata.get("call_id") or node.node_id),
                        "content": _text(node.content),
                    },
                )
                for node in results
            )
            continue
        user_nodes = [
            node
            for node in ordered
            if node.metadata.get("source") == "user_message"
            or node.node_type in {NodeType.GOAL, NodeType.SUBGOAL}
        ]
        calls = [node for node in ordered if node.node_type in _CALL_TYPES]
        decisions = [node for node in ordered if node.node_type == NodeType.DECISION]
        if user_nodes and not calls and not decisions:
            messages.append((ordinal, {"role": "user", "content": _text(user_nodes[-1].content)}))
            continue
        message: dict[str, Any] = {
            "role": "assistant",
            "content": _text(decisions[-1].content) if decisions else "",
        }
        if calls:
            message["tool_calls"] = [
                {
                    "id": str(call.metadata.get("call_id") or call.node_id),
                    "type": "function",
                    "function": {
                        "name": _tool_name(call),
                        "arguments": _text(_arguments(call)),
                    },
                }
                for call in calls
            ]
        messages.append((ordinal, message))
    return tuple(messages)
