"""Lifecycle inference and graph-constrained compression safety rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .archive import ArchiveStore
from .capture import estimate_tokens
from .graph import TraceGraph
from .schema import (
    EdgeType,
    LifecycleProfile,
    LifecycleState,
    Node,
    NodeType,
    RelevanceState,
    RetentionObligation,
    SemanticOutcome,
    StorageState,
    ValidityState,
)


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
        return node.node_type == NodeType.ERROR and not graph.resolving_edges(node.node_id)

    @staticmethod
    def _semantic_outcome(node: Node) -> SemanticOutcome | None:
        value = node.metadata.get("semantic_outcome")
        if value is None:
            return None
        try:
            return SemanticOutcome(value)
        except ValueError:
            return None

    @classmethod
    def _is_negative_result(cls, node: Node) -> bool:
        return node.node_type == NodeType.ERROR or cls._semantic_outcome(node) in {
            SemanticOutcome.NEGATIVE,
            SemanticOutcome.POLICY_DENIED,
            SemanticOutcome.TEST_FAILED,
        }

    @staticmethod
    def _supports_final(graph: TraceGraph, node_id: str, final_ids: set[str]) -> bool:
        return any(edge.target in final_ids for edge in graph.outgoing(node_id, EdgeType.SUPPORTS))

    @staticmethod
    def _relation_targets(graph: TraceGraph, node_id: str, relation: str) -> tuple[str, ...]:
        if relation == "resolution":
            edges = graph.resolving_edges(node_id)
            targets = [
                edge.target if edge.edge_type == EdgeType.RESOLVED_BY else edge.source
                for edge in edges
            ]
        elif relation == "supersession":
            edges = graph.superseding_edges(node_id)
            targets = [
                edge.target if edge.edge_type == EdgeType.SUPERSEDED_BY else edge.source
                for edge in edges
            ]
        elif relation == "summary":
            edges = graph.summarizing_edges(node_id)
            targets = [
                edge.target if edge.edge_type == EdgeType.SUMMARIZED_BY else edge.source
                for edge in edges
            ]
        else:
            raise ValueError(f"unknown relation: {relation}")
        return tuple(dict.fromkeys(targets))

    def infer_profiles(self, graph: TraceGraph) -> dict[str, LifecycleProfile]:
        """Infer independent lifecycle dimensions and their transition triggers."""

        final_ids = self.final_decision_ids(graph)
        inferred: dict[str, LifecycleProfile] = {}
        for node in graph.find_nodes():
            current = node.lifecycle_profile
            resolution_ids = self._relation_targets(graph, node.node_id, "resolution")
            supersession_ids = self._relation_targets(graph, node.node_id, "supersession")
            summary_ids = self._relation_targets(graph, node.node_id, "summary")
            supports_final = self._supports_final(graph, node.node_id, final_ids)

            relevance = current.relevance
            if node.node_type in {NodeType.TOOL_CALL, NodeType.MCP_CALL} and (
                graph.outgoing(node.node_id, EdgeType.PRODUCES)
                or graph.outgoing(node.node_id, EdgeType.FAILED_WITH)
            ):
                relevance = RelevanceState.CONSUMED
            elif node.node_type == NodeType.DECISION and not node.metadata.get("final", False):
                later_decision_exists = any(
                    other.node_type == NodeType.DECISION and other.step_id > node.step_id
                    for other in graph.nodes.values()
                )
                if graph.outgoing(node.node_id, EdgeType.LEADS_TO) or later_decision_exists:
                    relevance = RelevanceState.CONSUMED
            elif node.node_type == NodeType.OBSERVATION and graph.outgoing(
                node.node_id, EdgeType.SUPPORTS
            ):
                relevance = RelevanceState.CONSUMED
            elif not node.active:
                relevance = RelevanceState.CONSUMED
            elif self.semantic_relevance is not None and self.semantic_relevance(node, graph):
                relevance = RelevanceState.ACTIVE
            elif relevance == RelevanceState.UNCLASSIFIED:
                relevance = RelevanceState.ACTIVE

            semantic_outcome = self._semantic_outcome(node)
            validity = current.validity
            if supersession_ids:
                validity = ValidityState.SUPERSEDED
            elif self._is_negative_result(node):
                validity = (
                    ValidityState.NEGATIVE_RESOLVED
                    if resolution_ids
                    else ValidityState.NEGATIVE_UNRESOLVED
                )
            elif semantic_outcome == SemanticOutcome.POSITIVE:
                validity = ValidityState.VALID
            elif node.node_type == NodeType.SUMMARY:
                validity = ValidityState.VALID
            elif validity == ValidityState.UNKNOWN and node.node_type == NodeType.OBSERVATION:
                validity = ValidityState.VALID

            storage = current.storage
            if (summary_ids or node.lifecycle == LifecycleState.ARCHIVED) and node.raw_ref:
                storage = StorageState.ARCHIVED
            elif storage == StorageState.EVICTED and node.raw_ref:
                storage = StorageState.ARCHIVED

            obligations: list[RetentionObligation] = []
            if node.side_effect:
                obligations.append(RetentionObligation.AUDIT_REQUIRED)
            if node.node_type == NodeType.CONSTRAINT and node.active:
                obligations.append(RetentionObligation.ACTIVE_CONSTRAINT)
            if validity == ValidityState.NEGATIVE_UNRESOLVED:
                obligations.append(RetentionObligation.RETAIN_UNTIL_ACTION_COMPLETE)
            if supports_final or node.lifecycle == LifecycleState.CRITICAL_EVIDENCE:
                obligations.append(RetentionObligation.CRITICAL_EVIDENCE)

            trigger_ids = tuple(
                dict.fromkeys(
                    resolution_ids
                    + supersession_ids
                    + summary_ids
                    + tuple(
                        edge.target
                        for edge in graph.outgoing(node.node_id, EdgeType.SUPPORTS)
                        if edge.target in final_ids
                    )
                )
            )
            scope = dict(current.scope)
            if node.node_type in {NodeType.OBSERVATION, NodeType.ERROR}:
                producer_edges = graph.incoming(node.node_id, EdgeType.PRODUCES)
                producer_edges += graph.incoming(node.node_id, EdgeType.FAILED_WITH)
                if producer_edges:
                    call = graph.nodes[producer_edges[-1].source]
                    scope.update(
                        {
                            "tool_name": call.metadata.get("tool_name"),
                            "operation_key": call.metadata.get("operation_key"),
                        }
                    )
            inferred[node.node_id] = LifecycleProfile(
                relevance=relevance,
                validity=validity,
                storage=storage,
                obligations=tuple(obligations),
                scope=scope,
                confidence=1.0 if semantic_outcome is not None else 0.8,
                inferred_by="deterministic_structural_rules",
                trigger_node_ids=trigger_ids,
            )
        return inferred

    @staticmethod
    def _project_legacy(node: Node, profile: LifecycleProfile) -> LifecycleState:
        """Project a profile onto the v1 label for downstream compatibility."""

        if RetentionObligation.AUDIT_REQUIRED in profile.obligations:
            return LifecycleState.AUDIT_REQUIRED
        if (
            node.node_type == NodeType.ERROR
            and profile.validity == ValidityState.NEGATIVE_UNRESOLVED
        ):
            return LifecycleState.UNRESOLVED_FAILURE
        if node.node_type == NodeType.ERROR and profile.validity == ValidityState.NEGATIVE_RESOLVED:
            return LifecycleState.RESOLVED_FAILURE
        if RetentionObligation.CRITICAL_EVIDENCE in profile.obligations:
            return LifecycleState.CRITICAL_EVIDENCE
        if profile.validity == ValidityState.SUPERSEDED:
            return LifecycleState.SUPERSEDED
        if profile.storage == StorageState.ARCHIVED:
            return LifecycleState.ARCHIVED
        if profile.relevance == RelevanceState.CONSUMED:
            return LifecycleState.CONSUMED
        return LifecycleState.ACTIVE

    def infer(self, graph: TraceGraph) -> dict[str, LifecycleState]:
        """Infer legacy labels by projecting the factorized v2 profiles."""

        profiles = self.infer_profiles(graph)
        return {
            node_id: self._project_legacy(graph.nodes[node_id], profile)
            for node_id, profile in profiles.items()
        }

    def apply(self, graph: TraceGraph) -> dict[str, tuple[LifecycleState, LifecycleState]]:
        profiles = self.infer_profiles(graph)
        profile_audit = graph.metadata.setdefault("lifecycle_profile_transitions", [])
        for node_id, profile in profiles.items():
            previous_profile = graph.nodes[node_id].lifecycle_profile
            if previous_profile != profile:
                profile_audit.append(
                    {
                        "node_id": node_id,
                        "before": previous_profile.to_dict(),
                        "after": profile.to_dict(),
                        "trigger_node_ids": list(profile.trigger_node_ids),
                    }
                )
                graph.set_lifecycle_profile(node_id, profile)
        transitions: dict[str, tuple[LifecycleState, LifecycleState]] = {}
        for node_id, profile in profiles.items():
            state = self._project_legacy(graph.nodes[node_id], profile)
            previous = graph.nodes[node_id].lifecycle
            if previous != state:
                transitions[node_id] = (previous, state)
                graph.set_lifecycle(
                    node_id,
                    state,
                    active=(
                        profile.relevance != RelevanceState.CONSUMED
                        and profile.storage == StorageState.RAW_IN_CONTEXT
                    ),
                )
        return transitions

    def safety_decision(self, graph: TraceGraph, node_id: str) -> SafetyDecision:
        """Decide whether a node may leave active context.

        This is intentionally conservative: an item may be compressed or archived
        only if the graph proves that doing so does not remove required evidence.
        """

        node = graph.nodes[node_id]
        profile = self.infer_profiles(graph)[node_id]
        final_ids = self.final_decision_ids(graph)
        reasons: list[str] = []
        if node.node_type in {NodeType.GOAL, NodeType.SUBGOAL} and node.active:
            reasons.append("active_goal")
        if node.node_type == NodeType.CONSTRAINT and node.active:
            reasons.append("active_constraint")
        if profile.validity == ValidityState.NEGATIVE_UNRESOLVED:
            reasons.append("unresolved_negative_evidence")
        # Audit-required controls durable external storage. It does not by
        # itself require the raw write call to remain in every LLM prompt.
        if RetentionObligation.CRITICAL_EVIDENCE in profile.obligations:
            reasons.append("critical_evidence")
        if RetentionObligation.ACTIVE_CONSTRAINT in profile.obligations:
            reasons.append("active_constraint_obligation")
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
            graph.connect(source.node_id, summary_node.node_id, EdgeType.SUMMARIZED_BY)
            graph.set_lifecycle(source.node_id, LifecycleState.ARCHIVED, active=False)
        self.apply(graph)
        return summary_node
