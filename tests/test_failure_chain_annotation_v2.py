from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tracegraph.failure_chain_annotation import (
    ANNOTATION_FIELDS as V1_FIELDS,
    export_failure_chain_package,
)
from tracegraph.failure_chain_annotation_v2 import (
    ANNOTATION_FIELDS,
    V2_LABEL_FIELDS,
    build_failure_chain_items_v2,
    convert_v1_labels,
    export_failure_chain_package_v2,
    migrate_v1_package_to_v2,
    score_failure_chain_annotations_v2,
)
from tracegraph import ArchiveStore, TraceGraph
from tracegraph.capture import ToolExecutor
from tracegraph.failure_chain_annotation import build_failure_chain_items
from tracegraph.schema import ToolStatus


class FailureChainAnnotationV2Tests(unittest.TestCase):
    def _v1_item(self, session: str) -> dict:
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
            return build_failure_chain_items([("controlled", f"{session}.json", graph)])[0]

    def _v2_item(self, session: str) -> dict:
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
            return build_failure_chain_items_v2([("controlled", f"{session}.json", graph)])[0]

    @staticmethod
    def _fill_v2(
        path: Path,
        labels: dict[str, dict[str, str]],
        *,
        identity: str,
        provenance: str = "human_independent",
    ) -> None:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            for field in V2_LABEL_FIELDS:
                row[field] = labels[row["annotation_id"]][field]
            row["annotation_provenance"] = provenance
            row["annotator_identity"] = identity
            row["annotation_version"] = "2.0"
            row["independence_warning"] = ""
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ANNOTATION_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_v1_mapping_factorizes_state_and_cause(self) -> None:
        active, lossy = convert_v1_labels(
            {
                "same_operation_scope": "yes",
                "relation": "still_active",
                "failure_class": "actionable",
                "expiry_trigger": "still_active",
                "card_covers_next_step": "yes",
            }
        )
        self.assertEqual(active["should_card_remain_active"], "yes")
        self.assertEqual(active["expiry_cause"], "still_active")
        self.assertEqual(active["scope_relation"], "same_operation")
        self.assertEqual(lossy, [])

        terminal, lossy = convert_v1_labels(
            {
                "same_operation_scope": "not_applicable",
                "relation": "other",
                "failure_class": "terminal",
                "expiry_trigger": "terminal",
                "card_covers_next_step": "not_applicable",
            }
        )
        self.assertEqual(terminal["should_card_remain_active"], "no")
        self.assertEqual(terminal["expiry_cause"], "other")
        self.assertEqual(lossy, ["expiry_cause"])

    def test_blind_export_and_agreement_report_include_ac1_and_distributions(self) -> None:
        items = [self._v2_item(f"s-{index}") for index in range(4)]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            key = export_failure_chain_package_v2(
                controlled_items=items,
                natural_items=[],
                output_dir=output,
                controlled_sample_size=4,
                natural_sample_size=0,
                seed=3,
            )
            self.assertNotIn(
                "prediction",
                (output / "human_annotator_a.csv").read_text(encoding="utf-8-sig"),
            )
            predictions = {item["annotation_id"]: item["prediction"] for item in key["items"]}
            self._fill_v2(output / "human_annotator_a.csv", predictions, identity="human-a")
            self._fill_v2(output / "human_annotator_b.csv", predictions, identity="human-b")
            report = score_failure_chain_annotations_v2(
                output / "human_annotator_a.csv",
                output / "human_annotator_b.csv",
                output / "annotation_key.json",
                minimum_complete_chains=4,
            )
            self.assertTrue(report["complete"])
            self.assertTrue(report["human_independent_annotations"])
            for value in report["agreement"].values():
                self.assertEqual(value["raw_agreement"], 1.0)
                self.assertEqual(value["cohen_kappa"], 1.0)
                self.assertEqual(value["gwet_ac1"], 1.0)
                self.assertTrue(value["annotator_a_distribution"])
            self.assertEqual(report["retention_safety"]["recall"], 1.0)

    def test_inconsistent_activity_and_expiry_fail_closed(self) -> None:
        item = self._v2_item("bad")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            key = export_failure_chain_package_v2(
                controlled_items=[item],
                natural_items=[],
                output_dir=output,
                controlled_sample_size=1,
                natural_sample_size=0,
            )
            labels = {
                key["items"][0]["annotation_id"]: {
                    **key["items"][0]["prediction"],
                    "should_card_remain_active": "yes",
                    "expiry_cause": "resolved",
                }
            }
            self._fill_v2(output / "human_annotator_a.csv", labels, identity="human-a")
            self._fill_v2(output / "human_annotator_b.csv", labels, identity="human-b")
            with self.assertRaisesRegex(ValueError, "active card"):
                score_failure_chain_annotations_v2(
                    output / "human_annotator_a.csv",
                    output / "human_annotator_b.csv",
                    output / "annotation_key.json",
                    minimum_complete_chains=1,
                )

    def test_migration_is_non_destructive_and_emits_clean_human_sheets(self) -> None:
        items = [self._v1_item(f"m-{index}") for index in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1 = root / "v1"
            v2 = root / "v2"
            key = export_failure_chain_package(
                controlled_items=items,
                natural_items=[],
                output_dir=v1,
                controlled_sample_size=2,
                natural_sample_size=0,
                seed=4,
            )
            predictions = {item["annotation_id"]: item["prediction"] for item in key["items"]}
            for name, identity in (
                ("annotator_a.csv", "codex-a"),
                ("annotator_b.csv", "codex-b"),
            ):
                with (v1 / name).open(encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                for row in rows:
                    for field, value in predictions[row["annotation_id"]].items():
                        row[field] = value
                    row["annotation_provenance"] = "codex_provisional"
                    row["annotator_identity"] = identity
                    row["annotation_version"] = "codex_p2_v1"
                with (v1 / name).open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=V1_FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
            with (v1 / "adjudication.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=(
                        "annotation_id",
                        "disagreement_fields",
                        "same_operation_scope",
                        "relation",
                        "failure_class",
                        "expiry_trigger",
                        "card_covers_next_step",
                    ),
                )
                writer.writeheader()

            before = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in v1.iterdir()
                if path.is_file()
            }
            audit = migrate_v1_package_to_v2(v1, v2)
            after = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in v1.iterdir()
                if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertEqual(audit["migrated_key_count"], 2)
            clean = (v2 / "human_annotator_a.csv").read_text(encoding="utf-8-sig")
            self.assertNotIn("codex-a", clean)
            self.assertNotIn("codex_provisional", clean)
            migrated_key = json.loads((v2 / "annotation_key.json").read_text(encoding="utf-8"))
            self.assertEqual(migrated_key["schema_version"], "2.0")


if __name__ == "__main__":
    unittest.main()
