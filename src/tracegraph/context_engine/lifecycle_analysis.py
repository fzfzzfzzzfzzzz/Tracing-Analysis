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
    _CALL_NODE_TYPES as _CALL_NODE_TYPES,
    _KNOWN_TERMINAL_ATOM_STATUSES as _KNOWN_TERMINAL_ATOM_STATUSES,
    _NEGATIVE_OUTCOMES as _NEGATIVE_OUTCOMES,
    _RESULT_EDGE_TYPES as _RESULT_EDGE_TYPES,
    _REVERSE_DEPENDENCY_EDGES as _REVERSE_DEPENDENCY_EDGES,
    _ROOT_TYPES as _ROOT_TYPES,
)



def _visible_nodes(event_graph: TraceGraph, cutoff_step: int) -> tuple[Node, ...]:
    return tuple(
        sorted(
            (
                node
                for node in event_graph.nodes.values()
                if node.step_id <= cutoff_step
            ),
            key=lambda item: (item.step_id, item.node_id),
        )
    )


def _prefix_event_hash(event_graph: TraceGraph, cutoff_step: int) -> str:
    nodes = _visible_nodes(event_graph, cutoff_step)
    visible = {node.node_id for node in nodes}
    edges = sorted(
        (
            {
                "edge_id": edge.edge_id,
                "source": edge.source,
                "target": edge.target,
                "edge_type": edge.edge_type.value,
                "confidence": edge.confidence,
                "metadata": edge.metadata,
            }
            for edge in event_graph.edges.values()
            if edge.source in visible and edge.target in visible
        ),
        key=lambda item: str(item["edge_id"]),
    )
    neutral_nodes = [
        {
            "node_id": node.node_id,
            "node_type": node.node_type.value,
            "content": node.content,
            "step_id": node.step_id,
            "token_count": node.token_count,
            "raw_ref": node.raw_ref,
            "side_effect": node.side_effect,
            "metadata": node.metadata,
        }
        for node in nodes
    ]
    return stable_digest(
        {
            "schema_version": event_graph.schema_version,
            "session_id": event_graph.session_id,
            "cutoff_step": cutoff_step,
            "nodes": neutral_nodes,
            "edges": edges,
        }
    )


def _visible_relation_targets(
    event_graph: TraceGraph,
    event_id: str,
    cutoff_step: int,
    *,
    canonical: EdgeType,
    legacy: EdgeType,
) -> tuple[str, ...]:
    targets = [
        edge.target
        for edge in event_graph.outgoing(event_id, canonical)
        if event_graph.nodes[edge.target].step_id <= cutoff_step
        and edge.confidence == 1.0
    ]
    targets.extend(
        edge.source
        for edge in event_graph.incoming(event_id, legacy)
        if event_graph.nodes[edge.source].step_id <= cutoff_step
        and edge.confidence == 1.0
    )
    return tuple(sorted(set(targets)))


def _visible_consumers(
    event_graph: TraceGraph,
    event_id: str,
    cutoff_step: int,
) -> tuple[str, ...]:
    consumers = [
        edge.target
        for edge in event_graph.outgoing(event_id, EdgeType.PROVIDES_INPUT)
        if event_graph.nodes[edge.target].step_id <= cutoff_step
        and edge.confidence == 1.0
    ]
    consumers.extend(
        edge.source
        for edge in event_graph.incoming(event_id, EdgeType.USES)
        if event_graph.nodes[edge.source].step_id <= cutoff_step
        and edge.confidence == 1.0
    )
    return tuple(sorted(set(consumers)))


def _result_nodes(
    event_graph: TraceGraph,
    call: Node,
    visible: set[str],
) -> tuple[Node, ...]:
    return tuple(
        sorted(
            (
                event_graph.nodes[edge.target]
                for edge in event_graph.outgoing(call.node_id)
                if edge.edge_type in _RESULT_EDGE_TYPES and edge.target in visible
            ),
            key=lambda item: (item.step_id, item.node_id),
        )
    )


def _is_negative_result(node: Node) -> bool:
    return bool(
        node.node_type == NodeType.ERROR
        or node.metadata.get("semantic_outcome") in _NEGATIVE_OUTCOMES
    )


def _event_lifecycle_records(
    event_graph: TraceGraph,
    decision_state: DecisionStateGraph,
) -> tuple[EventLifecycleRecord, ...]:
    nodes = _visible_nodes(event_graph, decision_state.cutoff_step)
    visible = {node.node_id for node in nodes}
    atoms_by_event: dict[str, list[StateAtom]] = defaultdict(list)
    for atom in decision_state.atoms:
        for event_id in atom.source_event_ids:
            if event_id in visible:
                atoms_by_event[event_id].append(atom)
    invalidators_by_event: dict[str, set[str]] = defaultdict(set)
    for candidate in nodes:
        raw_targets = candidate.metadata.get("invalidates_event_ids") or ()
        if isinstance(raw_targets, str):
            raw_targets = (raw_targets,)
        verifier = str(candidate.metadata.get("invalidation_verifier") or "")
        confidence = float(
            candidate.metadata.get("invalidation_confidence", 0.0)
        )
        if not verifier.startswith("deterministic_") or confidence != 1.0:
            continue
        for target in map(str, raw_targets):
            if target in visible:
                invalidators_by_event[target].add(candidate.node_id)

    preliminary: dict[str, EventLifecycleRecord] = {}
    for node in nodes:
        atoms = atoms_by_event.get(node.node_id, [])
        atom_ids = tuple(atom.atom_id for atom in atoms)
        superseders = _visible_relation_targets(
            event_graph,
            node.node_id,
            decision_state.cutoff_step,
            canonical=EdgeType.SUPERSEDED_BY,
            legacy=EdgeType.SUPERSEDES,
        )
        resolvers = _visible_relation_targets(
            event_graph,
            node.node_id,
            decision_state.cutoff_step,
            canonical=EdgeType.RESOLVED_BY,
            legacy=EdgeType.RESOLVES,
        )
        invalidators = tuple(sorted(invalidators_by_event[node.node_id]))
        consumers = _visible_consumers(
            event_graph,
            node.node_id,
            decision_state.cutoff_step,
        )
        superseded_atom = any(atom.status == "superseded" for atom in atoms)
        uncertain_atom = any(not atom.verified or atom.confidence < 1.0 for atom in atoms)

        if node.side_effect:
            status, terminal, reason = (
                "audit_required",
                False,
                "side_effect_requires_live_receipt",
            )
        elif uncertain_atom:
            status, terminal, reason = (
                "uncertain",
                False,
                "provisional_or_low_confidence_state",
            )
        elif superseders or superseded_atom:
            status, terminal, reason = (
                "superseded",
                True,
                "explicit_supersession_within_cutoff",
            )
        elif resolvers:
            status, terminal, reason = (
                "resolved",
                True,
                "explicit_resolution_within_cutoff",
            )
        elif invalidators:
            status, terminal, reason = (
                "invalidated",
                True,
                "deterministic_invalidation_within_cutoff",
            )
        elif consumers:
            status, terminal, reason = (
                "consumed",
                True,
                "explicit_consumption_within_cutoff",
            )
        elif node.node_type in _CALL_NODE_TYPES:
            results = _result_nodes(event_graph, node, visible)
            if not results:
                status, terminal, reason = (
                    "pending",
                    False,
                    "tool_call_has_no_result_within_cutoff",
                )
            elif any(_is_negative_result(result) for result in results):
                status, terminal, reason = (
                    "unresolved_failure",
                    False,
                    "negative_result_has_no_explicit_resolution",
                )
            else:
                status, terminal, reason = (
                    "completed_uncertain",
                    False,
                    "completion_alone_does_not_prove_dead",
                )
        elif node.node_type in {NodeType.OBSERVATION, NodeType.ERROR}:
            if _is_negative_result(node):
                status, terminal, reason = (
                    "unresolved_failure",
                    False,
                    "negative_result_has_no_explicit_resolution",
                )
            else:
                status, terminal, reason = (
                    "current_evidence",
                    False,
                    "positive_result_has_no_explicit_terminal_relation",
                )
        else:
            status, terminal, reason = (
                "active",
                False,
                "non_tool_context_is_not_prune_eligible",
            )
        preliminary[node.node_id] = EventLifecycleRecord(
            event_id=node.node_id,
            status=status,
            terminal=terminal,
            reason=reason,
            confidence=1.0,
            source_atom_ids=atom_ids,
        )

    # A producing call shares the result lifecycle. This makes a full call/result
    # span evictable only when the explicit terminal relation covers the result.
    for call in (node for node in nodes if node.node_type in _CALL_NODE_TYPES):
        results = _result_nodes(event_graph, call, visible)
        if not results or call.side_effect:
            continue
        result_records = [preliminary[result.node_id] for result in results]
        if result_records and all(record.terminal for record in result_records):
            statuses = {record.status for record in result_records}
            status = next(iter(statuses)) if len(statuses) == 1 else "terminal_result_set"
            preliminary[call.node_id] = EventLifecycleRecord(
                event_id=call.node_id,
                status=status,
                terminal=True,
                reason="producer_inherits_explicit_terminal_result",
                confidence=min(record.confidence for record in result_records),
                source_atom_ids=preliminary[call.node_id].source_atom_ids,
            )
    return tuple(preliminary.values())


def build_state(
    event_graph: TraceGraph,
    cutoff: int | None = None,
    *,
    tool_schemas: Sequence[Mapping[str, Any]] = (),
    policy: Sequence[PolicyRule | Mapping[str, Any] | str] = (),
) -> DecisionLifecycleGraph:
    """Build prefix-only decision/lifecycle state without reading future nodes."""

    decision_state = reduce_event_graph(
        event_graph,
        cutoff,
        tool_schemas=tool_schemas,
        policy_rules=policy,
    )
    return DecisionLifecycleGraph(
        decision_state=decision_state,
        event_graph_hash=_prefix_event_hash(event_graph, decision_state.cutoff_step),
        event_records=_event_lifecycle_records(event_graph, decision_state),
    )


def derive_roots(
    state: DecisionLifecycleGraph,
    query: DecisionQuery,
    tool_schemas: Sequence[Mapping[str, Any]] = (),
    policy: Sequence[PolicyRule | Mapping[str, Any] | str] = (),
) -> LivenessRoots:
    """Derive conservative roots from explicit query and safety obligations."""

    del tool_schemas, policy  # Their frozen effects are already present in state/query.
    roots: dict[str, LivenessRoot] = {}
    uncertainty = list(query.uncertainty_reasons)

    def add_atom(atom: StateAtom, reason: str, *, hard: bool | None = None) -> None:
        root = LivenessRoot.create(
            atom_id=atom.atom_id,
            source_event_ids=atom.source_event_ids,
            reason=reason,
            hard=atom.hard if hard is None else hard,
        )
        roots[root.root_id] = root

    atom_map = state.decision_state.atom_map()
    lifecycle_records = state.record_map()
    explicit_atom_ids = {
        item
        for item in (
            query.goal_id,
            query.subgoal_id,
            *query.pending_confirmation,
            *query.referenced_atom_ids,
        )
        if item
    }
    for atom in state.decision_state.atoms:
        source_records = [
            lifecycle_records[event_id]
            for event_id in atom.source_event_ids
            if event_id in lifecycle_records
        ]
        explicitly_terminal = bool(
            source_records and all(record.terminal for record in source_records)
        )
        if atom.atom_id in explicit_atom_ids:
            add_atom(atom, "explicit_decision_query_reference", hard=True)
        if atom.hard and not explicitly_terminal:
            add_atom(atom, "state_hard_obligation", hard=True)
        if atom.atom_type in _ROOT_TYPES and not explicitly_terminal:
            add_atom(atom, f"root_type:{atom.atom_type.value}")
        if atom.status in {"pending", "failed", "conflicting"} and not explicitly_terminal:
            add_atom(atom, f"nonterminal_status:{atom.status}")
        if not atom.verified or atom.confidence < 1.0:
            add_atom(atom, "uncertainty_defaults_to_live", hard=True)
        if (
            atom.atom_type == StateAtomType.SLOT_VALUE
            and not explicitly_terminal
            and isinstance(atom.value, Mapping)
            and (
                str(atom.value.get("slot") or "") in query.required_slots
                or str(atom.value.get("tool_name") or "") in query.candidate_tools
            )
        ):
            add_atom(atom, "current_candidate_tool_slot")
        if atom.atom_id in query.known_entities and not explicitly_terminal:
            add_atom(atom, "current_known_entity")
        if (
            atom.status not in _KNOWN_TERMINAL_ATOM_STATUSES
            and atom.status not in {"current", "pending", "failed", "conflicting", "completed"}
        ):
            uncertainty.append(f"unknown_atom_status:{atom.atom_id}:{atom.status}")
            add_atom(atom, "unknown_lifecycle_defaults_to_live", hard=True)

    for atom_id in query.referenced_atom_ids:
        if atom_id not in atom_map:
            uncertainty.append(f"missing_referenced_atom:{atom_id}")
    for event_id in query.referenced_event_ids:
        if event_id not in lifecycle_records:
            uncertainty.append(f"missing_referenced_event:{event_id}")
            continue
        root = LivenessRoot.create(
            atom_id=None,
            source_event_ids=(event_id,),
            reason="explicit_historical_event_reference",
            hard=True,
        )
        roots[root.root_id] = root

    return LivenessRoots(
        lifecycle_hash=state.lifecycle_hash,
        query_hash=query.query_hash,
        roots=tuple(roots.values()),
        uncertainty_reasons=tuple(uncertainty),
    )


def _state_closure(
    state: DecisionLifecycleGraph,
    roots: LivenessRoots,
) -> tuple[set[str], list[dict[str, Any]]]:
    atom_map = state.decision_state.atom_map()
    live = {atom_id for atom_id in roots.root_atom_ids if atom_id in atom_map}
    queue = deque(sorted(live))
    provenance: list[dict[str, Any]] = []

    reverse: dict[str, list[tuple[str, Any, str]]] = defaultdict(list)
    for edge in state.decision_state.edges:
        if edge.edge_type in _REVERSE_DEPENDENCY_EDGES:
            reverse[edge.target].append((edge.source, edge, "dependency_reverse"))
        elif edge.edge_type == StateEdgeType.CONFLICTS_WITH:
            reverse[edge.target].append((edge.source, edge, "conflict_peer"))
            reverse[edge.source].append((edge.target, edge, "conflict_peer"))
        elif edge.edge_type == StateEdgeType.SUPERSEDES:
            # If an old atom is explicitly referenced, retain the current
            # superseder too. A live superseder does not reactivate old evidence.
            reverse[edge.target].append((edge.source, edge, "superseder_for_referenced_old"))

    while queue:
        current = queue.popleft()
        for dependency, edge, direction in sorted(
            reverse.get(current, ()),
            key=lambda item: (item[0], item[1].edge_id),
        ):
            if dependency in live:
                continue
            live.add(dependency)
            queue.append(dependency)
            provenance.append(
                {
                    "edge_id": edge.edge_id,
                    "edge_type": edge.edge_type.value,
                    "from_atom_id": current,
                    "added_atom_id": dependency,
                    "direction": direction,
                    "source_event_ids": list(edge.source_event_ids),
                }
            )
    return live, provenance


# Imported after definitions so mutually-referential helpers initialize safely.
from .lifecycle_models import (
    DecisionLifecycleGraph as DecisionLifecycleGraph,
    EventLifecycleRecord as EventLifecycleRecord,
    LivenessRoot as LivenessRoot,
    LivenessRoots as LivenessRoots,
)
