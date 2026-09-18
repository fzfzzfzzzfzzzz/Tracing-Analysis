"""Definitions moved from ``tracegraph.liveness``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from ..decision_query import DecisionQuery
from ..decision_state import DecisionStateGraph, StateAtom, StateAtomType, StateEdgeType, stable_digest
from ..graph import TraceGraph
from ..policy_rules import PolicyRule
from ..schema import EdgeType, Node, NodeType, SemanticOutcome
from ..state_reducer import reduce_event_graph

from .liveness_constants import (
    ArchiveReader as ArchiveReader,
    _CALL_NODE_TYPES as _CALL_NODE_TYPES,
    _TOOL_NODE_TYPES as _TOOL_NODE_TYPES,
)



def _call_id(node: Node) -> str | None:
    content = node.content if isinstance(node.content, Mapping) else {}
    value = node.metadata.get("call_id") or content.get("call_id")
    return str(value) if value else None


def _message_ordinal(node: Node) -> int | None:
    value = node.metadata.get("source_message_ordinal")
    return int(value) if isinstance(value, int) and value > 0 else None


def _group_spans(event_graph: TraceGraph, cutoff_step: int) -> tuple[EventSpan, ...]:
    nodes = _visible_nodes(event_graph, cutoff_step)
    visible = {node.node_id for node in nodes}
    nodes_by_ordinal: dict[int, set[str]] = defaultdict(set)
    for node in nodes:
        ordinal = _message_ordinal(node)
        if ordinal is not None:
            nodes_by_ordinal[ordinal].add(node.node_id)

    grouped: set[str] = set()
    spans: list[EventSpan] = []
    calls_by_ordinal: dict[int, list[Node]] = defaultdict(list)
    for node in nodes:
        ordinal = _message_ordinal(node)
        if node.node_type in _CALL_NODE_TYPES and ordinal is not None:
            calls_by_ordinal[ordinal].append(node)

    for call_ordinal, calls in sorted(calls_by_ordinal.items()):
        member_ids = set(nodes_by_ordinal[call_ordinal])
        ordinals = {call_ordinal}
        call_ids: list[str] = []
        for call in calls:
            call_id = _call_id(call)
            if call_id:
                call_ids.append(call_id)
            for result in _result_nodes(event_graph, call, visible):
                member_ids.add(result.node_id)
                result_ordinal = _message_ordinal(result)
                if result_ordinal is not None:
                    ordinals.add(result_ordinal)
                    member_ids.update(nodes_by_ordinal[result_ordinal])
        members = [event_graph.nodes[event_id] for event_id in member_ids]
        spans.append(
            EventSpan.create(
                span_type="tool_exchange",
                node_ids=tuple(member_ids),
                message_ordinals=tuple(ordinals),
                call_ids=tuple(call_ids),
                raw_refs=tuple(node.raw_ref for node in members if node.raw_ref),
            )
        )
        grouped.update(member_ids)

    for ordinal, member_ids in sorted(nodes_by_ordinal.items()):
        remaining = member_ids.difference(grouped)
        if not remaining:
            continue
        members = [event_graph.nodes[event_id] for event_id in remaining]
        spans.append(
            EventSpan.create(
                span_type="message",
                node_ids=tuple(remaining),
                message_ordinals=(ordinal,),
                raw_refs=tuple(node.raw_ref for node in members if node.raw_ref),
            )
        )
        grouped.update(remaining)

    for node in nodes:
        if node.node_id in grouped:
            continue
        spans.append(
            EventSpan.create(
                span_type="unmapped_event",
                node_ids=(node.node_id,),
                message_ordinals=(),
                call_ids=tuple(item for item in (_call_id(node),) if item),
                raw_refs=tuple(item for item in (node.raw_ref,) if item),
            )
        )
    return tuple(spans)


def _verify_archive_span(
    event_graph: TraceGraph,
    span: EventSpan,
    archive_reader: ArchiveReader | None,
) -> tuple[bool, str]:
    tool_nodes = [
        event_graph.nodes[event_id]
        for event_id in span.node_ids
        if event_graph.nodes[event_id].node_type in _TOOL_NODE_TYPES
    ]
    if any(not node.raw_ref for node in tool_nodes):
        return False, "tool_span_missing_archive_reference"
    if archive_reader is None:
        return False, "archive_verifier_unavailable"
    try:
        for reference in sorted({node.raw_ref for node in tool_nodes if node.raw_ref}):
            archive_reader(reference)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
        return False, f"archive_round_trip_failed:{error.__class__.__name__}"
    return True, "archive_round_trip_verified"


def analyze_liveness(
    event_graph: TraceGraph,
    state: DecisionLifecycleGraph,
    roots: LivenessRoots,
    *,
    archive_reader: ArchiveReader | None = None,
) -> LiveSubgraph:
    """Compute live closure and a conservative, protocol-grouped evictable set."""

    if roots.lifecycle_hash != state.lifecycle_hash:
        raise ValueError("liveness roots do not belong to the supplied lifecycle state")
    live_atoms, closure = _state_closure(state, roots)
    visible_nodes = {
        node.node_id for node in _visible_nodes(event_graph, state.cutoff_step)
    }
    live_events = set(roots.root_event_ids)
    atom_map = state.decision_state.atom_map()
    for atom_id in live_atoms:
        atom = atom_map[atom_id]
        live_events.update(event_id for event_id in atom.source_event_ids if event_id in visible_nodes)

    spans = _group_spans(event_graph, state.cutoff_step)
    records = state.record_map()
    live_spans: set[str] = set()
    evicted_spans: set[str] = set()
    evicted_nodes: set[str] = set()
    uncertainty: list[dict[str, Any]] = [
        {"reason": reason, "action": "retained_conservatively"}
        for reason in roots.uncertainty_reasons
    ]
    span_closure: list[dict[str, Any]] = []

    for span in spans:
        if span.span_type != "tool_exchange":
            live_spans.add(span.span_id)
            continue
        member_records = [records[event_id] for event_id in span.node_ids]
        if live_events.intersection(span.node_ids):
            live_spans.add(span.span_id)
            span_closure.append(
                {
                    "span_id": span.span_id,
                    "reason": "span_contains_live_event",
                    "live_event_ids": sorted(live_events.intersection(span.node_ids)),
                }
            )
            live_events.update(span.node_ids)
            continue
        if not member_records or not all(record.terminal for record in member_records):
            live_spans.add(span.span_id)
            uncertainty.append(
                {
                    "span_id": span.span_id,
                    "reason": "span_has_nonterminal_or_uncertain_event",
                    "statuses": sorted({record.status for record in member_records}),
                    "action": "retained_conservatively",
                }
            )
            live_events.update(span.node_ids)
            continue
        archive_ok, archive_reason = _verify_archive_span(
            event_graph,
            span,
            archive_reader,
        )
        if not archive_ok:
            live_spans.add(span.span_id)
            uncertainty.append(
                {
                    "span_id": span.span_id,
                    "reason": archive_reason,
                    "action": "retained_conservatively",
                }
            )
            live_events.update(span.node_ids)
            continue
        evicted_spans.add(span.span_id)
        evicted_nodes.update(span.node_ids)
        span_closure.append(
            {
                "span_id": span.span_id,
                "reason": "outside_live_closure_with_explicit_terminal_lifecycle",
                "lifecycle_statuses": sorted({record.status for record in member_records}),
                "archive": archive_reason,
            }
        )

    live_nodes = visible_nodes.difference(evicted_nodes)
    root_provenance = tuple(root.to_dict() for root in roots.roots)
    lifecycle_reasons = tuple(
        record.to_dict() for record in state.event_records
    )
    return LiveSubgraph(
        lifecycle_hash=state.lifecycle_hash,
        roots_hash=roots.roots_hash,
        cutoff_step=state.cutoff_step,
        spans=spans,
        live_atom_ids=tuple(live_atoms),
        live_node_ids=tuple(live_nodes),
        evicted_node_ids=tuple(evicted_nodes),
        live_span_ids=tuple(live_spans),
        evicted_span_ids=tuple(evicted_spans),
        root_provenance=root_provenance,
        closure_provenance=tuple(closure + span_closure),
        lifecycle_reasons=lifecycle_reasons,
        uncertainty_records=tuple(uncertainty),
    )


# Imported after definitions so mutually-referential helpers initialize safely.
from .lifecycle_analysis import (
    _result_nodes as _result_nodes,
    _state_closure as _state_closure,
    _visible_nodes as _visible_nodes,
)

from .lifecycle_models import (
    DecisionLifecycleGraph as DecisionLifecycleGraph,
    EventSpan as EventSpan,
    LiveSubgraph as LiveSubgraph,
    LivenessRoots as LivenessRoots,
)
