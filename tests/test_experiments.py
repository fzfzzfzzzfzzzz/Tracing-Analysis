from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracegraph import ArchiveStore
from tracegraph.experiments import (
    ExperimentConfig,
    ExperimentRunner,
    discover_graphs,
    prefix_graph,
)
from tracegraph.synthetic import build_synthetic_trace


class ExperimentTests(unittest.TestCase):
    def test_prefix_graph_contains_no_future_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = build_synthetic_trace(ArchiveStore(directory))
            prefix = prefix_graph(graph, 2)
            self.assertTrue(prefix.nodes)
            self.assertTrue(all(node.step_id <= 2 for node in prefix.nodes.values()))
            self.assertTrue(
                all(
                    edge.source in prefix.nodes and edge.target in prefix.nodes
                    for edge in prefix.edges.values()
                )
            )

    def test_complete_synthetic_suite_writes_auditable_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = ArchiveStore(root / "archive")
            graph = build_synthetic_trace(archive)
            graph_path = root / "graphs" / "synthetic.json"
            graph.save(graph_path)
            graphs = discover_graphs(graph_path.parent)
            manifest = ExperimentRunner(
                ExperimentConfig(budget=100, online_replay=True, provenance="unit_test"),
                archive=archive,
            ).run(graphs, root / "results")
            self.assertTrue(manifest["contains_synthetic_data"])
            self.assertEqual(manifest["graph_count"], 1)
            self.assertEqual(len(manifest["manager_names"]), 12)
            for name in manifest["files"]:
                self.assertTrue((root / "results" / name).is_file(), name)
            rows = [
                json.loads(line)
                for line in (root / "results" / "per_session.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(rows), 12)
            ours = next(row for row in rows if row["manager"] == "full_ours")
            self.assertEqual(ours["evidence_retention"], 1.0)
            self.assertEqual(ours["unresolved_failure_retention"], 1.0)
            self.assertEqual(ours["constraint_retention"], 1.0)
            self.assertEqual(ours["archive_recoverability"], 1.0)
            no_failure = next(
                row for row in rows if row["manager"] == "ours_without_failure_retention"
            )
            no_constraint = next(
                row for row in rows if row["manager"] == "ours_without_constraint_retention"
            )
            self.assertLess(no_failure["unresolved_failure_retention"], 1.0)
            self.assertLess(no_constraint["constraint_retention"], 1.0)
            self.assertIsNone(ours["task_success"])
            full = next(row for row in rows if row["manager"] == "full_trajectory")
            self.assertEqual(full["task_success"], 1.0)
            aggregate = json.loads(
                (root / "results" / "aggregate.json").read_text(encoding="utf-8")
            )
            self.assertIn("ours_without_failure_retention", aggregate)
            lifecycle = json.loads(
                (root / "results" / "lifecycle_analysis.json").read_text(encoding="utf-8")
            )
            self.assertGreater(lifecycle["sessions"], 0)


if __name__ == "__main__":
    unittest.main()
