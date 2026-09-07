"""Conservative four-dimensional lifecycle inference for policy v4."""

from __future__ import annotations

# ruff: noqa: F401

import json
import re
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable
from ..capture import estimate_tokens
from ..graph import TraceGraph
from ..schema import EdgeType, Node, NodeType, RelevanceState, RetentionObligation, SemanticOutcome, ToolStatus, ValidityState
from ..schema import StorageState
from .span_analysis import _group_spans
from .types import POLICY_API_VERSION, ContextPlan, LifecycleRecord, MemorySnapshot, MemorySpan, stable_value_hash

_WORDS = re.compile(r"[\w.-]+", re.UNICODE)

_CALL_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}

_RESULT_TYPES = {NodeType.OBSERVATION, NodeType.ERROR}

_EVIDENCE_TYPES = {NodeType.OBSERVATION, NodeType.ERROR, NodeType.DECISION}

_CAUSAL_EDGES = {
    EdgeType.PRODUCES,
    EdgeType.FAILED_WITH,
    EdgeType.USES,
    EdgeType.SUPPORTS,
    EdgeType.BLOCKS,
    EdgeType.RESOLVES,
    EdgeType.SUPERSEDES,
    EdgeType.RETRIES,
    EdgeType.LEADS_TO,
    EdgeType.PROVIDES_INPUT,
    EdgeType.RETRIED_BY,
    EdgeType.RESOLVED_BY,
    EdgeType.SUPERSEDED_BY,
}

_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.INCONCLUSIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}

def _terms(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        text = " ".join(f"{key} {item}" for key, item in value.items())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        text = " ".join(map(str, value))
    else:
        text = str(value)
    return tuple(sorted(set(match.casefold() for match in _WORDS.findall(text))))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _call_id(node: Node) -> str:
    content = _mapping(node.content)
    return str(node.metadata.get("call_id") or content.get("call_id") or "")


def _tool_name(node: Node) -> str:
    content = _mapping(node.content)
    return str(node.metadata.get("tool_name") or content.get("tool_name") or "")


def _fact(node: Node) -> tuple[str, str, str] | None:
    content = _mapping(node.content)
    entity = node.metadata.get("entity_id", content.get("entity_id", content.get("entity")))
    field = node.metadata.get("field", content.get("field", content.get("slot")))
    declared = node.metadata.get("value", content.get("value"))
    if declared is None and field is not None:
        declared = content.get(str(field))
    if entity is None or field is None or declared is None:
        return None
    value = json.dumps(declared, ensure_ascii=False, sort_keys=True, default=str)
    return str(entity).casefold(), str(field).casefold(), value


def _complete_update(node: Node) -> bool:
    content = _mapping(node.content)
    if node.metadata.get("partial") or content.get("partial"):
        return False
    status = str(node.metadata.get("tool_status") or content.get("status") or "").casefold()
    return status not in {ToolStatus.PARTIAL_SUCCESS.value, "incomplete", "partial"}


def _verified_consumers(graph: TraceGraph, node: Node, cutoff: int) -> tuple[str, ...]:
    candidates = [
        edge for edge in graph.outgoing(node.node_id, EdgeType.PROVIDES_INPUT)
        if graph.nodes[edge.target].step_id <= cutoff
    ]
    candidates.extend(
        edge for edge in graph.incoming(node.node_id, EdgeType.USES)
        if graph.nodes[edge.source].step_id <= cutoff
    )
    verified: list[str] = []
    source_fact = _fact(node)
    for edge in candidates:
        consumer_id = edge.target if edge.source == node.node_id else edge.source
        consumer = graph.nodes[consumer_id]
        if edge.confidence != 1.0:
            continue
        if edge.metadata.get("type_value_match") is True:
            verified.append(consumer_id)
            continue
        consumed = consumer.metadata.get("consumed_facts", ())
        if isinstance(consumed, Mapping):
            consumed = (consumed,)
        declared = {
            (
                str(item.get("entity")).casefold(),
                str(item.get("field")).casefold(),
                json.dumps(
                    item.get("value"), ensure_ascii=False, sort_keys=True, default=str
                ),
            )
            for item in consumed
            if isinstance(item, Mapping)
            and item.get("entity") is not None
            and item.get("field") is not None
            and "value" in item
        }
        if source_fact is not None and source_fact in declared:
            verified.append(consumer_id)
    return tuple(sorted(set(verified)))


def _verified_superseders(graph: TraceGraph, node: Node, cutoff: int) -> tuple[str, ...]:
    candidates = [
        edge for edge in graph.outgoing(node.node_id, EdgeType.SUPERSEDED_BY)
        if graph.nodes[edge.target].step_id <= cutoff
    ]
    candidates.extend(
        edge for edge in graph.incoming(node.node_id, EdgeType.SUPERSEDES)
        if graph.nodes[edge.source].step_id <= cutoff
    )
    old = _fact(node)
    verified: list[str] = []
    for edge in candidates:
        newer_id = edge.target if edge.source == node.node_id else edge.source
        newer = graph.nodes[newer_id]
        current = _fact(newer)
        if (
            edge.confidence == 1.0
            and old is not None
            and current is not None
            and old[:2] == current[:2]
            and newer.step_id >= node.step_id
            and _complete_update(newer)
        ):
            verified.append(newer_id)
    return tuple(sorted(set(verified)))


def _resolved_by(graph: TraceGraph, node: Node, cutoff: int) -> tuple[str, ...]:
    targets = [
        edge.target for edge in graph.outgoing(node.node_id, EdgeType.RESOLVED_BY)
        if edge.confidence == 1.0 and graph.nodes[edge.target].step_id <= cutoff
    ]
    targets.extend(
        edge.source for edge in graph.incoming(node.node_id, EdgeType.RESOLVES)
        if edge.confidence == 1.0 and graph.nodes[edge.source].step_id <= cutoff
    )
    return tuple(sorted(set(targets)))


def _allowed_tools(goal_context: Mapping[str, Any]) -> set[str]:
    names: set[str] = set(map(str, goal_context.get("allowed_tools", ())))
    for schema in goal_context.get("tool_schemas", ()):
        body = schema.get("function", schema) if isinstance(schema, Mapping) else {}
        if isinstance(body, Mapping) and body.get("name"):
            names.add(str(body["name"]))
    return names


def _classify(
    graph: TraceGraph,
    node: Node,
    cutoff: int,
    allowed_tools: set[str],
    *,
    preserve_failures: bool,
    preserve_constraints: bool,
    archive_reader: Any = None,
) -> LifecycleRecord:
    profile = node.lifecycle_profile
    reasons: list[str] = []
    obligations = {item.value for item in profile.obligations}
    uncertain = profile.confidence < 1.0
    consumers = _verified_consumers(graph, node, cutoff)
    superseders = _verified_superseders(graph, node, cutoff)
    resolvers = _resolved_by(graph, node, cutoff)
    content = _mapping(node.content)
    status = str(node.metadata.get("tool_status") or content.get("status") or "").casefold()
    outcome = str(node.metadata.get("semantic_outcome") or "").casefold()
    negative = node.node_type == NodeType.ERROR or outcome in _NEGATIVE_OUTCOMES

    if node.node_type in _CALL_TYPES and allowed_tools and _tool_name(node) not in allowed_tools:
        uncertain = True
        reasons.append("unknown_tool")
    if status in {ToolStatus.PARTIAL_SUCCESS.value, "partial", "incomplete"}:
        uncertain = True
        reasons.append("partial_result")
    if node.node_type in _CALL_TYPES | _RESULT_TYPES and not node.raw_ref:
        uncertain = True
        reasons.append("missing_raw_reference")
    if profile.storage == StorageState.ARCHIVED and node.raw_ref:
        if not callable(archive_reader):
            uncertain = True
            reasons.append("archive_verifier_unavailable")
        else:
            try:
                archive_reader(node.raw_ref)
            except (OSError, KeyError, TypeError, ValueError, RuntimeError):
                uncertain = True
                reasons.append("archive_round_trip_failed")
    explicit_relations = bool(
        graph.resolving_edges(node.node_id) or graph.superseding_edges(node.node_id)
    )
    if explicit_relations and not (resolvers or superseders):
        uncertain = True
        reasons.append("unverified_or_conflicting_relation")
    if resolvers and superseders:
        uncertain = True
        reasons.append("relation_conflict")

    if negative and not resolvers:
        validity = ValidityState.NEGATIVE_UNRESOLVED.value
        if preserve_failures:
            obligations.add(RetentionObligation.RETAIN_UNTIL_ACTION_COMPLETE.value)
        reasons.append("unresolved_failure")
    elif negative:
        validity = ValidityState.NEGATIVE_RESOLVED.value
        reasons.append("resolved_failure")
    elif superseders:
        validity = ValidityState.SUPERSEDED.value
        reasons.append("complete_same_field_update")
    elif profile.validity != ValidityState.UNKNOWN:
        validity = profile.validity.value
    else:
        validity = ValidityState.VALID.value

    if node.node_type == NodeType.CONSTRAINT and node.active and preserve_constraints:
        obligations.add(RetentionObligation.ACTIVE_CONSTRAINT.value)
        reasons.append("active_constraint")
    if node.side_effect:
        obligations.add(RetentionObligation.AUDIT_REQUIRED.value)
        reasons.append("side_effect_receipt")
    if node.node_type in _EVIDENCE_TYPES and node.metadata.get("critical_evidence"):
        obligations.add(RetentionObligation.CRITICAL_EVIDENCE.value)
    if uncertain:
        reasons.append("uncertainty_defaults_to_retain")

    if superseders:
        relevance = RelevanceState.DORMANT.value
    elif consumers:
        relevance = RelevanceState.CONSUMED.value
        reasons.append("type_and_value_matched_consumption")
    elif node.active or obligations or uncertain:
        relevance = RelevanceState.ACTIVE.value
    else:
        relevance = RelevanceState.DORMANT.value
    return LifecycleRecord(
        event_id=node.node_id,
        relevance=relevance,
        validity=validity,
        storage=profile.storage.value,
        retention_obligations=tuple(obligations),
        uncertain=uncertain,
        reasons=tuple(reasons),
    )
