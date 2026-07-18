from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracegraph.interventions import (
    InterventionConfig,
    P1_CONDITIONS,
    P1_INTERVENTION_KINDS,
    build_intervention_specs,
    run_p1_interventions,
)


class InterventionTests(unittest.TestCase):
    def test_requires_plan_sized_task_bucket(self) -> None:
        with self.assertRaises(ValueError):
            InterventionConfig(tasks_per_kind=4)
        with self.assertRaises(ValueError):
            InterventionConfig(tasks_per_kind=11)

    def test_builds_fixed_task_and_seed_matrix(self) -> None:
        config = InterventionConfig(tasks_per_kind=5, base_seed=700)
        specs = build_intervention_specs(config)

        self.assertEqual(len(specs), 20)
        self.assertEqual({spec.intervention_kind for spec in specs}, set(P1_INTERVENTION_KINDS))
        self.assertEqual([spec.seed for spec in specs], list(range(700, 720)))
        self.assertEqual(len({spec.intervention_id for spec in specs}), 20)

    def test_complete_matrix_is_auditable_and_mechanism_identifiable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = run_p1_interventions(
                output,
                config=InterventionConfig(tasks_per_kind=5, budget=512),
            )

            self.assertEqual(manifest["task_count"], 20)
            self.assertEqual(manifest["run_count"], 80)
            self.assertEqual(manifest["conditions"], list(P1_CONDITIONS))
            self.assertTrue(manifest["mechanism_gate"]["p1_engineering_gate_passed"])
            for name in manifest["files"][:5]:
                self.assertTrue((output / name).is_file(), name)

            rows = [
                json.loads(line)
                for line in (output / "per_run.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 80)
            self.assertTrue(all(not row["graph_validation_errors"] for row in rows))
            card_rows = [row for row in rows if row["manager"] == "full_ours"]
            remove_rows = [
                row
                for row in rows
                if row["manager"] == "ours_without_failure_retention"
            ]
            self.assertTrue(all(row["card_precision_controlled_gold"] == 1.0 for row in card_rows))
            self.assertTrue(
                all(row["expiry_correctness_controlled_gold"] == 1.0 for row in card_rows)
            )
            self.assertTrue(all(row["repeated_invalid_action"] == 0 for row in card_rows))
            self.assertTrue(all(row["repeated_invalid_action"] == 1 for row in remove_rows))

            comparisons = json.loads(
                (output / "paired_comparisons.json").read_text(encoding="utf-8")
            )
            vs_raw = comparisons["full_ours_vs_raw_hard_failure_retention"]
            self.assertLess(vs_raw["mean_protocol_closed_message_tokens_delta"], 0)
            self.assertLess(vs_raw["mean_actual_provider_input_tokens_delta"], 0)


if __name__ == "__main__":
    unittest.main()
