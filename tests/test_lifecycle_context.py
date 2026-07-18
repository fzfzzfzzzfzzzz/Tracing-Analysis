from __future__ import annotations

import tempfile
import unittest

from tracegraph import ArchiveStore, EdgeType, LifecycleState, NodeType, TraceGraph
from tracegraph.context import (
    GraphLifecycleManager,
    LastKManager,
    SummaryOnlyManager,
    build_context_managers,
)
from tracegraph.lifecycle import LifecycleEngine
from tracegraph.metrics import evaluate_view


def build_research_graph(store: ArchiveStore) -> TraceGraph:
    graph = TraceGraph("research_fixture")
    goal = graph.create_node(NodeType.GOAL, "complete order safely", 0, token_count=5)
    constraint = graph.create_node(
        NodeType.CONSTRAINT,
        "obtain confirmation before write",
        0,
        lifecycle=LifecycleState.ACTIVE,
        token_count=7,
    )
    call1 = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool": "update"},
        1,
        token_count=5,
        raw_ref=store.put({"call": 1}),
    )
    error = graph.create_node(
        NodeType.ERROR,
        {"error": "confirmation missing"},
        1,
        token_count=10,
        raw_ref=store.put({"error": "confirmation missing"}),
    )
    graph.connect(call1.node_id, error.node_id, EdgeType.FAILED_WITH)
    graph.connect(constraint.node_id, call1.node_id, EdgeType.BLOCKS)

    call2 = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool": "read"},
        2,
        token_count=4,
        raw_ref=store.put({"call": 2}),
    )
    evidence = graph.create_node(
        NodeType.OBSERVATION,
        {"confirmed": True},
        2,
        token_count=8,
        raw_ref=store.put({"confirmed": True}),
    )
    graph.connect(call2.node_id, evidence.node_id, EdgeType.PRODUCES)
    decision = graph.create_node(
        NodeType.DECISION,
        "perform update",
        3,
        token_count=5,
        metadata={"final": True},
    )
    graph.connect(evidence.node_id, decision.node_id, EdgeType.SUPPORTS)
    graph.connect(decision.node_id, call1.node_id, EdgeType.LEADS_TO)
    self_noise = graph.create_node(
        NodeType.OBSERVATION,
        "old catalogue " * 100,
        1,
        lifecycle=LifecycleState.CONSUMED,
        token_count=300,
        raw_ref=store.put("old catalogue " * 100),
        active=False,
    )
    graph.metadata["noise_node"] = self_noise.node_id
    graph.metadata["goal_node"] = goal.node_id
    graph.metadata["error_node"] = error.node_id
    graph.metadata["evidence_node"] = evidence.node_id
    graph.metadata["constraint_node"] = constraint.node_id
    return graph


class LifecycleTests(unittest.TestCase):
    def test_hard_constraints_protect_required_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = build_research_graph(ArchiveStore(directory))
            engine = LifecycleEngine()
            transitions = engine.apply(graph)
            self.assertIn(graph.metadata["error_node"], transitions)
            self.assertEqual(
                graph.nodes[graph.metadata["error_node"]].lifecycle,
                LifecycleState.UNRESOLVED_FAILURE,
            )
            self.assertFalse(
                engine.safety_decision(graph, graph.metadata["error_node"]).removable
            )
            self.assertFalse(
                engine.safety_decision(graph, graph.metadata["evidence_node"]).removable
            )
            self.assertTrue(
                engine.safety_decision(graph, graph.metadata["noise_node"]).removable
            )

    def test_resolved_failure_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = build_research_graph(ArchiveStore(directory))
            resolver = graph.create_node(
                NodeType.OBSERVATION,
                "confirmation obtained",
                4,
                raw_ref=ArchiveStore(directory).put("confirmation obtained"),
            )
            graph.connect(resolver.node_id, graph.metadata["error_node"], EdgeType.RESOLVES)
            LifecycleEngine().apply(graph)
            self.assertEqual(
                graph.nodes[graph.metadata["error_node"]].lifecycle,
                LifecycleState.RESOLVED_FAILURE,
            )

    def test_safe_compression_creates_summary_and_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            graph = build_research_graph(store)
            engine = LifecycleEngine()
            summary = engine.compress_nodes(
                graph,
                store,
                [graph.metadata["noise_node"]],
                summary="old catalogue archived",
                step_id=5,
            )
            self.assertEqual(summary.node_type, NodeType.SUMMARY)
            self.assertFalse(graph.nodes[graph.metadata["noise_node"]].active)
            self.assertTrue(store.exists(summary.raw_ref or ""))
            self.assertEqual(len(graph.find_nodes(node_types={NodeType.ARCHIVE_HANDLE})), 1)


class ContextManagerTests(unittest.TestCase):
    def test_registry_contains_all_report_baselines_and_ablations(self) -> None:
        managers = build_context_managers()
        self.assertEqual(
            set(managers),
            {
                "full_trajectory",
                "last_k",
                "token_length_pruning",
                "summary_only",
                "llm_only_pruning",
                "agentdiet_style",
                "acon_style",
                "ours_without_graph_edges",
                "ours_without_lifecycle_states",
                "ours_without_failure_retention",
                "ours_without_constraint_retention",
                "raw_hard_failure_retention",
                "full_ours",
            },
        )

    def test_full_ours_retains_hard_constraints_under_budget_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            graph = build_research_graph(store)
            view = GraphLifecycleManager().select(graph, budget=50)
            covered = view.covered_node_ids
            self.assertIn(graph.metadata["error_node"], covered)
            self.assertIn(graph.metadata["evidence_node"], covered)
            self.assertIn(graph.metadata["constraint_node"], covered)
            self.assertNotIn(graph.metadata["noise_node"], covered)
            metrics = evaluate_view(graph, view, archive=store)
            self.assertEqual(metrics.evidence_retention, 1.0)
            self.assertEqual(metrics.unresolved_failure_retention, 1.0)
            self.assertEqual(metrics.constraint_retention, 1.0)
            self.assertEqual(metrics.unsafe_removal_count, 0)
            self.assertGreater(metrics.compression_ratio, 0.5)

    def test_last_k_can_drop_early_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = build_research_graph(ArchiveStore(directory))
            view = LastKManager(k=2).select(graph)
            metrics = evaluate_view(graph, view)
            self.assertEqual(metrics.constraint_retention, 0.0)
            self.assertGreater(metrics.unsafe_removal_count, 0)

    def test_unverified_summary_does_not_claim_evidence_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = build_research_graph(ArchiveStore(directory))
            LifecycleEngine().apply(graph)
            view = SummaryOnlyManager().select(graph)
            metrics = evaluate_view(graph, view)
            self.assertEqual(metrics.evidence_retention, 0.0)


if __name__ == "__main__":
    unittest.main()
