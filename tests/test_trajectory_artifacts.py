from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracegraph.trajectory_artifacts import (
    EvaluationConfig,
    TrajectoryArtifactStore,
    merge_rewards_into_results,
)


class TrajectoryArtifactTests(unittest.TestCase):
    @staticmethod
    def _generation(simulation_id: str = "sim-1") -> dict:
        return {
            "simulation_id": simulation_id,
            "simulation": {
                "id": simulation_id,
                "task_id": "task-1",
                "termination_reason": "agent_stop",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {
                        "role": "assistant",
                        "content": "done",
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                    },
                ],
                "reward_info": None,
            },
            "task": {"id": "task-1", "user_scenario": "test"},
            "environment_summary": {
                "domain": "mock",
                "agent_db_hash": "abc",
                "user_db_hash": "def",
            },
            "usage": {"agent_input_tokens": 10, "agent_output_tokens": 2},
            "provenance": {
                "agent_model": "local-test",
                "user_model": "local-test",
                "mode": "half_duplex",
            },
        }

    def test_generation_is_immutable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryArtifactStore(directory)
            values = self._generation()
            first = store.persist_generation(**values)
            before = (Path(directory) / "sim-1" / "generation.json").read_bytes()
            second = store.persist_generation(**values)
            self.assertEqual(first["generation_sha256"], second["generation_sha256"])
            self.assertEqual(before, (Path(directory) / "sim-1" / "generation.json").read_bytes())
            conflict = self._generation()
            conflict["simulation"]["messages"][0]["content"] = "changed"
            with self.assertRaisesRegex(ValueError, "conflicting generation"):
                store.persist_generation(**conflict)

    def test_evaluator_failure_keeps_conversation_and_retry_does_not_generate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryArtifactStore(directory)
            generation = store.persist_generation(**self._generation())
            generation_path = Path(directory) / "sim-1" / "generation.json"
            before = generation_path.read_bytes()
            config = EvaluationConfig(
                model="local-evaluator",
                args={"temperature": 0},
                evaluation_type="all",
                json_mode="strict",
            )

            calls = {"evaluate": 0, "generate": 0}

            def failing(artifact, recorder):
                calls["evaluate"] += 1
                self.assertEqual(artifact["generation_sha256"], generation["generation_sha256"])
                recorder.record_raw("not-json")
                raise ValueError("evaluator parse failed")

            with self.assertRaisesRegex(ValueError, "parse failed"):
                store.run_offline_evaluation("sim-1", config, failing)
            self.assertEqual(before, generation_path.read_bytes())
            first_result = json.loads(
                (
                    Path(directory)
                    / "sim-1"
                    / "evaluation_attempts"
                    / "attempt_0001"
                    / "result.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(first_result["status"], "evaluation_error")
            self.assertEqual(first_result["raw_response_count"], 1)

            def successful(artifact, recorder):
                calls["evaluate"] += 1
                recorder.record_raw('{"reward": 1}')
                return {"reward_info": {"reward": 1.0}, "note": "offline retry"}

            merged = store.run_offline_evaluation("sim-1", config, successful)
            self.assertEqual(merged["simulation"]["reward_info"]["reward"], 1.0)
            self.assertEqual(calls, {"evaluate": 2, "generate": 0})
            self.assertEqual(before, generation_path.read_bytes())
            summary = store.summary()
            self.assertEqual(summary["counts"]["generation_complete"], 1)
            self.assertEqual(summary["counts"]["evaluation_error"], 1)
            self.assertEqual(summary["counts"]["evaluation_complete"], 1)
            self.assertEqual(summary["counts"]["merged"], 1)

    def test_hash_tampering_and_secret_config_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryArtifactStore(directory)
            store.persist_generation(**self._generation())
            generation_path = Path(directory) / "sim-1" / "generation.json"
            payload = json.loads(generation_path.read_text(encoding="utf-8"))
            payload["conversation"][0]["content"] = "tampered"
            generation_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                store.load_generation("sim-1")

        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryArtifactStore(directory)
            values = self._generation()
            values["provenance"]["api_key"] = "must-not-persist"
            with self.assertRaisesRegex(ValueError, "sensitive key"):
                store.persist_generation(**values)

    def test_generation_error_is_counted_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryArtifactStore(directory)
            store.record_generation_error("sim-error", RuntimeError("agent failed"))
            summary = store.summary()
            self.assertEqual(summary["counts"]["generation_error"], 1)
            self.assertEqual(summary["counts"]["evaluation_error"], 0)

    def test_materializes_reward_by_id_without_overwriting_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryArtifactStore(directory)
            values = self._generation()
            store.persist_generation(**values)
            config = EvaluationConfig(
                model="local-evaluator",
                args={"temperature": 0},
                evaluation_type="all",
                json_mode="strict",
            )

            def evaluator(artifact, recorder):
                return {"reward_info": {"reward": 0.75}}

            store.run_offline_evaluation("sim-1", config, evaluator)
            source = {"info": {"run": "generation-only"}, "simulations": [values["simulation"]]}
            output, audit = merge_rewards_into_results(source, store)
            self.assertEqual(output["simulations"][0]["reward_info"]["reward"], 0.75)
            self.assertEqual(
                output["simulations"][0]["messages"],
                source["simulations"][0]["messages"],
            )
            self.assertTrue(audit["complete"])
            self.assertEqual(audit["merged_count"], 1)
            self.assertIsNone(source["simulations"][0]["reward_info"])


if __name__ == "__main__":
    unittest.main()
