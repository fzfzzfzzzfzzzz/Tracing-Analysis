"""Scoped compact negative-evidence cards for phase-three context selection."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable

from .graph import TraceGraph
from .schema import (
    EdgeType,
    FailureCard,
    FailureClass,
    FailureExpiryTrigger,
    Node,
    NodeType,
    SemanticOutcome,
    ValidityState,
)


_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}

_MALFORMED_MARKERS = (
    "malformed",
    "invalid json",
    "invalid argument",
    "invalid parameter",
    "missing required",
    "schema validation",
    "syntax error",
    "unknown tool",
)


def _stable_card_id(operation_scope: str) -> str:
    digest = hashlib.sha256(operation_scope.encode("utf-8")).hexdigest()[:16]
    return f"failure_card_{digest}"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)


def _negative(node: Node) -> bool:
    return node.node_type == NodeType.ERROR or node.metadata.get("semantic_outcome") in {
        *_NEGATIVE_OUTCOMES,
    }


def _producer_call(graph: TraceGraph, node: Node) -> Node | None:
    edges = graph.incoming(node.node_id, EdgeType.FAILED_WITH)
    edges += graph.incoming(node.node_id, EdgeType.PRODUCES)
    if not edges:
        return None
    return graph.nodes[edges[-1].source]


def _operation_scope(graph: TraceGraph, node: Node) -> str:
    scope = node.lifecycle_profile.scope.get("operation_key")
    call = _producer_call(graph, node)
    if not scope and call is not None:
        scope = call.metadata.get("operation_key")
    if scope:
        return str(scope)
    tool_name = node.metadata.get("tool_name")
    if not tool_name and call is not None:
        tool_name = call.metadata.get("tool_name")
    return f"{tool_name or 'unknown_tool'}:node:{node.node_id}"


def _action_family(graph: TraceGraph, node: Node) -> str:
    call = _producer_call(graph, node)
    value = node.metadata.get("tool_name")
    if not value and call is not None:
        value = call.metadata.get("tool_name")
    return str(value or "unknown_tool")


def _entity_ids(operation_scope: str) -> tuple[str, ...]:
    try:
        payload = json.loads(operation_scope)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    identity = payload.get("identity", []) if isinstance(payload, dict) else []
    values: list[str] = []
    for item in identity:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            value = item[1]
            if isinstance(value, (str, int, float)):
                values.append(str(value))
            elif isinstance(value, list):
                values.extend(str(child) for child in value)
    return tuple(dict.fromkeys(values))


def _failure_class(node: Node) -> FailureClass:
    explicit = node.metadata.get("failure_class")
    if explicit:
        try:
            return FailureClass(str(explicit))
        except ValueError:
            pass
    if node.metadata.get("terminal", False):
        return FailureClass.TERMINAL
    if node.metadata.get("stale", False):
        return FailureClass.STALE
    if node.metadata.get("malformed_call", False):
        return FailureClass.MALFORMED
    if node.metadata.get("semantic_outcome") == SemanticOutcome.POLICY_DENIED.value:
        return FailureClass.POLICY_DENIED
    lowered = _content_text(node.content).lower()
    if any(marker in lowered for marker in _MALFORMED_MARKERS):
        return FailureClass.MALFORMED
    return FailureClass.ACTIONABLE


def classify_failure(node: Node) -> FailureClass:
    """Expose the deterministic Failure Card class for audit tooling."""

    return _failure_class(node)


def _call_arguments(graph: TraceGraph, node: Node) -> dict[str, Any]:
    call = _producer_call(graph, node)
    if call is None or not isinstance(call.content, dict):
        return {}
    arguments = call.content.get("arguments", {})
    return dict(arguments) if isinstance(arguments, dict) else {"value": arguments}


def _argument_diff(
    graph: TraceGraph,
    chain: list[Node],
) -> dict[str, Any]:
    if len(chain) < 2:
        return {}
    before = _call_arguments(graph, chain[-2])
    after = _call_arguments(graph, chain[-1])
    diff: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            diff[key] = {"before": before.get(key), "after": after.get(key)}
    return diff


def _correction(node: Node, failure_class: FailureClass) -> str:
    explicit = node.metadata.get("next_admissible_correction")
    if explicit:
        return str(explicit)
    if failure_class == FailureClass.POLICY_DENIED:
        return "change the plan or obtain the required authorization before retrying"
    if failure_class == FailureClass.MALFORMED:
        return "correct the tool name and arguments before retrying"
    if failure_class == FailureClass.TERMINAL:
        return "stop retrying this operation and choose another goal or path"
    return "change the failed arguments or use an admissible alternative path"


def _metadata_scopes(graph: TraceGraph, key: str) -> set[str]:
    value = graph.metadata.get(key, ())
    if isinstance(value, str):
        return {value}
    return {str(item) for item in value or ()}


def _corrected_syntax_exists(
    graph: TraceGraph,
    latest: Node,
    action_family: str,
) -> bool:
    for node in graph.find_nodes(node_types={NodeType.TOOL_CALL, NodeType.MCP_CALL}):
        if node.step_id <= latest.step_id:
            continue
        if node.metadata.get("tool_name") != action_family:
            continue
        if node.metadata.get("arguments_valid") is True:
            return True
    return False


def _recent_dependency_exists(graph: TraceGraph, node: Node, cutoff: int) -> bool:
    for edge in graph.outgoing(node.node_id):
        target = graph.nodes[edge.target]
        if target.step_id >= cutoff and edge.edge_type in {
            EdgeType.BLOCKS,
            EdgeType.PROVIDES_INPUT,
            EdgeType.SUPPORTS,
        }:
            return True
    return False


def _expiry_trigger(
    graph: TraceGraph,
    latest: Node,
    *,
    operation_scope: str,
    failure_class: FailureClass,
    action_family: str,
    ttl_steps: int | None,
) -> FailureExpiryTrigger | None:
    if graph.outgoing(latest.node_id, EdgeType.RESOLVED_BY) or (
        latest.lifecycle_profile.validity == ValidityState.NEGATIVE_RESOLVED
    ):
        return FailureExpiryTrigger.RESOLVED
    if graph.outgoing(latest.node_id, EdgeType.SUPERSEDED_BY) or (
        latest.lifecycle_profile.validity == ValidityState.SUPERSEDED
    ):
        return FailureExpiryTrigger.SUPERSEDED
    if operation_scope in _metadata_scopes(graph, "completed_operation_scopes"):
        return FailureExpiryTrigger.ALTERNATIVE_COMPLETED
    if operation_scope in _metadata_scopes(graph, "abandoned_operation_scopes"):
        return FailureExpiryTrigger.USER_ABANDONED
    if operation_scope in _metadata_scopes(graph, "constraint_changed_operation_scopes"):
        return FailureExpiryTrigger.CONSTRAINT_CHANGED
    accepted_ids = _metadata_scopes(graph, "accepted_failure_node_ids")
    if latest.metadata.get("final_accepted", False) or latest.node_id in accepted_ids:
        return FailureExpiryTrigger.FINAL_ACCEPTED
    if failure_class == FailureClass.MALFORMED and _corrected_syntax_exists(
        graph, latest, action_family
    ):
        return FailureExpiryTrigger.CORRECTED_SYNTAX
    if failure_class == FailureClass.TERMINAL:
        return FailureExpiryTrigger.TERMINAL
    if failure_class == FailureClass.STALE:
        return FailureExpiryTrigger.STALE
    if ttl_steps is not None and ttl_steps >= 0:
        latest_step = max((node.step_id for node in graph.nodes.values()), default=latest.step_id)
        cutoff = latest_step - ttl_steps
        if latest.step_id < cutoff and not _recent_dependency_exists(graph, latest, cutoff):
            return FailureExpiryTrigger.TTL_EXPIRED
    return None


def _event(
    card: FailureCard,
    event: str,
) -> dict[str, Any]:
    return {
        "event": event,
        "card_id": card.card_id,
        "operation_scope": card.operation_scope,
        "failure_class": card.failure_class.value,
        "expiry_trigger": (
            card.expiry_trigger.value if card.expiry_trigger is not None else None
        ),
        "source_node_ids": list(card.source_node_ids),
        "last_relevant_step": card.last_relevant_step,
    }


def record_failure_card_events(graph: TraceGraph, events: Iterable[dict[str, Any]]) -> None:
    """Append deterministic card audit events without duplicating repeated selects."""

    audit = graph.metadata.setdefault("failure_card_events", [])
    known = {
        (
            item.get("event"),
            item.get("card_id"),
            item.get("expiry_trigger"),
            tuple(item.get("source_node_ids", ())),
        )
        for item in audit
    }
    for item in events:
        key = (
            item.get("event"),
            item.get("card_id"),
            item.get("expiry_trigger"),
            tuple(item.get("source_node_ids", ())),
        )
        if key not in known:
            audit.append(item)
            known.add(key)


def build_failure_cards(
    graph: TraceGraph,
    *,
    ttl_steps: int | None = 8,
    confidence_threshold: float = 0.75,
) -> tuple[list[FailureCard], list[dict[str, Any]]]:
    """Build at most one active card for each operation scope.

    Resolved, superseded, terminal, stale, or explicitly expired chains produce
    audit events but no protected card. Policy-denied and malformed failures are
    retained as compact guidance because they still prescribe a plan or syntax
    correction.
    """

    grouped: dict[str, list[Node]] = defaultdict(list)
    for node in graph.find_nodes(node_types={NodeType.ERROR, NodeType.OBSERVATION}):
        if _negative(node):
            grouped[_operation_scope(graph, node)].append(node)

    cards: list[FailureCard] = []
    events: list[dict[str, Any]] = []
    for operation_scope, chain in sorted(grouped.items()):
        chain.sort(key=lambda node: (node.step_id, node.node_id))
        latest = chain[-1]
        failure_class = _failure_class(latest)
        action_family = _action_family(graph, latest)
        trigger = _expiry_trigger(
            graph,
            latest,
            operation_scope=operation_scope,
            failure_class=failure_class,
            action_family=action_family,
            ttl_steps=ttl_steps,
        )
        confidence = float(latest.metadata.get("failure_card_confidence", 1.0))
        card = FailureCard(
            card_id=_stable_card_id(operation_scope),
            operation_scope=operation_scope,
            action_family=action_family,
            entity_ids=_entity_ids(operation_scope),
            failure_class=failure_class,
            latest_failure_cause=_content_text(latest.content),
            failed_argument_diff=_argument_diff(graph, chain),
            next_admissible_correction=_correction(latest, failure_class),
            confidence=confidence,
            created_step=min(node.step_id for node in chain),
            last_relevant_step=latest.step_id,
            expiry_trigger=trigger,
            raw_archive_refs=tuple(
                node.raw_ref for node in chain if node.raw_ref is not None
            ),
            source_node_ids=tuple(node.node_id for node in chain),
        )
        if card.active and card.confidence >= confidence_threshold:
            cards.append(card)
            events.append(_event(card, "created" if len(chain) == 1 else "updated"))
        else:
            events.append(_event(card, "expired" if trigger is not None else "downgraded"))
    cards.sort(
        key=lambda card: (
            card.failure_class in {FailureClass.ACTIONABLE, FailureClass.POLICY_DENIED},
            card.confidence,
            card.last_relevant_step,
        ),
        reverse=True,
    )
    return cards, events
