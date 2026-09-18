from __future__ import annotations

import copy

import pytest

from tracegraph.benchmark.failure_experience import ContinuationTrial
from tracegraph.benchmark.compression_audit.models import PrefixRecord
from tracegraph.benchmark.recursive_memory import (
    BoundedRecursiveMemoryLoop,
    EvolutionPolicy,
    FailureExperience,
    MemoryEntry,
    MemoryEvolutionLedger,
    MemoryUpdateCandidate,
    PostCommitGuard,
    ValidationEvidence,
    compare_revisions,
    load_ledger_snapshot,
    load_revision_snapshot,
    preview_candidate_revision,
    summarize_recursive_improvement,
    write_ledger_snapshot,
    write_revision_snapshot,
)


def _experience(name: str, *, split: str = "dev", tokens: int = 32) -> FailureExperience:
    return FailureExperience.create(
        source_task_id=f"task-{name}",
        source_checkpoint_id=f"checkpoint-{name}",
        source_prefix_hash=f"prefix-{name}",
        source_split=split,
        failure_signature=f"failure-{name}",
        failed_action_key=f"action-old-{name}",
        failure_reason=f"reason-{name}",
        diagnosis=f"diagnosis-{name}",
        recovery_steps=(f"repair-{name}",),
        successful_action_key=f"action-safe-{name}",
        applicability_conditions=(f"condition-{name}",),
        evidence_event_ids=(f"event-{name}-1", f"event-{name}-2"),
        extractor_id="fixture-extractor",
        estimated_tokens=tokens,
    )


def _candidate(ledger, experience, round_id, *, operation="add", target=None):
    entry = (MemoryEntry.from_experience(experience, round_id)
             if operation in {"add", "replace"} else None)
    return MemoryUpdateCandidate.create(
        round_id=round_id,
        parent_revision_id=ledger.current_revision_id,
        operation=operation,
        source_experience_ids=(experience.experience_id,),
        generator_id="fixture-generator",
        rationale="retain a validated failure-recovery experience",
        proposed_entry=entry,
        target_entry_id=target,
    )


def _validation(candidate, *, split="dev", replay=True, hidden=False):
    return ValidationEvidence(
        candidate_id=candidate.candidate_id,
        validator_id="fixture-validator",
        source_split=split,
        provenance_pass=True,
        replay_pass=replay,
        regression_pass=True,
        contradiction_free=True,
        safety_pass=True,
        hidden_evaluation_observed=hidden,
        evaluated_task_ids=("validation-task",),
        metrics={"success_delta": 0.25, "repeat_failure_delta": -0.5},
    )


def _trial(task, checkpoint, method, success, actions, failed, tokens):
    return ContinuationTrial(
        task_id=task,
        checkpoint_id=checkpoint,
        method_id=method,
        condition_id="structured_failure_episode",
        task_success=success,
        attempted_action_keys=tuple(actions),
        known_failed_action_keys=tuple(failed),
        reacquisition_calls=0 if success else 1,
        tool_calls=len(actions),
        model_calls=len(actions),
        input_tokens=tokens,
        output_tokens=10,
        observation_tokens=5,
        latency_seconds=float(len(actions)),
        steps_to_resolution=len(actions) if success else None,
    )


def test_validated_update_creates_an_immutable_revision_and_roundtrips(tmp_path):
    ledger = MemoryEvolutionLedger()
    experience = _experience("one")
    candidate = _candidate(ledger, experience, "round-1")
    result = ledger.finalize_round(
        round_id="round-1",
        experiences=(experience,),
        candidates=(candidate,),
        validations=(_validation(candidate),),
        policy=EvolutionPolicy(max_entries=2, max_total_tokens=100),
    )
    assert result["accepted_candidate_ids"] == [candidate.candidate_id]
    assert ledger.current.operation == "update"
    assert ledger.current.parent_revision_id == ledger.revision_order[0]
    assert [entry.experience.experience_id for entry in ledger.current.entries] == [
        experience.experience_id
    ]
    assert ledger.current.total_tokens == 32

    restored = MemoryEvolutionLedger.from_dict(copy.deepcopy(ledger.to_dict()))
    assert restored.to_dict() == ledger.to_dict()
    snapshot = write_ledger_snapshot(ledger, tmp_path)
    assert write_ledger_snapshot(ledger, tmp_path) == snapshot
    assert load_ledger_snapshot(snapshot).to_dict() == ledger.to_dict()

    envelope = snapshot.read_text(encoding="utf-8").replace("success_delta", "changed")
    snapshot.write_text(envelope, encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_ledger_snapshot(snapshot)


def test_candidate_preview_is_isolated_and_content_addressed(tmp_path):
    ledger = MemoryEvolutionLedger()
    experience = _experience("preview")
    candidate = _candidate(ledger, experience, "round-preview")
    preview = preview_candidate_revision(ledger.current, candidate)
    assert ledger.current.entries == ()
    assert preview.parent_revision_id == ledger.current_revision_id
    assert preview.accepted_candidate_ids == (candidate.candidate_id,)
    assert preview.entries[0].experience == experience

    snapshot = write_revision_snapshot(preview, tmp_path)
    assert write_revision_snapshot(preview, tmp_path) == snapshot
    assert load_revision_snapshot(snapshot) == preview


def test_failed_or_hidden_validation_is_recorded_but_never_committed():
    ledger = MemoryEvolutionLedger()
    experience = _experience("unsafe")
    candidate = _candidate(ledger, experience, "round-unsafe")
    result = ledger.finalize_round(
        round_id="round-unsafe",
        experiences=(experience,),
        candidates=(candidate,),
        validations=(_validation(candidate, replay=False, hidden=True),),
        policy=EvolutionPolicy(),
    )
    assert result["new_revision_id"] is None
    assert result["rejected_candidate_ids"] == [candidate.candidate_id]
    assert ledger.current.operation == "genesis"
    reasons = result["decisions"][0]["reasons"]
    assert "replay_failed" in reasons
    assert "hidden_evaluation_observed" in reasons


def test_test_split_cannot_authorize_updates_and_limits_are_atomic():
    ledger = MemoryEvolutionLedger()
    heldout = _experience("heldout", split="test")
    candidate = _candidate(ledger, heldout, "round-test")
    result = ledger.finalize_round(
        round_id="round-test",
        experiences=(heldout,),
        candidates=(candidate,),
        validations=(_validation(candidate),),
        policy=EvolutionPolicy(),
    )
    assert result["new_revision_id"] is None
    assert "experience_split_not_allowed" in result["decisions"][0]["reasons"]

    first, second = _experience("first", tokens=40), _experience("second", tokens=40)
    one = _candidate(ledger, first, "round-limit")
    two = _candidate(ledger, second, "round-limit")
    with pytest.raises(ValueError, match="entry limit"):
        ledger.finalize_round(
            round_id="round-limit", experiences=(first, second), candidates=(one, two),
            validations=(_validation(one), _validation(two)),
            policy=EvolutionPolicy(max_entries=1, max_total_tokens=100),
        )
    assert ledger.current.operation == "genesis"
    assert one.candidate_id not in ledger.candidates


def test_replace_then_rollback_preserves_the_audit_chain():
    ledger = MemoryEvolutionLedger()
    first = _experience("first")
    add = _candidate(ledger, first, "round-1")
    ledger.finalize_round(
        round_id="round-1", experiences=(first,), candidates=(add,),
        validations=(_validation(add),), policy=EvolutionPolicy(),
    )
    first_revision = ledger.current_revision_id
    target = ledger.current.entries[0].entry_id

    second = _experience("second")
    replace = _candidate(ledger, second, "round-2", operation="replace", target=target)
    ledger.finalize_round(
        round_id="round-2", experiences=(second,), candidates=(replace,),
        validations=(_validation(replace),), policy=EvolutionPolicy(),
    )
    assert ledger.current.entries[0].experience.experience_id == second.experience_id

    rollback = ledger.rollback(first_revision, round_id="round-3",
                               reason="post-commit regression")
    assert rollback.operation == "rollback"
    assert rollback.rollback_target_revision_id == first_revision
    assert rollback.entries[0].experience.experience_id == first.experience_id
    assert MemoryEvolutionLedger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()


def test_paired_revision_comparison_and_longitudinal_summary():
    ledger = MemoryEvolutionLedger()
    experience = _experience("one")
    candidate = _candidate(ledger, experience, "round-1")
    parent = ledger.current_revision_id
    ledger.finalize_round(
        round_id="round-1", experiences=(experience,), candidates=(candidate,),
        validations=(_validation(candidate),), policy=EvolutionPolicy(),
    )
    baseline = [
        _trial("task-1", "cp-1", "baseline", False, ("old", "safe"), ("old",), 200),
        _trial("task-2", "cp-2", "baseline", True, ("safe",), ("old",), 150),
    ]
    evolved = [
        _trial("task-1", "cp-1", "evolved", True, ("safe",), ("old",), 120),
        _trial("task-2", "cp-2", "evolved", True, ("safe",), ("old",), 130),
    ]
    comparison = compare_revisions(
        baseline, evolved, round_id="round-1", baseline_revision_id=parent,
        evolved_revision_id=ledger.current_revision_id,
    )
    assert comparison["success_rate_delta"] == 0.5
    assert comparison["repeated_failure_rate_delta"] == -0.5
    assert comparison["mean_token_delta"] == -50

    summary = summarize_recursive_improvement(ledger, [comparison])
    assert summary["accepted_update_count"] == 1
    assert summary["mean_success_rate_delta"] == 0.5
    assert summary["interpretation"].endswith("not_weight_level_rsi")


def test_revision_comparison_requires_identical_paired_units():
    left = [_trial("task-1", "cp-1", "baseline", False, ("old",), ("old",), 50)]
    right = [_trial("task-2", "cp-2", "evolved", True, ("safe",), ("old",), 50)]
    with pytest.raises(ValueError, match="identical"):
        compare_revisions(
            left, right, round_id="round", baseline_revision_id="before",
            evolved_revision_id="after",
        )


def test_bounded_loop_orchestrates_components_and_rolls_back_regression():
    prefix = PrefixRecord(
        prefix_id="loop-prefix", source_kind="synthetic", source_ref={}, split="dev",
        failure_family="fixture", task_domain="software", recoverability="R1",
        context_length="short", budget_tokens=512,
        events=({"event_id": "event-1", "step_id": 1, "kind": "observation",
                 "content": "fixture"},),
        messages=(), tool_schemas=(), environment_snapshot={},
    )

    class Extractor:
        extractor_id = "loop-extractor"

        def extract(self, source, *, task_id, checkpoint_id):
            return (FailureExperience.create(
                source_task_id=task_id,
                source_checkpoint_id=checkpoint_id,
                source_prefix_hash=source.prefix_hash,
                source_split=source.split,
                failure_signature="known failure",
                failed_action_key="old",
                failure_reason="old is incompatible",
                diagnosis="use safe",
                recovery_steps=("switch to safe",),
                successful_action_key="safe",
                applicability_conditions=(),
                evidence_event_ids=("event-1",),
                extractor_id=self.extractor_id,
                estimated_tokens=16,
            ),)

    class Generator:
        generator_id = "loop-generator"

        def propose(self, experiences, parent, *, round_id):
            entry = MemoryEntry.from_experience(experiences[0], round_id)
            return (MemoryUpdateCandidate.create(
                round_id=round_id,
                parent_revision_id=parent.revision_id,
                operation="add",
                source_experience_ids=(experiences[0].experience_id,),
                generator_id=self.generator_id,
                rationale="add validated experience",
                proposed_entry=entry,
            ),)

    class Validator:
        validator_id = "loop-validator"

        def validate(self, candidate, parent):
            assert parent.operation == "genesis"
            return ValidationEvidence(
                candidate_id=candidate.candidate_id,
                validator_id=self.validator_id,
                source_split="dev",
                provenance_pass=True,
                replay_pass=True,
                regression_pass=True,
                contradiction_free=True,
                safety_pass=True,
                hidden_evaluation_observed=False,
                evaluated_task_ids=("dev-task",),
            )

    loop = BoundedRecursiveMemoryLoop(
        extractor=Extractor(), generator=Generator(), validator=Validator(),
        policy=EvolutionPolicy(),
    )
    result = loop.run_update_round(
        prefix, task_id="task", checkpoint_id="checkpoint", round_id="round-1"
    )
    assert result["new_revision_id"] == loop.ledger.current_revision_id

    baseline = [_trial("task", "checkpoint", "baseline", True,
                       ("safe",), ("old",), 100)]
    regressed = [_trial("task", "checkpoint", "evolved", False,
                        ("old",), ("old",), 150)]
    guarded = loop.evaluate_and_guard(
        result, baseline, regressed,
        guard=PostCommitGuard(max_mean_token_delta=0),
        rollback_round_id="round-1-rollback",
    )
    assert not guarded["guard"]["pass"]
    assert guarded["rollback_revision_id"] == loop.ledger.current_revision_id
    assert loop.ledger.current.operation == "rollback"
    assert loop.ledger.current.entries == ()
