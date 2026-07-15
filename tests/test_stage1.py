import json
import tempfile
import unittest
from pathlib import Path

from tracegraph.stage1 import (
    analyze_stage1_plan,
    materialize_trace_archives,
    materialize_trace_graphs,
    write_stage1_report,
)


def _plan() -> dict:
    return {
        "matrix_id": "stage1_test",
        "run_count": 1,
        "session_count": 2,
        "gates": {
            "minimum_task_success_rate": 0.5,
            "minimum_normal_stop_rate": 0.5,
            "minimum_median_tool_calls": 1.5,
            "minimum_median_estimated_tokens": 4096,
            "maximum_infrastructure_error_rate": 0.05,
        },
        "runs": [
            {
                "run_id": "retail_0",
                "domain": "retail",
                "task_id": "0",
                "trials": 2,
                "save_to": "retail_0",
                "trace_output_dir": "outputs/traces/retail_0",
            }
        ],
    }


def _simulation(trial: int, reward: float, termination: str, calls: int) -> dict:
    successful = reward == 1.0
    return {
        "id": f"simulation-{trial}",
        "task_id": "0",
        "trial": trial,
        "seed": 300 + trial,
        "reward_info": {
            "reward": reward,
            "db_check": {"db_reward": reward},
            "action_checks": [
                {"tool_type": "read", "action_match": True},
                {"tool_type": "write", "action_match": successful},
            ],
            "nl_assertions": [{"met": successful}],
            "communicate_checks": [{"met": True}],
        },
        "termination_reason": termination,
        "duration": 10 + trial,
        "agent_cost": 0.001,
        "user_cost": 0.0005,
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": index} for index in range(calls)]}
        ],
    }


def _trace(path: Path, token_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": path.parent.name,
                "metadata": {"graph_validation_errors": []},
                "nodes": [{"token_count": token_count}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )


class Stage1AnalysisTests(unittest.TestCase):
    def test_complete_matrix_passes_all_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "results" / "retail_0"
            results.mkdir(parents=True)
            (results / "results.json").write_text(
                json.dumps(
                    {
                        "simulations": [
                            _simulation(0, 1.0, "user_stop", 2),
                            _simulation(1, 0.0, "max_steps", 1),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _trace(root / "outputs/traces/retail_0/a/trace.json", 5000)
            _trace(root / "outputs/traces/retail_0/b/trace.json", 7000)

            report = analyze_stage1_plan(
                _plan(), project_root=root, results_root=root / "results"
            )
            self.assertEqual(report["state"], "pass")
            self.assertTrue(report["complete"])
            self.assertEqual(report["metrics"]["task_success_rate"], 0.5)
            self.assertEqual(report["metrics"]["normal_stop_rate"], 0.5)
            self.assertEqual(report["metrics"]["median_tool_calls"], 1.5)
            self.assertEqual(
                report["metrics"]["median_estimated_trajectory_tokens"], 6000.0
            )
            self.assertAlmostEqual(report["metrics"]["total_actual_cost_usd"], 0.003)
            self.assertEqual(report["counts"]["graph_validation_errors"], 0)
            self.assertEqual(report["domain_metrics"]["retail"]["successes"], 1)
            self.assertEqual(report["task_metrics"][0]["sessions"], 2)
            self.assertEqual(report["sessions"][0]["expected_read_actions"], 1)
            self.assertEqual(report["sessions"][0]["correct_read_actions"], 1)
            self.assertEqual(report["sessions"][1]["correct_write_actions"], 0)
            self.assertIn(
                "write_action_mismatch", report["sessions"][1]["failure_reasons"]
            )
            self.assertIn(
                "natural_language_assertion_mismatch",
                report["sessions"][1]["failure_reasons"],
            )
            self.assertIn("database_mismatch", report["failure_reason_counts"])

            output = root / "analysis"
            write_stage1_report(report, output)
            self.assertTrue((output / "stage1_report.json").exists())
            self.assertEqual(
                len((output / "stage1_sessions.csv").read_text().splitlines()), 3
            )
            graph_output = root / "flat_graphs"
            written = materialize_trace_graphs(
                report, project_root=root, output_dir=graph_output
            )
            self.assertEqual(len(written), 2)
            self.assertTrue((graph_output / "retail_0_trial0.json").exists())
            enriched = json.loads(
                (graph_output / "retail_0_trial0.json").read_text(encoding="utf-8")
            )
            self.assertEqual(enriched["metadata"]["official_simulation_id"], "simulation-0")
            self.assertEqual(enriched["metadata"]["evaluated_context_manager"], "full_trajectory")
            archive_source = (
                root / "outputs/traces/retail_0/a/archive/objects/ab/abcdef.json"
            )
            archive_source.parent.mkdir(parents=True)
            archive_source.write_text('{"digest":"abcdef"}', encoding="utf-8")
            archives = materialize_trace_archives(
                report, project_root=root, output_dir=root / "flat_archive"
            )
            self.assertEqual(len(archives), 1)
            self.assertTrue(
                (root / "flat_archive/objects/ab/abcdef.json").exists()
            )

    def test_missing_trace_keeps_matrix_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "results" / "retail_0"
            results.mkdir(parents=True)
            (results / "results.json").write_text(
                json.dumps(
                    {
                        "simulations": [
                            _simulation(0, 1.0, "user_stop", 2),
                            _simulation(1, 1.0, "user_stop", 2),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _trace(root / "outputs/traces/retail_0/a/trace.json", 5000)

            report = analyze_stage1_plan(
                _plan(), project_root=root, results_root=root / "results"
            )
            self.assertEqual(report["state"], "incomplete")
            self.assertFalse(report["overall_pass"])
            self.assertFalse(report["gates"]["minimum_task_success_rate"]["passed"])

    def test_invalid_graph_keeps_matrix_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "results" / "retail_0"
            results.mkdir(parents=True)
            (results / "results.json").write_text(
                json.dumps(
                    {
                        "simulations": [
                            _simulation(0, 1.0, "user_stop", 2),
                            _simulation(1, 1.0, "user_stop", 2),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _trace(root / "outputs/traces/retail_0/a/trace.json", 5000)
            invalid = root / "outputs/traces/retail_0/b/trace.json"
            _trace(invalid, 5000)
            payload = json.loads(invalid.read_text(encoding="utf-8"))
            payload["metadata"]["graph_validation_errors"] = ["broken edge"]
            invalid.write_text(json.dumps(payload), encoding="utf-8")

            report = analyze_stage1_plan(
                _plan(), project_root=root, results_root=root / "results"
            )
            self.assertEqual(report["state"], "incomplete")
            self.assertEqual(report["counts"]["graph_validation_errors"], 1)


if __name__ == "__main__":
    unittest.main()
