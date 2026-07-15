"""Primary and structural-reliability metrics derived from report hypotheses."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .archive import ArchiveStore
from .context import ContextView
from .graph import TraceGraph
from .lifecycle import LifecycleEngine
from .schema import EdgeType, LifecycleState, NodeType


def _rate(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


@dataclass(slots=True)
class EvaluationMetrics:
    task_success: float | None
    policy_violation: float | None
    input_tokens: int
    original_tokens: int
    compression_ratio: float
    evidence_retention: float
    unresolved_failure_retention: float
    constraint_retention: float
    evidence_path_preservation: float
    archive_recoverability: float
    repeated_failed_tool_calls: int
    unsafe_removal_count: int
    manager_overhead_ms: float | None = None
    graph_maintenance_overhead_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_view(
    graph: TraceGraph,
    view: ContextView,
    *,
    archive: ArchiveStore | None = None,
    task_success: float | None = None,
    policy_violation: float | None = None,
    manager_overhead_ms: float | None = None,
    graph_maintenance_overhead_ms: float | None = None,
) -> EvaluationMetrics:
    covered = view.covered_node_ids
    final_ids = LifecycleEngine.final_decision_ids(graph)
    evidence_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.lifecycle == LifecycleState.CRITICAL_EVIDENCE
        or any(
            edge.target in final_ids
            for edge in graph.outgoing(node.node_id, EdgeType.SUPPORTS)
        )
    }
    unresolved_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.node_type == NodeType.ERROR
        and not graph.incoming(node.node_id, EdgeType.RESOLVES)
    }
    constraint_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.node_type == NodeType.CONSTRAINT and node.active
    }
    path_hits = 0
    for decision_id in final_ids:
        supporters = {
            edge.source for edge in graph.incoming(decision_id, EdgeType.SUPPORTS)
        }
        if supporters & covered:
            path_hits += 1

    recoverable_nodes = [
        node
        for node in graph.nodes.values()
        if node.raw_ref is not None
        and node.node_type
        in {NodeType.TOOL_CALL, NodeType.MCP_CALL, NodeType.OBSERVATION, NodeType.ERROR}
    ]
    if archive is None:
        recovered = sum(1 for node in recoverable_nodes if node.raw_ref)
    else:
        recovered = sum(1 for node in recoverable_nodes if archive.exists(node.raw_ref or ""))

    retry_edges = [edge for edge in graph.edges.values() if edge.edge_type == EdgeType.RETRIES]
    # This is the observed repeated-failure count in the source trajectory.
    # Counterfactual repetitions caused by a particular context view require a
    # live run and must not be fabricated from offline omission.
    repeated_failed = len(retry_edges)
    engine = LifecycleEngine()
    unsafe = sum(
        1
        for node_id in graph.nodes
        if node_id not in covered and not engine.safety_decision(graph, node_id).removable
    )
    return EvaluationMetrics(
        task_success=task_success,
        policy_violation=policy_violation,
        input_tokens=view.selected_tokens,
        original_tokens=view.original_tokens,
        compression_ratio=view.compression_ratio,
        evidence_retention=_rate(len(evidence_ids & covered), len(evidence_ids)),
        unresolved_failure_retention=_rate(len(unresolved_ids & covered), len(unresolved_ids)),
        constraint_retention=_rate(len(constraint_ids & covered), len(constraint_ids)),
        evidence_path_preservation=_rate(path_hits, len(final_ids)),
        archive_recoverability=_rate(recovered, len(recoverable_nodes)),
        repeated_failed_tool_calls=repeated_failed,
        unsafe_removal_count=unsafe,
        manager_overhead_ms=manager_overhead_ms,
        graph_maintenance_overhead_ms=graph_maintenance_overhead_ms,
    )
