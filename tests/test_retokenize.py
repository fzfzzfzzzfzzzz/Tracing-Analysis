import tempfile
import unittest
from pathlib import Path

from tracegraph.capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens
from tracegraph.graph import TraceGraph
from tracegraph.retokenize import retokenize_trace, retokenize_tree
from tracegraph.schema import NodeType


class RetokenizeTests(unittest.TestCase):
    def test_repairs_prompt_inflated_node_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = TraceGraph(session_id="usage")
            node = graph.create_node(
                NodeType.DECISION,
                "brief answer",
                1,
                token_count=5000,
            )
            path = root / "run" / "session" / "trace.json"
            graph.save(path)

            report = retokenize_trace(path)
            repaired = TraceGraph.load(path)

            self.assertEqual(report["changed_nodes"], 1)
            self.assertEqual(
                repaired.nodes[node.node_id].token_count,
                estimate_tokens("brief answer"),
            )
            self.assertEqual(
                repaired.metadata["token_accounting"],
                TOKEN_ACCOUNTING_VERSION,
            )
            self.assertEqual(repaired.validate(), [])

    def test_discovers_only_trace_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = TraceGraph(session_id="tree")
            graph.create_node(NodeType.GOAL, "goal", 0, token_count=999)
            graph.save(root / "a" / "trace.json")
            graph.save(root / "b" / "trace.json")
            (root / "other.json").write_text("{}", encoding="utf-8")

            rows = retokenize_tree(root)

            self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
