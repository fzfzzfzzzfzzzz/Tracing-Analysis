"""Run zero-API fault injection over the Phase 4 trajectory/evaluator protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from tracegraph.trajectory_artifacts import EvaluationConfig, TrajectoryArtifactStore


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = TrajectoryArtifactStore(root)
        simulation = {
            "id": "phase4-audit-simulation",
            "task_id": "audit-task",
            "termination_reason": "agent_stop",
            "messages": [
                {"role": "user", "content": "audit"},
                {
                    "role": "assistant",
                    "content": "complete",
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1},
                },
            ],
            "reward_info": None,
        }
        generation = store.persist_generation(
            simulation_id=simulation["id"],
            simulation=simulation,
            task={"id": "audit-task"},
            environment_summary={"domain": "mock", "agent_db_hash": "audit"},
            usage={"agent_input_tokens": 4, "agent_output_tokens": 1},
            provenance={"agent_model": "deterministic-audit", "mode": "half_duplex"},
        )
        generation_path = root / simulation["id"] / "generation.json"
        before = generation_path.read_bytes()
        config = EvaluationConfig(
            model="deterministic-audit",
            args={"temperature": 0},
            evaluation_type="all",
            json_mode="strict",
        )

        def fail_once(artifact, recorder):
            recorder.record_raw("malformed evaluator response")
            raise ValueError("injected evaluator failure")

        error_observed = False
        try:
            store.run_offline_evaluation(simulation["id"], config, fail_once)
        except ValueError as error:
            error_observed = "injected evaluator failure" in str(error)
        unchanged_after_failure = before == generation_path.read_bytes()

        def succeed(artifact, recorder):
            recorder.record_raw('{"reward": 1.0}')
            return {"reward_info": {"reward": 1.0}, "source": "offline_retry"}

        merged = store.run_offline_evaluation(simulation["id"], config, succeed)
        unchanged_after_success = before == generation_path.read_bytes()
        first_attempt = root / simulation["id"] / "evaluation_attempts" / "attempt_0001"
        second_attempt = root / simulation["id"] / "evaluation_attempts" / "attempt_0002"
        summary = store.summary()
        conflict_failed_closed = False
        conflicting = dict(simulation)
        conflicting["messages"] = [{"role": "user", "content": "changed"}]
        try:
            store.persist_generation(
                simulation_id=simulation["id"],
                simulation=conflicting,
                task={"id": "audit-task"},
                environment_summary={"domain": "mock", "agent_db_hash": "audit"},
                usage={"agent_input_tokens": 4, "agent_output_tokens": 1},
                provenance={"agent_model": "deterministic-audit", "mode": "half_duplex"},
            )
        except ValueError:
            conflict_failed_closed = True

        checks = {
            "generation_marked_before_evaluation": (
                root / simulation["id"] / "generation_complete.json"
            ).is_file(),
            "generation_hash_verified": store.load_generation(simulation["id"])["generation_sha256"]
            == generation["generation_sha256"],
            "evaluator_failure_observed": error_observed,
            "raw_failure_response_persisted": (first_attempt / "raw_response_0001.txt").is_file(),
            "failure_did_not_change_generation": unchanged_after_failure,
            "offline_retry_raw_response_persisted": (
                second_attempt / "raw_response_0001.txt"
            ).is_file(),
            "offline_retry_merged_reward": merged["simulation"]["reward_info"]["reward"] == 1.0,
            "success_did_not_change_generation": unchanged_after_success,
            "generation_and_evaluation_errors_separate": (
                summary["counts"]["generation_error"] == 0
                and summary["counts"]["evaluation_error"] == 1
                and summary["counts"]["evaluation_complete"] == 1
            ),
            "conflicting_generation_failed_closed": conflict_failed_closed,
        }

    report = {
        "schema_version": "1.0",
        "audit": "trajectory_generation_evaluator_decoupling_fault_injection",
        "external_api_calls": 0,
        "checks": checks,
        "passed": all(checks.values()),
        "source_hashes": {
            "trajectory_artifacts.py": _sha256(
                project_root / "src" / "tracegraph" / "trajectory_artifacts.py"
            ),
            "tau3_offline.py": _sha256(project_root / "src" / "tracegraph" / "tau3_offline.py"),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
