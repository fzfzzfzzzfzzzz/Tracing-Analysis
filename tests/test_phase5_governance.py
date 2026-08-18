import json
import unittest
from pathlib import Path

from tracegraph.manager_provenance import manager_provenance


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "phase5_liveness.json"
SCHEMA_PATH = ROOT / "configs" / "phase5_liveness.schema.json"


class Phase5GovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_identity_and_strategy_gate_are_frozen(self) -> None:
        manager = self.config["manager"]
        self.assertEqual(manager["name"], "lifecycle_graph_context")
        self.assertEqual(manager["implementation_version"], "lifecycle_graph_context_v1")
        self.assertEqual(manager["strategies"]["prune"]["policy_version"], "gdsc_prune_v1")
        self.assertEqual(manager["strategies"]["prune"]["status"], "active_development")
        self.assertEqual(
            manager["strategies"]["structured"]["policy_version"],
            "gdsc_structured_v1",
        )
        self.assertEqual(
            manager["strategies"]["structured"]["status"],
            "gated_after_f5_g2",
        )
        self.assertEqual(
            self.config["gates"]["structured_online_requires"],
            "f5_g2_passed",
        )

    def test_phase4_identity_and_no_go_remain_immutable(self) -> None:
        compatibility = self.config["compatibility"]
        governance = self.config["governance"]
        self.assertEqual(compatibility["legacy_manager"], "decision_state_compiler")
        self.assertEqual(compatibility["legacy_policy_version"], "gdsc_core_v1")
        self.assertTrue(compatibility["legacy_behavior_must_remain_unchanged"])
        self.assertEqual(governance["historical_r2_decision"], "no_go")
        self.assertEqual(governance["historical_e0_decision"], "no_go")
        self.assertEqual(governance["historical_r2_threshold_percent"], 30.0)
        self.assertTrue(governance["retroactive_threshold_change_forbidden"])

    def test_phase5_outputs_are_disjoint_and_external_runs_fail_closed(self) -> None:
        phase5_root = self.config["outputs"]["root"].rstrip("/")
        protected = self.config["compatibility"]["protected_output_roots"]
        self.assertEqual(phase5_root, "outputs/phase5")
        self.assertTrue(all(not path.startswith(f"{phase5_root}/") for path in protected))
        self.assertTrue(all(not phase5_root.startswith(f"{path}/") for path in protected))
        self.assertFalse(self.config["outputs"]["overwrite_existing"])
        execution = self.config["external_execution"]
        self.assertFalse(execution["provider_generation_authorized"])
        self.assertEqual(execution["sessions_consumed"], 0)
        self.assertFalse(execution["automatic_rerun"])
        self.assertTrue(self.config["gates"]["fail_closed"])

    def test_json_schema_freezes_the_same_top_level_contract(self) -> None:
        self.assertEqual(
            self.schema["properties"]["schema_version"]["const"],
            self.config["schema_version"],
        )
        self.assertEqual(
            self.schema["properties"]["manager"]["properties"]["name"]["const"],
            self.config["manager"]["name"],
        )
        self.assertEqual(set(self.schema["required"]), set(self.config))

    def test_manager_provenance_keeps_phase4_and_phase5_separate(self) -> None:
        provenance = manager_provenance(
            ["decision_state_compiler", "lifecycle_graph_context"]
        )
        self.assertEqual(
            provenance["decision_state_compiler"]["context_policy_version"],
            "gdsc_core_v1",
        )
        phase5 = provenance["lifecycle_graph_context"]
        self.assertEqual(phase5["context_policy_version"], "gdsc_prune_v1")
        self.assertEqual(
            phase5["structured_policy_version"],
            "gdsc_structured_v1",
        )
        self.assertFalse(phase5["main_result_eligible"])
        self.assertEqual(
            phase5["implementation_status"],
            "f5_g1_no_go",
        )


if __name__ == "__main__":
    unittest.main()
