import copy
import unittest

from tracegraph.capture import TOKEN_ACCOUNTING_VERSION
from tracegraph.matrix import (
    build_matrix_plan,
    require_execution_budget,
    require_codex_provisional_p2,
    require_phase3_p2_construct_gate,
    require_phase3_p4_go,
)


def sample_config():
    return {
        "matrix_id": "pilot_v1",
        "agent_model": "zai/glm-4.5-air",
        "user_model": "zai/glm-4.5-air",
        "normalize_user_stop": True,
        "base_seed": 300,
        "trials": 3,
        "max_steps": 50,
        "timeout_seconds": 900,
        "inter_run_delay_seconds": 2.5,
        "estimated_cost_per_session_usd": 0.01,
        "domains": [
            {"name": "retail", "task_ids": ["0", "1"]},
            {"name": "airline", "task_ids": ["0"]},
        ],
        "conditions": [{"manager": "full_trajectory", "budget": "none"}],
        "gates": {"minimum_task_success_rate": 0.5},
    }


class MatrixPlanTests(unittest.TestCase):
    def test_formal_p3_execution_requires_passing_p2_report(self) -> None:
        with self.assertRaisesRegex(ValueError, "passing P2"):
            require_phase3_p2_construct_gate(None)
        with self.assertRaisesRegex(ValueError, "passing P2"):
            require_phase3_p2_construct_gate(
                {
                    "complete": True,
                    "chain_count": 60,
                    "annotation_provenance": "human_independent",
                    "human_independent_annotations": True,
                    "cohen_kappa": 0.69,
                    "actionable_precision": 1.0,
                    "actionable_recall": 1.0,
                    "expiry_precision": 1.0,
                    "operation_scope_aggregation_error_rate": 0.0,
                }
            )
        require_phase3_p2_construct_gate(
            {
                "complete": True,
                "chain_count": 60,
                "annotation_provenance": "human_independent",
                "human_independent_annotations": True,
                "cohen_kappa": 0.70,
                "actionable_precision": 0.75,
                "actionable_recall": 0.75,
                "expiry_precision": 0.90,
                "operation_scope_aggregation_error_rate": 0.10,
            }
        )

    def test_codex_report_only_authorizes_provisional_lane(self) -> None:
        report = {
            "complete": True,
            "chain_count": 60,
            "annotation_provenance": "codex_provisional",
            "human_independent_annotations": False,
            "unresolved_adjudications": 0,
        }
        require_codex_provisional_p2(report)
        with self.assertRaisesRegex(ValueError, "human construct"):
            require_phase3_p2_construct_gate(report)
        with self.assertRaisesRegex(ValueError, "Codex-labelled"):
            require_codex_provisional_p2(
                {**report, "annotation_provenance": "human_independent"}
            )

    def test_p4_execution_requires_positive_phase3_gate(self) -> None:
        with self.assertRaises(ValueError):
            require_phase3_p4_go(None)
        with self.assertRaises(ValueError):
            require_phase3_p4_go(
                {"p4": {"go_gate_passed": False, "blockers": ["p2"]}}
            )
        require_phase3_p4_go({"p4": {"go_gate_passed": True, "blockers": []}})

    def test_expands_paired_tasks_trials_and_cost(self):
        plan = build_matrix_plan(sample_config())
        self.assertEqual(plan["run_count"], 3)
        self.assertEqual(plan["session_count"], 9)
        self.assertEqual(plan["estimated_total_cost_usd"], 0.09)
        self.assertTrue(all(run["base_seed"] == 300 for run in plan["runs"]))
        self.assertTrue(all(run["trials"] == 3 for run in plan["runs"]))
        self.assertEqual(plan["inter_run_delay_seconds"], 2.5)
        self.assertEqual(
            plan["paired_invariants"]["inter_run_delay_seconds"], 2.5
        )
        self.assertEqual(plan["token_accounting"], TOKEN_ACCOUNTING_VERSION)
        self.assertTrue(
            all(
                run["token_accounting"] == TOKEN_ACCOUNTING_VERSION
                for run in plan["runs"]
            )
        )

    def test_rejects_any_secret_like_config_key(self):
        config = sample_config()
        config["provider"] = {"api_key": "must-not-be-here"}
        with self.assertRaisesRegex(ValueError, "sensitive key"):
            build_matrix_plan(config)

    def test_rejects_unknown_manager(self):
        config = sample_config()
        config["conditions"] = [{"manager": "unknown", "budget": "2048"}]
        with self.assertRaisesRegex(ValueError, "unknown manager"):
            build_matrix_plan(config)

    def test_preserves_explicit_nl_evaluator_model(self):
        config = sample_config()
        config["evaluator_model"] = "zai/glm-4.7-flash"
        plan = build_matrix_plan(config)
        self.assertEqual(plan["evaluator_model"], "zai/glm-4.7-flash")
        self.assertEqual(
            plan["paired_invariants"]["evaluator_model"], "zai/glm-4.7-flash"
        )
        self.assertTrue(
            all(
                run["evaluator_model"] == "zai/glm-4.7-flash"
                for run in plan["runs"]
            )
        )

    def test_accepts_live_only_official_acon_manager(self):
        config = sample_config()
        config["conditions"] = [{"manager": "acon_official", "budget": "official_config"}]
        plan = build_matrix_plan(config)
        self.assertEqual(plan["conditions"][0]["manager"], "acon_official")

    def test_short_run_label_preserves_manager_identity(self):
        config = sample_config()
        config["conditions"] = [
            {
                "manager": "ours_without_failure_retention",
                "budget": "4096",
                "run_label": "remove",
            }
        ]
        plan = build_matrix_plan(config)
        self.assertEqual(
            plan["runs"][0]["manager"], "ours_without_failure_retention"
        )
        self.assertIn("_remove_b4096", plan["runs"][0]["run_id"])

        config["conditions"][0]["run_label"] = "not safe"
        with self.assertRaisesRegex(ValueError, "run_label"):
            build_matrix_plan(config)

    def test_rejects_duplicate_tasks_and_conditions(self):
        duplicate_tasks = sample_config()
        duplicate_tasks["domains"][0]["task_ids"] = ["0", "0"]
        with self.assertRaisesRegex(ValueError, "duplicate task_ids"):
            build_matrix_plan(duplicate_tasks)

        duplicate_conditions = copy.deepcopy(sample_config())
        duplicate_conditions["conditions"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate condition"):
            build_matrix_plan(duplicate_conditions)

    def test_execution_requires_cap_covering_estimate(self):
        plan = build_matrix_plan(sample_config())
        with self.assertRaisesRegex(ValueError, "explicit max"):
            require_execution_budget(plan, None)
        with self.assertRaisesRegex(ValueError, "exceeds explicit cap"):
            require_execution_budget(plan, 0.08)
        require_execution_budget(plan, 0.09)

    def test_rejects_negative_inter_run_delay(self):
        config = sample_config()
        config["inter_run_delay_seconds"] = -1
        with self.assertRaisesRegex(ValueError, "inter_run_delay_seconds"):
            build_matrix_plan(config)

    def test_rejects_stale_token_accounting(self):
        config = sample_config()
        config["token_accounting"] = "prompt_usage_v1"
        with self.assertRaisesRegex(ValueError, "token_accounting"):
            build_matrix_plan(config)


if __name__ == "__main__":
    unittest.main()
