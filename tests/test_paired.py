import json
import os
import tempfile
import unittest
from pathlib import Path

from tracegraph.paired import (
    _holm_adjust,
    analyze_live_matrix,
    write_live_matrix_report,
)


def _plan() -> dict:
    runs = []
    for manager, budget in (
        ("full_trajectory", "none"),
        ("full_ours", "16384"),
    ):
        for task_id in ("0", "1"):
            run_id = f"{manager}_{task_id}"
            runs.append(
                {
                    "run_id": run_id,
                    "manager": manager,
                    "budget": budget,
                    "domain": "retail",
                    "task_id": task_id,
                    "trials": 1,
                    "save_to": run_id,
                    "trace_output_dir": f"outputs/traces/{run_id}",
                }
            )
    return {
        "matrix_id": "paired_test",
        "run_count": 4,
        "session_count": 4,
        "interpretation_warning": "pilot only",
        "runs": runs,
    }


def _simulation(
    run_id: str,
    reward: float | None,
    termination: str = "user_stop",
) -> dict:
    return {
        "id": f"sim-{run_id}",
        "trial": 0,
        "seed": 300,
        "reward_info": {"reward": reward},
        "termination_reason": termination,
        "duration": 10.0,
        "agent_cost": 0.0,
        "user_cost": 0.0,
        "messages": [{"role": "assistant", "tool_calls": [{"id": "call"}]}],
    }


def _write_run(root: Path, run: dict, reward: float) -> None:
    result_dir = root / "results" / run["save_to"]
    result_dir.mkdir(parents=True)
    simulation = _simulation(run["run_id"], reward)
    simulation["messages"][0]["usage"] = {
        "prompt_tokens": (
            1000 if run["manager"] == "full_trajectory" else 400
        ),
        "completion_tokens": 50,
    }
    (result_dir / "results.json").write_text(
        json.dumps({"simulations": [simulation]}),
        encoding="utf-8",
    )
    trace = root / run["trace_output_dir"] / run["run_id"] / "trace.json"
    trace.parent.mkdir(parents=True)
    trace.write_text(
        json.dumps(
            {
                "session_id": run["run_id"],
                "metadata": {"graph_validation_errors": []},
                "nodes": [{"token_count": 5000}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    selected_tokens = 5000 if run["manager"] == "full_trajectory" else 2000
    (trace.parent / "context_views.jsonl").write_text(
        json.dumps(
            {
                "selected_tokens": selected_tokens,
                "compression_ratio": (
                    0.0 if run["manager"] == "full_trajectory" else 0.6
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )


class PairedMatrixTests(unittest.TestCase):
    def test_holm_adjustment_is_step_down_and_preserves_missing_values(self):
        self.assertEqual(
            _holm_adjust(
                {
                    "small": 0.01,
                    "middle": 0.04,
                    "large": 0.5,
                    "missing": None,
                }
            ),
            {
                "small": 0.03,
                "middle": 0.08,
                "large": 0.5,
                "missing": None,
            },
        )

    def test_matches_multi_trial_traces_by_simulation_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = {
                "run_id": "full_trajectory_multi",
                "manager": "full_trajectory",
                "budget": "none",
                "domain": "retail",
                "task_id": "0",
                "trials": 2,
                "save_to": "full_trajectory_multi",
                "trace_output_dir": "outputs/traces/full_trajectory_multi",
            }
            plan = {
                "matrix_id": "multi_trial_test",
                "run_count": 1,
                "session_count": 2,
                "runs": [run],
            }
            simulations = [
                {
                    **_simulation("trial-a", 1.0),
                    "id": "sim-a",
                    "trial": 0,
                },
                {
                    **_simulation("trial-b", 0.0),
                    "id": "sim-b",
                    "trial": 1,
                },
            ]
            result_dir = root / "results" / run["save_to"]
            result_dir.mkdir(parents=True)
            (result_dir / "results.json").write_text(
                json.dumps({"simulations": simulations}),
                encoding="utf-8",
            )

            trace_root = root / run["trace_output_dir"]
            trace_b = trace_root / "trace-b" / "trace.json"
            trace_a = trace_root / "trace-a" / "trace.json"
            for path, simulation_id, tokens in (
                (trace_b, "sim-b", 2222),
                (trace_a, "sim-a", 1111),
            ):
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps(
                        {
                            "session_id": f"trace-{simulation_id}",
                            "metadata": {
                                "simulation_id": simulation_id,
                                "graph_validation_errors": [],
                            },
                            "nodes": [{"token_count": tokens}],
                            "edges": [],
                        }
                    ),
                    encoding="utf-8",
                )
            os.utime(trace_b, (1, 1))
            os.utime(trace_a, (2, 2))

            report = analyze_live_matrix(
                plan,
                project_root=root,
                results_root=root / "results",
                bootstrap_samples=100,
            )
            self.assertTrue(report["complete"])
            tokens_by_simulation = {
                row["simulation_id"]: row["estimated_trajectory_tokens"]
                for row in report["sessions"]
            }
            self.assertEqual(tokens_by_simulation, {"sim-a": 1111, "sim-b": 2222})
            metrics = report["condition_metrics"]["full_trajectory"]
            self.assertEqual(metrics["minimum_evaluated_trials_per_task"], 2)
            self.assertEqual(metrics["pass_hat_ks"], {1: 0.5, 2: 0.0})
            self.assertEqual(
                report["domain_condition_metrics"]["full_trajectory"]["retail"][
                    "pass_hat_ks"
                ],
                {1: 0.5, 2: 0.0},
            )

    def test_reports_condition_metrics_and_paired_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rewards = {
                ("full_trajectory", "0"): 1.0,
                ("full_trajectory", "1"): 0.0,
                ("full_ours", "0"): 1.0,
                ("full_ours", "1"): 1.0,
            }
            plan = _plan()
            for run in plan["runs"]:
                _write_run(
                    root,
                    run,
                    rewards[(run["manager"], run["task_id"])],
                )

            report = analyze_live_matrix(
                plan,
                project_root=root,
                results_root=root / "results",
                bootstrap_samples=100,
            )
            self.assertTrue(report["complete"])
            self.assertEqual(
                report["condition_metrics"]["full_trajectory"]["task_success_rate"],
                0.5,
            )
            self.assertEqual(
                report["condition_metrics"]["full_ours"]["task_success_rate"],
                1.0,
            )
            comparison = report["paired_comparisons"]["full_ours"]
            self.assertEqual(comparison["eligible_pairs"], 2)
            self.assertEqual(comparison["both_success"], 1)
            self.assertEqual(comparison["comparator_only_success"], 1)
            self.assertEqual(comparison["success_rate_delta"], 0.5)
            self.assertEqual(comparison["exact_mcnemar_p"], 1.0)
            self.assertEqual(comparison["holm_adjusted_mcnemar_p"], 1.0)
            self.assertEqual(
                comparison["mean_total_selected_context_tokens_delta"], -3000.0
            )
            self.assertEqual(
                report["condition_metrics"]["full_trajectory"][
                    "mean_agent_provider_input_tokens"
                ],
                1000.0,
            )
            self.assertEqual(
                report["condition_metrics"]["full_trajectory"][
                    "agent_provider_input_usage_coverage"
                ],
                1.0,
            )
            self.assertEqual(
                report["counts"]["missing_agent_provider_input_usage"],
                0,
            )
            self.assertEqual(
                comparison["mean_agent_provider_input_tokens_delta"],
                -600.0,
            )
            self.assertEqual(
                report["condition_metrics"]["full_trajectory"][
                    "mean_agent_provider_input_tokens_per_call"
                ],
                1000.0,
            )
            self.assertEqual(
                comparison[
                    "mean_agent_provider_input_tokens_per_call_delta"
                ],
                -600.0,
            )

            output = root / "analysis"
            write_live_matrix_report(report, output)
            self.assertTrue((output / "live_matrix_report.json").exists())
            self.assertEqual(
                len(
                    (output / "live_matrix_sessions.csv")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
                5,
            )

    def test_wall_clock_timeout_is_excluded_as_infrastructure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = _plan()
            for run in plan["runs"]:
                result_dir = root / "results" / run["save_to"]
                result_dir.mkdir(parents=True)
                timed_out = (
                    run["manager"] == "full_ours" and run["task_id"] == "0"
                )
                simulation = _simulation(
                    run["run_id"],
                    None if timed_out else 1.0,
                    "timeout" if timed_out else "user_stop",
                )
                (result_dir / "results.json").write_text(
                    json.dumps({"simulations": [simulation]}),
                    encoding="utf-8",
                )
                trace = root / run["trace_output_dir"] / run["run_id"] / "trace.json"
                trace.parent.mkdir(parents=True)
                trace.write_text(
                    json.dumps(
                        {
                            "session_id": run["run_id"],
                            "metadata": {"graph_validation_errors": []},
                            "nodes": [{"token_count": 5000}],
                            "edges": [],
                        }
                    ),
                    encoding="utf-8",
                )
                (trace.parent / "context_views.jsonl").write_text(
                    json.dumps(
                        {"selected_tokens": 1000, "compression_ratio": 0.5}
                    )
                    + "\n",
                    encoding="utf-8",
                )

            report = analyze_live_matrix(
                plan,
                project_root=root,
                results_root=root / "results",
                bootstrap_samples=100,
            )
            self.assertEqual(report["counts"]["infrastructure_errors"], 1)
            self.assertEqual(
                report["condition_metrics"]["full_ours"]["evaluated_sessions"], 1
            )
            self.assertEqual(
                report["paired_comparisons"]["full_ours"]["excluded_pairs"], 1
            )


if __name__ == "__main__":
    unittest.main()
