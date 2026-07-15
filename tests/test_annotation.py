import csv
import tempfile
import unittest
from pathlib import Path

from tracegraph.annotation import (
    export_annotation_package,
    score_annotations,
    write_annotation_score,
)
from tracegraph.graph import TraceGraph
from tracegraph.schema import LifecycleState, Node, NodeType


class AnnotationTests(unittest.TestCase):
    def _graph(self) -> TraceGraph:
        graph = TraceGraph("annotation-session")
        graph.add_node(
            Node(
                node_id="goal",
                node_type=NodeType.GOAL,
                content="do the task",
                step_id=0,
                lifecycle=LifecycleState.ACTIVE,
            )
        )
        graph.add_node(
            Node(
                node_id="observation",
                node_type=NodeType.OBSERVATION,
                content="evidence",
                step_id=1,
                lifecycle=LifecycleState.CRITICAL_EVIDENCE,
            )
        )
        return graph

    @staticmethod
    def _fill(path: Path, label: str) -> None:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fields = list(rows[0])
        for row in rows:
            row["annotator_label"] = label
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_blind_export_and_perfect_agreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            key = export_annotation_package(
                [self._graph()], output_dir=output, sample_size=2, seed=300
            )
            self.assertEqual(key["sample_size_actual"], 2)
            sheet = (output / "annotator_a.csv").read_text(encoding="utf-8-sig")
            self.assertNotIn("predicted_lifecycle", sheet)
            self._fill(output / "annotator_a.csv", "active")
            self._fill(output / "annotator_b.csv", "active")
            expected_ids = {item["annotation_id"] for item in key["items"]}
            report = score_annotations(
                output / "annotator_a.csv",
                output / "annotator_b.csv",
                expected_ids=expected_ids,
            )
            self.assertEqual(report["cohen_kappa"], 1.0)
            self.assertEqual(report["disagreement_count"], 0)
            write_annotation_score(report, output / "score")
            self.assertTrue((output / "score/agreement.json").exists())

    def test_rejects_blank_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            export_annotation_package(
                [self._graph()], output_dir=output, sample_size=1, seed=300
            )
            with self.assertRaisesRegex(ValueError, "invalid or blank"):
                score_annotations(
                    output / "annotator_a.csv", output / "annotator_b.csv"
                )


if __name__ == "__main__":
    unittest.main()
