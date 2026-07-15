"""Lifecycle inference and graph-constrained compression safety rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .archive import ArchiveStore
from .capture import estimate_tokens
from .graph import TraceGraph
from .schema import EdgeType, LifecycleState, Node, NodeType


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    removable: bool
    reasons: tuple[str, ...]


class LifecycleEngine:
    """Apply report-defined hard constraints before semantic compression."""

    def __init__(
        self,
        semantic_relevance: Callable[[Node, TraceGraph], bool] | None = None,
    ) -> None:
        self.semantic_relevance = semantic_relevance

    @staticmethod
    def final_decision_ids(graph: TraceGraph) -> set[str]:
        return {
            node.node_id
            for node in graph.find_nodes(node_types={NodeType.DECISION})
            if node.metadata.get("final", False)
        }

    @staticmethod
    def _is_unresolved_error(graph: TraceGraph, node: Node) -> bool:
        return node.node_type == NodeType.ERROR and not graph.incoming(
            node.node_id, EdgeType.RESOLVES
        )

    @staticmethod
    def _supports_final(graph: TraceGraph, node_id: str, final_ids: set[str]) -> bool:
        return any(
            edge.target in final_ids
            for edge in graph.outgoing(node_id, EdgeType.SUPPORTS)
        )

    def infer(self, graph: TraceGraph) -> dict[str, LifecycleState]:
        """Infer current lifecycle labels without deleting any graph data."""

        final_ids = self.final_decision_ids(graph)
        inferred: dict[str, LifecycleState] = {}
        for node in graph.find_nodes():
            state = node.lifecycle
            if node.side_effect:
                state = LifecycleState.AUDIT_REQUIRED
            elif self._is_unresolved_error(graph, node):
                state = LifecycleState.UNRESOLVED_FAILURE
            elif node.node_type == NodeType.ERROR and graph.incoming(
                node.node_id, EdgeType.RESOLVES
            ):
                state = LifecycleState.RESOLVED_FAILURE
            elif self._supports_final(graph, node.node_id, final_ids):
                state = LifecycleState.CRITICAL_EVIDENCE
            elif node.node_type == NodeType.OBSERVATION and graph.incoming(
                node.node_id, EdgeType.SUPERSEDES
            ):
                state = LifecycleState.SUPERSEDED
            elif graph.incoming(node.node_id, EdgeType.COMPRESSES) and node.raw_ref:
                state = LifecycleState.ARCHIVED
            elif node.node_type in {NodeType.TOOL_CALL, NodeType.MCP_CALL} and (
                graph.outgoing(node.node_id, EdgeType.PRODUCES)
                or graph.outgoing(node.node_id, EdgeType.FAILED_WITH)
            ):
                state = LifecycleState.CONSUMED
            elif node.node_type == NodeType.DECISION and not node.metadata.get("final", False):
                later_decision_exists = any(
                    other.node_type == NodeType.DECISION and other.step_id > node.step_id
                    for other in graph.nodes.values()
                )
                if graph.outgoing(node.node_id, EdgeType.LEADS_TO) or later_decision_exists:
                    state = LifecycleState.CONSUMED
            elif node.node_type == NodeType.OBSERVATION and graph.outgoing(
                node.node_id, EdgeType.SUPPORTS
            ):
                state = LifecycleState.CONSUMED
            elif self.semantic_relevance is not None and self.semantic_relevance(node, graph):
                state = LifecycleState.ACTIVE
            elif node.lifecycle == LifecycleState.CREATED:
                state = LifecycleState.ACTIVE
            inferred[node.node_id] = state
        return inferred

    def apply(self, graph: TraceGraph) -> dict[str, tuple[LifecycleState, LifecycleState]]:
        transitions: dict[str, tuple[LifecycleState, LifecycleState]] = {}
        for node_id, state in self.infer(graph).items():
            previous = graph.nodes[node_id].lifecycle
            if previous != state:
                transitions[node_id] = (previous, state)
                graph.set_lifecycle(
                    node_id,
                    state,
                    active=state
                    not in {
                        LifecycleState.ARCHIVED,
                        LifecycleState.CONSUMED,
                        LifecycleState.RESOLVED_FAILURE,
                        LifecycleState.SUPERSEDED,
                    },
                )
        return transitions

    def safety_decision(self, graph: TraceGraph, node_id: str) -> SafetyDecision:
        """Decide whether a node may leave active context.

        This is intentionally conservative: an item may be compressed or archived
        only if the graph proves that doing so does not remove required evidence.
        """

        node = graph.nodes[node_id]
        final_ids = self.final_decision_ids(graph)
        reasons: list[str] = []
        if node.node_type in {NodeType.GOAL, NodeType.SUBGOAL} and node.active:
            reasons.append("active_goal")
        if node.node_type == NodeType.CONSTRAINT and node.active:
            reasons.append("active_constraint")
        if self._is_unresolved_error(graph, node):
            reasons.append("unresolved_failure")
        if node.side_effect:
            reasons.append("audit_required_side_effect")
        if node.lifecycle == LifecycleState.CRITICAL_EVIDENCE:
            reasons.append("critical_evidence")
        if self._supports_final(graph, node_id, final_ids):
            reasons.append("supports_final_decision")
        for final_id in final_ids:
            supporting = [
                edge.source
                for edge in graph.incoming(final_id, EdgeType.SUPPORTS)
                if graph.nodes[edge.source].active
            ]
            if supporting == [node_id]:
                reasons.append("unique_final_evidence")
        if reasons:
            return SafetyDecision(False, tuple(dict.fromkeys(reasons)))
        if node.raw_ref is None and node.node_type in {
            NodeType.OBSERVATION,
            NodeType.ERROR,
            NodeType.TOOL_CALL,
            NodeType.MCP_CALL,
        }:
            return SafetyDecision(False, ("missing_recoverable_raw_ref",))
        return SafetyDecision(True, ("safe_to_remove_from_active_context",))

    def compress_nodes(
        self,
        graph: TraceGraph,
        archive: ArchiveStore,
        node_ids: Iterable[str],
        *,
        summary: str,
        step_id: int,
    ) -> Node:
        """Create a recoverable summary and archive safe source nodes."""

        sources = [graph.nodes[node_id] for node_id in node_ids]
        if not sources:
            raise ValueError("at least one source node is required")
        unsafe = {
            node.node_id: self.safety_decision(graph, node.node_id).reasons
            for node in sources
            if not self.safety_decision(graph, node.node_id).removable
        }
        if unsafe:
            raise ValueError(f"unsafe compression request: {unsafe}")
        missing = [node.node_id for node in sources if not node.raw_ref]
        if missing:
            raise ValueError(f"source nodes are not recoverable: {missing}")
        reference = archive.put(
            {
                "summary": summary,
                "source_nodes": [node.to_dict() for node in sources],
                "source_refs": [node.raw_ref for node in sources],
            },
            metadata={"kind": "trace_summary", "session_id": graph.session_id},
        )
        summary_node = graph.create_node(
            NodeType.SUMMARY,
            summary,
            step_id,
            lifecycle=LifecycleState.ACTIVE,
            token_count=estimate_tokens(summary),
            raw_ref=reference,
            metadata={"source_node_ids": [node.node_id for node in sources]},
        )
        graph.create_node(
            NodeType.ARCHIVE_HANDLE,
            reference,
            step_id,
            lifecycle=LifecycleState.ACTIVE,
            token_count=estimate_tokens(reference),
            raw_ref=reference,
            metadata={"for_summary": summary_node.node_id},
        )
        for source in sources:
            graph.connect(summary_node.node_id, source.node_id, EdgeType.COMPRESSES)
            graph.set_lifecycle(source.node_id, LifecycleState.ARCHIVED, active=False)
        return summary_node
