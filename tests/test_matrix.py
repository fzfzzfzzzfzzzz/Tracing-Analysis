import copy
import unittest

from tracegraph.matrix import build_matrix_plan, require_execution_budget


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
        "estimated_cost_per_session_usd": 0.01,
        "domains": [
            {"name": "retail", "task_ids": ["0", "1"]},
            {"name": "airline", "task_ids": ["0"]},
        ],
        "conditions": [{"manager": "full_trajectory", "budget": "none"}],
        "gates": {"minimum_task_success_rate": 0.5},
    }


class MatrixPlanTests(unittest.TestCase):
    def test_expands_paired_tasks_trials_and_cost(self):
        plan = build_matrix_plan(sample_config())
        self.assertEqual(plan["run_count"], 3)
        self.assertEqual(plan["session_count"], 9)
        self.assertEqual(plan["estimated_total_cost_usd"], 0.09)
        self.assertTrue(all(run["base_seed"] == 300 for run in plan["runs"]))
        self.assertTrue(all(run["trials"] == 3 for run in plan["runs"]))

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

    def test_accepts_live_only_official_acon_manager(self):
        config = sample_config()
        config["conditions"] = [{"manager": "acon_official", "budget": "official_config"}]
        plan = build_matrix_plan(config)
        self.assertEqual(plan["conditions"][0]["manager"], "acon_official")

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


if __name__ == "__main__":
    unittest.main()
