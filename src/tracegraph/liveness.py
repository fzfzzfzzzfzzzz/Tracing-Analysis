"""Deterministic lifecycle liveness analysis for Phase 5.

The immutable :class:`TraceGraph` remains the audit/event layer.  This module
builds a prefix-only lifecycle view, derives explicit decision roots, computes
their dependency closure, and groups raw provider messages into protocol-safe
spans.  It does not render or summarize provider context.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .decision_query import DecisionQuery
from .decision_state import (
    DecisionStateGraph,
    StateAtom,
    StateAtomType,
    StateEdgeType,
    stable_digest,
)
from .graph import TraceGraph
from .policy_rules import PolicyRule
from .schema import EdgeType, Node, NodeType, SemanticOutcome
from .state_reducer import reduce_event_graph


ArchiveReader = Callable[[str], Any]

_TOOL_NODE_TYPES = {
    NodeType.TOOL_CALL,
    NodeType.MCP_CALL,
    NodeType.OBSERVATION,
    NodeType.ERROR,
}
_CALL_NODE_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}
_RESULT_EDGE_TYPES = {EdgeType.PRODUCES, EdgeType.FAILED_WITH}
_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}
_ROOT_TYPES = {
    StateAtomType.ACTIVE_GOAL,
    StateAtomType.OPEN_SUBGOAL,
    StateAtomType.PENDING_OPERATION,
    StateAtomType.UNKNOWN_SLOT,
    StateAtomType.CONFIRMATION_REQUIREMENT,
    StateAtomType.APPLICABLE_POLICY_RULE,
    StateAtomType.GLOBAL_POLICY_RULE,
    StateAtomType.CRITICAL_EVIDENCE,
    StateAtomType.CONFLICTING_FACT,
    StateAtomType.NEGATIVE_GUARD,
    StateAtomType.SIDE_EFFECT_RECEIPT,
}
_KNOWN_TERMINAL_ATOM_STATUSES = {
    "superseded",
    "resolved",
    "invalidated",
    "consumed",
}
_REVERSE_DEPENDENCY_EDGES = {
    StateEdgeType.REQUIRED_FOR,
    StateEdgeType.FILLS,
    StateEdgeType.SUPPORTS,
    StateEdgeType.BLOCKS,
    StateEdgeType.SATISFIES,
    StateEdgeType.VIOLATES,
    StateEdgeType.DERIVED_FROM,
    StateEdgeType.RESOLVES,
    StateEdgeType.ALTERNATIVE_FOR,
}


@dataclass(frozen=True, slots=True)
class EventLifecycleRecord:
    event_id: str
    status: str
    terminal: bool
    reason: str
    confidence: float
    source_atom_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("lifecycle confidence must be between zero and one")
        object.__setattr__(self, "source_atom_ids", tuple(sorted(set(self.source_atom_ids))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "status": self.status,
            "terminal": self.terminal,
            "reason": self.reason,
            "confidence": self.confidence,
            "source_atom_ids": list(self.source_atom_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventLifecycleRecord":
        return cls(
            event_id=str(data["event_id"]),
            status=str(data["status"]),
            terminal=bool(data["terminal"]),
            reason=str(data["reason"]),
            confidence=float(data["confidence"]),
            source_atom_ids=tuple(map(str, data.get("source_atom_ids", ()))),
        )


@dataclass(frozen=True, slots=True)
class DecisionLifecycleGraph:
    """Prefix-only decision state plus deterministic event lifecycle records."""

    decision_state: DecisionStateGraph
    event_graph_hash: str
    event_records: tuple[EventLifecycleRecord, ...]
    lifecycle_version: str = "decision_lifecycle_graph_v1"

    def __post_init__(self) -> None:
        records = tuple(sorted(self.event_records, key=lambda item: item.event_id))
        ids = [item.event_id for item in records]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate event lifecycle record")
        object.__setattr__(self, "event_records", records)

    @property
    def cutoff_step(self) -> int:
        return self.decision_state.cutoff_step

    @property
    def lifecycle_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def record_map(self) -> dict[str, EventLifecycleRecord]:
        return {item.event_id: item for item in self.event_records}

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.lifecycle_version,
            "event_graph_hash": self.event_graph_hash,
            "decision_state": self.decision_state.to_dict(),
            "event_records": [item.to_dict() for item in self.event_records],
        }
        if include_hash:
            result["lifecycle_hash"] = self.lifecycle_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionLifecycleGraph":
        value = cls(
            decision_state=DecisionStateGraph.from_dict(
                dict(data["decision_state"])
            ),
            event_graph_hash=str(data["event_graph_hash"]),
            event_records=tuple(
                EventLifecycleRecord.from_dict(item)
                for item in data.get("event_records", ())
            ),
            lifecycle_version=str(
                data.get("schema_version", "decision_lifecycle_graph_v1")
            ),
        )
        declared = data.get("lifecycle_hash")
        if declared is not None and declared != value.lifecycle_hash:
            raise ValueError("DecisionLifecycleGraph hash mismatch")
        return value


@dataclass(frozen=True, slots=True)
class LivenessRoot:
    root_id: str
    atom_id: str | None
    source_event_ids: tuple[str, ...]
    reason: str
    hard: bool

    @classmethod
    def create(
        cls,
        *,
        atom_id: str | None,
        source_event_ids: Sequence[str],
        reason: str,
        hard: bool,
    ) -> "LivenessRoot":
        sources = tuple(sorted(set(map(str, source_event_ids))))
        identity = {
            "atom_id": atom_id,
            "source_event_ids": sources,
            "reason": reason,
            "hard": hard,
        }
        return cls(
            root_id=f"root_{stable_digest(identity)[:24]}",
            atom_id=atom_id,
            source_event_ids=sources,
            reason=reason,
            hard=hard,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "atom_id": self.atom_id,
            "source_event_ids": list(self.source_event_ids),
            "reason": self.reason,
            "hard": self.hard,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LivenessRoot":
        value = cls.create(
            atom_id=str(data["atom_id"]) if data.get("atom_id") else None,
            source_event_ids=tuple(
                map(str, data.get("source_event_ids", ()))
            ),
            reason=str(data["reason"]),
            hard=bool(data["hard"]),
        )
        declared = data.get("root_id")
        if declared is not None and declared != value.root_id:
            raise ValueError("LivenessRoot id mismatch")
        return value


@dataclass(frozen=True, slots=True)
class LivenessRoots:
    lifecycle_hash: str
    query_hash: str
    roots: tuple[LivenessRoot, ...]
    uncertainty_reasons: tuple[str, ...] = ()
    roots_version: str = "liveness_roots_v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "roots", tuple(sorted(self.roots, key=lambda item: item.root_id)))
        object.__setattr__(
            self,
            "uncertainty_reasons",
            tuple(sorted(set(map(str, self.uncertainty_reasons)))),
        )

    @property
    def root_atom_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.atom_id for item in self.roots if item.atom_id}))

    @property
    def root_event_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    event_id
                    for item in self.roots
                    for event_id in item.source_event_ids
                }
            )
        )

    @property
    def roots_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.roots_version,
            "lifecycle_hash": self.lifecycle_hash,
            "query_hash": self.query_hash,
            "roots": [item.to_dict() for item in self.roots],
            "root_atom_ids": list(self.root_atom_ids),
            "root_event_ids": list(self.root_event_ids),
            "uncertainty_reasons": list(self.uncertainty_reasons),
        }
        if include_hash:
            result["roots_hash"] = self.roots_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LivenessRoots":
        value = cls(
            lifecycle_hash=str(data["lifecycle_hash"]),
            query_hash=str(data["query_hash"]),
            roots=tuple(
                LivenessRoot.from_dict(item)
                for item in data.get("roots", ())
            ),
            uncertainty_reasons=tuple(
                map(str, data.get("uncertainty_reasons", ()))
            ),
            roots_version=str(data.get("schema_version", "liveness_roots_v1")),
        )
        declared = data.get("roots_hash")
        if declared is not None and declared != value.roots_hash:
            raise ValueError("LivenessRoots hash mismatch")
        return value


@dataclass(frozen=True, slots=True)
class EventSpan:
    span_id: str
    span_type: str
    node_ids: tuple[str, ...]
    message_ordinals: tuple[int, ...]
    call_ids: tuple[str, ...]
    raw_refs: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        span_type: str,
        node_ids: Sequence[str],
        message_ordinals: Sequence[int],
        call_ids: Sequence[str] = (),
        raw_refs: Sequence[str] = (),
    ) -> "EventSpan":
        nodes = tuple(sorted(set(map(str, node_ids))))
        ordinals = tuple(sorted(set(map(int, message_ordinals))))
        calls = tuple(sorted(set(map(str, call_ids))))
        refs = tuple(sorted(set(map(str, raw_refs))))
        identity = {
            "span_type": span_type,
            "node_ids": nodes,
            "message_ordinals": ordinals,
            "call_ids": calls,
            "raw_refs": refs,
        }
        return cls(
            span_id=f"span_{stable_digest(identity)[:24]}",
            span_type=span_type,
            node_ids=nodes,
            message_ordinals=ordinals,
            call_ids=calls,
            raw_refs=refs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "span_type": self.span_type,
            "node_ids": list(self.node_ids),
            "message_ordinals": list(self.message_ordinals),
            "call_ids": list(self.call_ids),
            "raw_refs": list(self.raw_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventSpan":
        value = cls.create(
            span_type=str(data["span_type"]),
            node_ids=tuple(map(str, data.get("node_ids", ()))),
            message_ordinals=tuple(
                map(int, data.get("message_ordinals", ()))
            ),
            call_ids=tuple(map(str, data.get("call_ids", ()))),
            raw_refs=tuple(map(str, data.get("raw_refs", ()))),
        )
        declared = data.get("span_id")
        if declared is not None and declared != value.span_id:
            raise ValueError("EventSpan id mismatch")
        return value


@dataclass(frozen=True, slots=True)
class LiveSubgraph:
    lifecycle_hash: str
    roots_hash: str
    cutoff_step: int
    spans: tuple[EventSpan, ...]
    live_atom_ids: tuple[str, ...]
    live_node_ids: tuple[str, ...]
    evicted_node_ids: tuple[str, ...]
    live_span_ids: tuple[str, ...]
    evicted_span_ids: tuple[str, ...]
    root_provenance: tuple[dict[str, Any], ...]
    closure_provenance: tuple[dict[str, Any], ...]
    lifecycle_reasons: tuple[dict[str, Any], ...]
    uncertainty_records: tuple[dict[str, Any], ...] = ()
    analyzer_version: str = "live_subgraph_v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "spans", tuple(sorted(self.spans, key=lambda item: item.span_id)))
        for field in (
            "live_atom_ids",
            "live_node_ids",
            "evicted_node_ids",
            "live_span_ids",
            "evicted_span_ids",
        ):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))
        if set(self.live_node_ids).intersection(self.evicted_node_ids):
            raise ValueError("live and evicted node sets overlap")
        if set(self.live_span_ids).intersection(self.evicted_span_ids):
            raise ValueError("live and evicted span sets overlap")

    @property
    def live_subgraph_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def span_map(self) -> dict[str, EventSpan]:
        return {span.span_id: span for span in self.spans}

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.analyzer_version,
            "lifecycle_hash": self.lifecycle_hash,
            "roots_hash": self.roots_hash,
            "cutoff_step": self.cutoff_step,
            "spans": [item.to_dict() for item in self.spans],
            "live_atom_ids": list(self.live_atom_ids),
            "live_node_ids": list(self.live_node_ids),
            "evicted_node_ids": list(self.evicted_node_ids),
            "live_span_ids": list(self.live_span_ids),
            "evicted_span_ids": list(self.evicted_span_ids),
            "root_provenance": [dict(item) for item in self.root_provenance],
            "closure_provenance": [dict(item) for item in self.closure_provenance],
            "lifecycle_reasons": [dict(item) for item in self.lifecycle_reasons],
            "uncertainty_records": [dict(item) for item in self.uncertainty_records],
        }
        if include_hash:
            result["live_subgraph_hash"] = self.live_subgraph_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveSubgraph":
        value = cls(
            lifecycle_hash=str(data["lifecycle_hash"]),
            roots_hash=str(data["roots_hash"]),
            cutoff_step=int(data["cutoff_step"]),
            spans=tuple(
                EventSpan.from_dict(item) for item in data.get("spans", ())
            ),
            live_atom_ids=tuple(map(str, data.get("live_atom_ids", ()))),
            live_node_ids=tuple(map(str, data.get("live_node_ids", ()))),
            evicted_node_ids=tuple(
                map(str, data.get("evicted_node_ids", ()))
            ),
            live_span_ids=tuple(map(str, data.get("live_span_ids", ()))),
            evicted_span_ids=tuple(
                map(str, data.get("evicted_span_ids", ()))
            ),
            root_provenance=tuple(
                dict(item) for item in data.get("root_provenance", ())
            ),
            closure_provenance=tuple(
                dict(item) for item in data.get("closure_provenance", ())
            ),
            lifecycle_reasons=tuple(
                dict(item) for item in data.get("lifecycle_reasons", ())
            ),
            uncertainty_records=tuple(
                dict(item) for item in data.get("uncertainty_records", ())
            ),
            analyzer_version=str(
                data.get("schema_version", "live_subgraph_v1")
            ),
        )
        declared = data.get("live_subgraph_hash")
        if declared is not None and declared != value.live_subgraph_hash:
            raise ValueError("LiveSubgraph hash mismatch")
        return value


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
