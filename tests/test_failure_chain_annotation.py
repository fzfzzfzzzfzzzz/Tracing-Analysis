from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from tracegraph import ArchiveStore, TraceGraph
from tracegraph.capture import ToolExecutor
from tracegraph.failure_chain_annotation import (
    LABEL_FIELDS,
    build_failure_chain_items,
    export_failure_chain_package,
    score_failure_chain_annotations,
    write_failure_chain_score,
)
from tracegraph.schema import ToolStatus


class FailureChainAnnotationTests(unittest.TestCase):
    def _item(self, session: str, source: str) -> dict:
        graph = TraceGraph(session)
        with tempfile.TemporaryDirectory() as archive:
            executor = ToolExecutor(graph, ArchiveStore(archive))
            executor.record_result(
                tool_name="lookup",
                arguments={"record_id": session},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "not found"},
            )
            return build_failure_chain_items([(source, f"{session}.json", graph)])[0]

    @staticmethod
    def _fill(
        path: Path,
        predictions: dict[str, dict[str, str]],
        *,
        provenance: str = "",
        identity: str = "",
    ) -> None:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            for field in LABEL_FIELDS:
                row[field] = predictions[row["annotation_id"]][field]
            row["annotation_provenance"] = provenance
            row["annotator_identity"] = identity
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_export_is_blind_and_score_is_gate_compatible(self) -> None:
        controlled = [self._item(f"c-{index}", "controlled") for index in range(2)]
        natural = [self._item(f"n-{index}", "natural") for index in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            key = export_failure_chain_package(
                controlled_items=controlled,
                natural_items=natural,
                output_dir=output,
                controlled_sample_size=2,
                natural_sample_size=2,
                seed=1,
            )
            sheet = (output / "annotator_a.csv").read_text(encoding="utf-8-sig")
            self.assertNotIn("prediction", sheet)
            predictions = {
                item["annotation_id"]: item["prediction"] for item in key["items"]
            }
            self._fill(output / "annotator_a.csv", predictions)
            self._fill(output / "annotator_b.csv", predictions)
            report = score_failure_chain_annotations(
                output / "annotator_a.csv",
                output / "annotator_b.csv",
                output / "annotation_key.json",
            )
            self.assertEqual(report["cohen_kappa"], 1.0)
            self.assertEqual(report["actionable_precision"], 1.0)
            self.assertEqual(report["operation_scope_aggregation_error_rate"], None)
            self.assertFalse(report["complete"])
            self.assertFalse(report["human_independent_annotations"])
            write_failure_chain_score(report, output / "p2_report.json")
            self.assertTrue((output / "adjudication.csv").exists())

    def test_blank_label_fails_closed(self) -> None:
        item = self._item("one", "controlled")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            export_failure_chain_package(
                controlled_items=[item],
                natural_items=[],
                output_dir=output,
                controlled_sample_size=1,
                natural_sample_size=0,
            )
            with self.assertRaisesRegex(ValueError, "invalid or blank"):
                score_failure_chain_annotations(
                    output / "annotator_a.csv",
                    output / "annotator_b.csv",
                    output / "annotation_key.json",
                )

    def test_codex_provenance_is_never_human_gold(self) -> None:
        controlled = [self._item(f"c-{index}", "controlled") for index in range(60)]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            key = export_failure_chain_package(
                controlled_items=controlled,
                natural_items=[],
                output_dir=output,
                controlled_sample_size=60,
                natural_sample_size=0,
            )
            predictions = {
                item["annotation_id"]: item["prediction"] for item in key["items"]
            }
            self._fill(
                output / "annotator_a.csv",
                predictions,
                provenance="codex_provisional",
                identity="codex_pass_a",
            )
            self._fill(
                output / "annotator_b.csv",
                predictions,
                provenance="codex_provisional",
                identity="codex_pass_b",
            )
            report = score_failure_chain_annotations(
                output / "annotator_a.csv",
                output / "annotator_b.csv",
                output / "annotation_key.json",
            )
            self.assertTrue(report["complete"])
            self.assertEqual(report["annotation_provenance"], "codex_provisional")
            self.assertFalse(report["human_independent_annotations"])


if __name__ == "__main__":
    unittest.main()
