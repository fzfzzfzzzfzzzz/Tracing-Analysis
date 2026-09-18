from __future__ import annotations

import json

from tracegraph.benchmark.compression_audit.models import PrefixRecord
from tracegraph.benchmark.compression_audit.io import file_sha256, stable_digest
from tracegraph.benchmark.failure_experience import ContinuationTrial
from tracegraph.benchmark.recursive_memory import (
    EvolutionPolicy,
    FailureExperience,
    MemoryEntry,
    MemoryEvolutionLedger,
    MemoryRevision,
    PostCommitGuard,
    action_key,
    preview_candidate_revision,
    write_revision_snapshot,
)
from tracegraph.benchmark.server_eval.mini import MiniModel
from tracegraph.benchmark.recursive_memory_plugins import (
    CheckpointReplayValidator,
    ConservativeAddOnlyGenerator,
    DeterministicMemoryRenderer,
    DeterministicRecoveryExtractor,
    MiniArtifactReplayProvider,
    MiniReplayPair,
    ReplayObservation,
    continuation_trial_from_mini_run,
    policy_compatible_plugins,
)


def _prefix() -> PrefixRecord:
    return PrefixRecord(
        prefix_id="public-prefix", source_kind="synthetic", source_ref={}, split="dev",
        failure_family="fixture", task_domain="software", recoverability="R1",
        context_length="short", budget_tokens=1024,
        events=(
            {"event_id": "e1", "step_id": 1, "kind": "tool_call", "call_id": "c1",
             "tool_name": "run", "content": {"arguments": {"path": "x", "action": "old"}}},
            {"event_id": "e2", "step_id": 2, "kind": "error", "call_id": "c1",
             "tool_name": "run", "content": {"error": "gradient broke"}},
            {"event_id": "e3", "step_id": 3, "kind": "decision",
             "content": "switch to the safe action"},
            {"event_id": "e4", "step_id": 4, "kind": "tool_call", "call_id": "c2",
             "tool_name": "run", "content": {"arguments": {"path": "x", "action": "safe"}}},
            {"event_id": "e5", "step_id": 5, "kind": "observation", "call_id": "c2",
             "tool_name": "run", "content": {"success": True}},
        ),
        messages=(), tool_schemas=(), environment_snapshot={},
    )


def _trial(method: str, success: bool, actions: tuple[str, ...], failed: str) -> ContinuationTrial:
    return ContinuationTrial(
        task_id="task", checkpoint_id="checkpoint", method_id=method,
        condition_id="structured_failure_episode", task_success=success,
        attempted_action_keys=actions, known_failed_action_keys=(failed,),
        reacquisition_calls=0, tool_calls=len(actions), model_calls=len(actions),
        input_tokens=100, output_tokens=10, observation_tokens=5,
        latency_seconds=1, steps_to_resolution=len(actions) if success else None,
    )


def test_deterministic_extractor_generator_and_renderer_need_no_gold():
    prefix = _prefix()
    extractor = DeterministicRecoveryExtractor()
    experiences = extractor.extract(
        prefix, task_id="task", checkpoint_id="checkpoint"
    )
    assert len(experiences) == 1
    experience = experiences[0]
    assert experience.failure_signature == "gradient broke"
    assert experience.applicability_conditions == ('path="x"',)
    assert experience.evidence_event_ids == ("e1", "e2", "e3", "e4", "e5")
    assert experience.failed_action_key != experience.successful_action_key

    ledger = MemoryEvolutionLedger()
    generator = ConservativeAddOnlyGenerator()
    candidates = generator.propose(experiences, ledger.current, round_id="round-1")
    assert len(candidates) == 1 and candidates[0].operation == "add"

    class Provider:
        def observe(self, candidate, parent):
            return ReplayObservation(
                source_split="dev",
                baseline_trials=(_trial("baseline", False,
                                        (experience.failed_action_key,),
                                        experience.failed_action_key),),
                evolved_trials=(_trial("evolved", True,
                                       (experience.successful_action_key,),
                                       experience.failed_action_key),),
                replay_reproduced=True, provenance_pass=True,
                contradiction_free=True, safety_pass=True,
            )

    validator = CheckpointReplayValidator(Provider(), guard=PostCommitGuard())
    validation = validator.validate(candidates[0], ledger.current)
    assert validation.replay_pass and validation.regression_pass
    result = ledger.finalize_round(
        round_id="round-1", experiences=experiences, candidates=candidates,
        validations=(validation,), policy=EvolutionPolicy(),
    )
    assert result["new_revision_id"] == ledger.current_revision_id
    assert generator.propose(experiences, ledger.current, round_id="round-2") == ()

    rendered = DeterministicMemoryRenderer().render(
        ledger.current, query="gradient safe", budget_tokens=1024
    )
    assert rendered.send_eligible and len(rendered.records) == 1
    assert rendered.records[0]["content"]["source_evidence_event_ids"] == [
        "e1", "e2", "e3", "e4", "e5"
    ]
    manifest = policy_compatible_plugins(EvolutionPolicy())
    assert manifest["model_calls"] == 0 and not manifest["hidden_gold_observed"]


def test_add_only_generator_suppresses_ambiguous_recoveries():
    original = DeterministicRecoveryExtractor().extract(
        _prefix(), task_id="task", checkpoint_id="checkpoint"
    )[0]
    conflicting = FailureExperience.create(
        source_task_id="task-2", source_checkpoint_id="checkpoint-2",
        source_prefix_hash="other", source_split="dev",
        failure_signature=original.failure_signature,
        failed_action_key=original.failed_action_key,
        failure_reason=original.failure_reason,
        diagnosis="a different diagnosis",
        recovery_steps=("use a conflicting recovery",),
        successful_action_key="different-success",
        applicability_conditions=original.applicability_conditions,
        evidence_event_ids=("other-1",), extractor_id="fixture",
        estimated_tokens=10,
    )
    candidates = ConservativeAddOnlyGenerator().propose(
        (original, conflicting), MemoryEvolutionLedger().current, round_id="round"
    )
    assert candidates == ()


def test_renderer_refuses_to_slice_an_oversized_experience():
    prefix = _prefix()
    experience = DeterministicRecoveryExtractor().extract(
        prefix, task_id="task", checkpoint_id="checkpoint"
    )[0]
    ledger = MemoryEvolutionLedger()
    candidate = ConservativeAddOnlyGenerator().propose(
        (experience,), ledger.current, round_id="round"
    )[0]

    class Provider:
        def observe(self, candidate, parent):
            return ReplayObservation(
                source_split="dev",
                baseline_trials=(_trial("before", True, ("safe",), "old"),),
                evolved_trials=(_trial("after", True, ("safe",), "old"),),
                replay_reproduced=True, provenance_pass=True,
                contradiction_free=True, safety_pass=True,
            )

    validation = CheckpointReplayValidator(Provider()).validate(candidate, ledger.current)
    ledger.finalize_round(
        round_id="round", experiences=(experience,), candidates=(candidate,),
        validations=(validation,), policy=EvolutionPolicy(),
    )
    rendered = DeterministicMemoryRenderer(token_counter=lambda value: 100).render(
        ledger.current, query="gradient", budget_tokens=10
    )
    assert not rendered.send_eligible
    assert rendered.records == ()
    assert rendered.safety_reasons == ("no_complete_experience_fits_budget",)


def test_completed_mini_branch_normalizes_to_a_continuation_trial(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    command = "python -m pytest"
    (run / "identity.json").write_text(json.dumps({
        "task_id": "task", "restore_source": None,
    }), encoding="utf-8")
    (run / "task_result.json").write_text(json.dumps({
        "task_success": True, "model_calls": 2,
        "development_only": True, "independent_validation": False,
    }), encoding="utf-8")
    attempt = {"command": command, "cwd": "/repo", "container": "fixture", "stage": "tool"}
    (run / "tool_attempts.jsonl").write_text(
        json.dumps(attempt) + "\n", encoding="utf-8"
    )
    (run / "tool_results.jsonl").write_text(json.dumps({
        "attempt": attempt, "result": {"output": "passed", "returncode": 0},
    }) + "\n", encoding="utf-8")
    (run / "provider_ledger.jsonl").write_text(json.dumps({
        "valid_usage": True, "prompt_tokens": 50, "completion_tokens": 5,
        "latency_seconds": 0.5,
    }) + "\n", encoding="utf-8")
    (run / "memory_contexts.jsonl").write_text(json.dumps({
        "bundle": {"retrieval_usage": {"observation_tokens": 7}},
    }) + "\n", encoding="utf-8")
    failed_key = action_key({
        "kind": "tool_call", "tool_name": "bash", "content": command,
    })
    trial = continuation_trial_from_mini_run(
        run, task_id="task", checkpoint_id="checkpoint", method_id="candidate",
        condition_id="structured_failure_episode",
        known_failed_action_keys=(failed_key,),
    )
    assert trial.task_success and trial.model_calls == 2
    assert trial.input_tokens == 50 and trial.observation_tokens == 7
    assert trial.score()["repeated_failed_operation_count"] == 1


def test_mini_model_injects_revision_memory_from_a_reserved_budget(tmp_path):
    experience = DeterministicRecoveryExtractor().extract(
        _prefix(), task_id="task", checkpoint_id="checkpoint"
    )[0]
    revision = MemoryRevision.create(
        parent_revision_id=None, round_id="genesis", operation="genesis",
        entries=(MemoryEntry.from_experience(experience, "genesis"),),
    )
    calls = []

    class Ledger:
        config = {"model": "fixture", "action_transport": "json_schema"}

        def call(self, body, **kwargs):
            calls.append(body)
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "thought": "use the recorded recovery", "command": "echo safe",
            })}}]}

    task = {"id": "task", "task": "repair gradient failure"}
    model = MiniModel(
        Ledger(), None, task, "full_history", 1024, tmp_path,
        revision_memory=revision, revision_renderer=DeterministicMemoryRenderer(),
        revision_budget=512,
    )
    response = model.query([
        {"role": "system", "content": "system"},
        {"role": "user", "content": "repair gradient failure"},
    ])
    assert response["extra"]["actions"] == [{"command": "echo safe"}]
    injected = json.loads(calls[0]["messages"][2]["content"].split("\n", 1)[1])
    assert injected["failure_experience_memory"][0]["kind"] == "failure_experience"
    assert "execution_history" not in injected
    assert model.serialize()["execution_history_budget"] == 512
    assert model.serialize()["revision_id"] == revision.revision_id


def test_completed_mini_pair_is_identity_checked_and_validates_candidate(tmp_path):
    experience = DeterministicRecoveryExtractor().extract(
        _prefix(), task_id="task", checkpoint_id="source-checkpoint"
    )[0]
    ledger = MemoryEvolutionLedger()
    candidate = ConservativeAddOnlyGenerator().propose(
        (experience,), ledger.current, round_id="round"
    )[0]
    preview = preview_candidate_revision(ledger.current, candidate)
    sources = tmp_path / "memory-sources"
    baseline_source = write_revision_snapshot(ledger.current, sources)
    evolved_source = write_revision_snapshot(preview, sources)

    checkpoint = {
        "n_calls": 1,
        "development_only": True,
        "independent_validation": False,
    }
    checkpoint["checkpoint_hash"] = stable_digest(checkpoint)
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    def write_run(path, *, revision_id, source, success, command):
        path.mkdir()
        identity = {
            "task_id": "task", "plan_hash": "plan", "model_id": "model",
            "method": "full_history", "budget": 1024,
            "revision_memory_budget": 256, "revision_id": revision_id,
            "memory_source_sha256": file_sha256(source),
            "restore_source": str(checkpoint_path),
        }
        (path / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
        (path / "task_result.json").write_text(json.dumps({
            "task_success": success, "model_calls": 2,
            "development_only": True, "independent_validation": False,
        }), encoding="utf-8")
        attempt = {
            "command": command, "cwd": "/repo", "container": "fixture",
            "stage": "tool",
        }
        (path / "tool_attempts.jsonl").write_text(
            json.dumps(attempt) + "\n", encoding="utf-8"
        )
        (path / "tool_results.jsonl").write_text(json.dumps({
            "attempt": attempt,
            "result": {"output": "ok" if success else "failed", "returncode": 0},
        }) + "\n", encoding="utf-8")
        (path / "provider_ledger.jsonl").write_text(json.dumps({
            "valid_usage": True, "prompt_tokens": 10, "completion_tokens": 1,
            "latency_seconds": 0.1,
        }) + "\n", encoding="utf-8")

    baseline, evolved = tmp_path / "baseline", tmp_path / "evolved"
    write_run(
        baseline, revision_id=ledger.current.revision_id, source=baseline_source,
        success=False, command="old",
    )
    write_run(
        evolved, revision_id=preview.revision_id, source=evolved_source,
        success=True, command="safe",
    )
    failed_key = action_key({
        "kind": "tool_call", "tool_name": "bash", "content": "old",
    })
    pair = MiniReplayPair(
        baseline_run=baseline, evolved_run=evolved, task_id="task",
        checkpoint_id=checkpoint["checkpoint_hash"], source_split="dev",
        known_failed_action_keys=(failed_key,), checkpoint_model_calls=1,
    )
    provider = MiniArtifactReplayProvider({candidate.candidate_id: pair})
    evidence = CheckpointReplayValidator(provider).validate(candidate, ledger.current)
    assert evidence.replay_pass and evidence.regression_pass
    assert evidence.metrics["success_rate_delta"] == 1.0

    changed = json.loads((evolved / "identity.json").read_text(encoding="utf-8"))
    changed["revision_memory_budget"] = 128
    (evolved / "identity.json").write_text(json.dumps(changed), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="revision_memory_budget"):
        provider.observe(candidate, ledger.current)
