from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracegraph import (
    ArchiveStore,
    EdgeType,
    LifecycleState,
    NodeType,
    ToolExecutor,
    ToolStatus,
    TraceGraph,
)
from tracegraph.graph import GraphValidationError


class ArchiveStoreTests(unittest.TestCase):
    def test_round_trip_and_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            first = store.put({"value": "证据", "count": 2})
            second = store.put({"count": 2, "value": "证据"})
            self.assertEqual(first, second)
            self.assertEqual(store.get(first), {"value": "证据", "count": 2})
            self.assertEqual(store.verify_all(), [])


class TraceGraphTests(unittest.TestCase):
    def test_typed_edges_and_persistence(self) -> None:
        graph = TraceGraph("session_test")
        call = graph.create_node(NodeType.TOOL_CALL, {"tool": "lookup"}, 1)
        observation = graph.create_node(NodeType.OBSERVATION, {"ok": True}, 1)
        graph.connect(call.node_id, observation.node_id, EdgeType.PRODUCES)

        with self.assertRaises(GraphValidationError):
            graph.connect(observation.node_id, call.node_id, EdgeType.PRODUCES)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            graph.save(path)
            restored = TraceGraph.load(path)
        self.assertEqual(restored.to_dict(), graph.to_dict())
        self.assertEqual(restored.validate(), [])

    def test_path_query_can_exclude_node(self) -> None:
        graph = TraceGraph()
        observation = graph.create_node(NodeType.OBSERVATION, "fact", 1)
        summary = graph.create_node(NodeType.SUMMARY, "summary", 2)
        decision = graph.create_node(NodeType.DECISION, "decide", 3)
        graph.connect(summary.node_id, observation.node_id, EdgeType.COMPRESSES)
        graph.connect(summary.node_id, decision.node_id, EdgeType.SUPPORTS)
        self.assertTrue(graph.has_path(summary.node_id, decision.node_id))
        self.assertFalse(
            graph.has_path(summary.node_id, decision.node_id, excluded_nodes={decision.node_id})
        )


class ToolExecutorTests(unittest.TestCase):
    def test_success_and_failure_are_archived(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph("session_capture")
            store = ArchiveStore(directory)
            executor = ToolExecutor(graph, store)

            self.assertEqual(executor.execute(lambda value: value + 1, 2, step_id=1), 3)

            def fail(value: int) -> int:
                raise RuntimeError(f"bad value {value}")

            with self.assertRaisesRegex(RuntimeError, "bad value"):
                executor.execute(fail, 7, step_id=2)

            observations = graph.find_nodes(node_types={NodeType.OBSERVATION})
            errors = graph.find_nodes(node_types={NodeType.ERROR})
            self.assertEqual(len(observations), 1)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].lifecycle, LifecycleState.UNRESOLVED_FAILURE)
            self.assertTrue(store.exists(observations[0].raw_ref or ""))
            self.assertTrue(store.exists(errors[0].raw_ref or ""))
            self.assertEqual(graph.validate(), [])

    def test_retry_edge_is_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            executor.record_result(
                tool_name="search",
                arguments={"query": "x"},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "temporary"},
            )
            executor.record_result(
                tool_name="search",
                arguments={"query": "x"},
                step_id=2,
                status=ToolStatus.SUCCESS,
                payload={"items": [1]},
            )
            retry_edges = [
                edge for edge in graph.edges.values() if edge.edge_type == EdgeType.RETRIES
            ]
            self.assertEqual(len(retry_edges), 1)

    def test_side_effect_call_is_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            call, _ = executor.record_result(
                tool_name="write_file",
                arguments={"path": "result.txt"},
                step_id=3,
                status=ToolStatus.SUCCESS,
                payload={"written": True},
                side_effect=True,
            )
            self.assertEqual(call.lifecycle, LifecycleState.AUDIT_REQUIRED)
            self.assertTrue(call.raw_ref)
            self.assertEqual(graph.validate(), [])


if __name__ == "__main__":
    unittest.main()
