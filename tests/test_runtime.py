from __future__ import annotations

import tempfile
import unittest

from tracegraph import ArchiveStore, EdgeType, LifecycleState, NodeType
from tracegraph.context import GraphLifecycleManager
from tracegraph.runtime import (
    ContextManagedAgent,
    ModelTurn,
    ScriptedBackend,
    ToolRequest,
    ToolSpec,
)


class RuntimeTests(unittest.TestCase):
    def test_fixed_scaffold_records_retry_resolution_and_final_evidence(self) -> None:
        attempts = 0

        def flaky_lookup(query: str) -> dict[str, str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary backend failure")
            return {"answer": query.upper()}

        backend = ScriptedBackend(
            [
                ModelTurn(tool_calls=[ToolRequest("lookup", {"query": "alpha"})]),
                ModelTurn(tool_calls=[ToolRequest("lookup", {"query": "alpha"})]),
                ModelTurn(content="ALPHA is the verified answer."),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(directory)
            agent = ContextManagedAgent(
                backend=backend,
                tools=[ToolSpec("lookup", flaky_lookup)],
                context_manager=GraphLifecycleManager(),
                archive=archive,
                budget=200,
            )
            result = agent.run("find alpha", constraints=["do not invent results"])
            graph = result.graph
            self.assertEqual(result.termination_reason, "model_final")
            self.assertEqual(result.final_text, "ALPHA is the verified answer.")
            self.assertEqual(
                len(
                    [edge for edge in graph.edges.values() if edge.edge_type == EdgeType.RETRIED_BY]
                ),
                1,
            )
            self.assertEqual(
                len(
                    [
                        edge
                        for edge in graph.edges.values()
                        if edge.edge_type == EdgeType.RESOLVED_BY
                    ]
                ),
                1,
            )
            errors = graph.find_nodes(node_types={NodeType.ERROR})
            self.assertEqual(errors[0].lifecycle, LifecycleState.RESOLVED_FAILURE)
            final = [
                node
                for node in graph.find_nodes(node_types={NodeType.DECISION})
                if node.metadata.get("final")
            ]
            self.assertEqual(len(final), 1)
            self.assertEqual(graph.validate(), [])

    def test_unknown_tool_is_retained_as_unresolved_failure(self) -> None:
        backend = ScriptedBackend(
            [
                ModelTurn(tool_calls=[ToolRequest("missing", {})]),
                ModelTurn(content="cannot proceed"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = ContextManagedAgent(
                backend=backend,
                tools=[],
                context_manager=GraphLifecycleManager(),
                archive=ArchiveStore(directory),
            ).run("try missing tool")
            errors = result.graph.find_nodes(node_types={NodeType.ERROR})
            self.assertEqual(errors[0].lifecycle, LifecycleState.UNRESOLVED_FAILURE)
            self.assertIn(errors[0].node_id, result.context_views[-1].covered_node_ids)


if __name__ == "__main__":
    unittest.main()
