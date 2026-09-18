from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracegraph.benchmark.compression_audit.io import stable_digest
from tracegraph.benchmark.recursive_memory import (
    FailureExperience,
    MemoryEntry,
    MemoryEvolutionLedger,
    MemoryUpdateCandidate,
)
from tracegraph.benchmark.recursive_memory_replay import (
    execute_mini_replay_branch,
    load_mini_replay_plan,
    mini_replay_pair_from_plan,
    prepare_mini_replay_plan,
)


def _candidate():
    ledger = MemoryEvolutionLedger()
    experience = FailureExperience.create(
        source_task_id="source-task", source_checkpoint_id="source-checkpoint",
        source_prefix_hash="prefix", source_split="dev",
        failure_signature="known failure", failed_action_key="old",
        failure_reason="old failed", diagnosis="use safe",
        recovery_steps=("switch to safe",), successful_action_key="safe",
        applicability_conditions=(), evidence_event_ids=("event-1",),
        extractor_id="fixture", estimated_tokens=16,
    )
    entry = MemoryEntry.from_experience(experience, "round")
    candidate = MemoryUpdateCandidate.create(
        round_id="round", parent_revision_id=ledger.current_revision_id,
        operation="add", source_experience_ids=(experience.experience_id,),
        generator_id="fixture", rationale="replay the candidate", proposed_entry=entry,
    )
    return ledger.current, candidate


def _inputs(tmp_path):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    for name, value in {
        "config.snapshot.json": {},
        "tasks.snapshot.json": [{"id": "task"}],
        "plan.json": {},
    }.items():
        (prepared / name).write_text(json.dumps(value), encoding="utf-8")
    calibration = tmp_path / "calibration"
    calibration.mkdir()
    for name in ("report.json", "identity.json"):
        (calibration / name).write_text("{}", encoding="utf-8")
    boundary = tmp_path / "boundary"
    boundary.mkdir()
    checkpoint = {
        "n_calls": 2, "development_only": True,
        "independent_validation": False,
    }
    checkpoint["checkpoint_hash"] = stable_digest(checkpoint)
    checkpoint_path = boundary / "checkpoint.json"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    (boundary / "provider_ledger.jsonl").write_text(
        json.dumps({"valid_usage": True}) + "\n", encoding="utf-8"
    )
    return prepared, calibration, checkpoint_path


def test_replay_plan_freezes_parent_and_candidate_without_execution(tmp_path):
    parent, candidate = _candidate()
    prepared, calibration, checkpoint = _inputs(tmp_path)
    path = prepare_mini_replay_plan(
        candidate=candidate, parent=parent, prepared=prepared,
        checkpoint=checkpoint, calibration=calibration, output=tmp_path / "replay",
        task_id="task", model_id="model", method="full_history",
        budget=1024, memory_budget=256,
    )
    plan = load_mini_replay_plan(path)
    assert plan["parent_revision_id"] == parent.revision_id
    assert plan["preview_revision_id"] != parent.revision_id
    assert not (tmp_path / "replay" / "runs" / "baseline").exists()
    with pytest.raises(ValueError, match="explicit"):
        execute_mini_replay_branch(path, "baseline", tmp_path)

    (prepared / "tasks.snapshot.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="input changed"):
        load_mini_replay_plan(path)


def test_replay_plan_runs_one_named_branch_and_binds_completed_pair(
    tmp_path, monkeypatch
):
    parent, candidate = _candidate()
    prepared, calibration, checkpoint = _inputs(tmp_path)
    path = prepare_mini_replay_plan(
        candidate=candidate, parent=parent, prepared=prepared,
        checkpoint=checkpoint, calibration=calibration, output=tmp_path / "replay",
        task_id="task", model_id="model", method="full_history",
        budget=1024, memory_budget=256,
    )
    calls = []

    def fake_execute(*args, **kwargs):
        calls.append((args, kwargs))
        return {"task_success": True}

    monkeypatch.setattr(
        "tracegraph.benchmark.server_eval.mini_runner.execute_mini", fake_execute
    )
    result = execute_mini_replay_branch(
        path, "evolved", tmp_path, execute=True
    )
    assert result["task_success"]
    assert calls[0][1]["execute"] is True
    assert calls[0][1]["replay_parent_revision_id"] == parent.revision_id
    assert calls[0][1]["memory_budget"] == 256

    plan = load_mini_replay_plan(path)
    for branch in plan["branches"].values():
        Path(branch["output"]).mkdir(parents=True)
    pair = mini_replay_pair_from_plan(
        path, source_split="dev", known_failed_action_keys=("old",)
    )
    assert pair.checkpoint_model_calls == 2
    assert pair.baseline_prior_provider_rows == 1
