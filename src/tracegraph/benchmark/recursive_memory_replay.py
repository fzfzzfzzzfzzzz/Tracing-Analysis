"""Frozen mini-run replay plans for candidate memory revisions.

Preparing a plan is offline.  Executing either branch remains an explicit action and
delegates to the existing ``server_eval.mini_runner`` live gates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .compression_audit.io import file_sha256, load_jsonl, stable_digest
from .recursive_memory import (
    MemoryRevision,
    MemoryUpdateCandidate,
    load_revision_snapshot,
    preview_candidate_revision,
    write_revision_snapshot,
)
from .recursive_memory_plugins import MiniReplayPair


PLAN_SCHEMA_VERSION = "recursive_memory_mini_replay_plan_v1"


def _frozen_file(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"replay input is unavailable: {resolved}")
    return {"path": str(resolved), "sha256": file_sha256(resolved)}


def _checkpoint(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    body = {key: item for key, item in value.items() if key != "checkpoint_hash"}
    if value.get("checkpoint_hash") != stable_digest(body):
        raise ValueError("replay checkpoint digest mismatch")
    if (value.get("development_only") is not True
            or value.get("independent_validation") is not False):
        raise ValueError("replay checkpoint lacks development provenance")
    if type(value.get("n_calls")) is not int or value["n_calls"] < 0:
        raise ValueError("replay checkpoint has an invalid model-call boundary")
    return value


def prepare_mini_replay_plan(
    *,
    candidate: MemoryUpdateCandidate,
    parent: MemoryRevision,
    prepared: Path,
    checkpoint: Path,
    calibration: Path,
    output: Path,
    task_id: str,
    model_id: str,
    method: str,
    budget: int,
    memory_budget: int,
) -> Path:
    """Freeze one fair parent-vs-candidate mini replay without executing it."""

    if output.exists():
        raise ValueError("mini replay plan output must be new")
    if type(budget) is not int or type(memory_budget) is not int:
        raise ValueError("replay budgets must be integers")
    if not 0 < memory_budget < budget:
        raise ValueError("memory budget must be positive and below the total budget")
    for name, value in {
        "task_id": task_id, "model_id": model_id, "method": method,
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
    required = {
        "prepared_config": prepared / "config.snapshot.json",
        "prepared_tasks": prepared / "tasks.snapshot.json",
        "prepared_plan": prepared / "plan.json",
        "calibration_report": calibration / "report.json",
        "calibration_identity": calibration / "identity.json",
        "checkpoint": checkpoint,
    }
    frozen_inputs = {name: _frozen_file(path) for name, path in required.items()}
    checkpoint_value = _checkpoint(Path(frozen_inputs["checkpoint"]["path"]))
    tasks = json.loads(Path(frozen_inputs["prepared_tasks"]["path"]).read_text(
        encoding="utf-8"
    ))
    if not any(item.get("id") == task_id for item in tasks):
        raise ValueError("replay task is absent from the prepared mini plan")

    preview = preview_candidate_revision(parent, candidate)
    output.mkdir(parents=True)
    sources = output / "memory-sources"
    baseline_source = write_revision_snapshot(parent, sources)
    evolved_source = write_revision_snapshot(preview, sources)
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "candidate": candidate.to_dict(),
        "parent_revision_id": parent.revision_id,
        "preview_revision_id": preview.revision_id,
        "checkpoint_id": checkpoint_value["checkpoint_hash"],
        "task_id": task_id,
        "model_id": model_id,
        "method": method,
        "budget": budget,
        "memory_budget": memory_budget,
        "frozen_inputs": frozen_inputs,
        "branches": {
            "baseline": {
                "revision_id": parent.revision_id,
                "memory_revision": _frozen_file(baseline_source),
                "output": str((output / "runs" / "baseline").resolve()),
            },
            "evolved": {
                "revision_id": preview.revision_id,
                "memory_revision": _frozen_file(evolved_source),
                "output": str((output / "runs" / "evolved").resolve()),
            },
        },
        "execution": "explicitly run exactly one named branch per invocation",
        "development_only": True,
        "independent_validation": False,
    }
    digest = stable_digest(payload)
    envelope = {
        "schema_version": "recursive_memory_mini_replay_plan_envelope_v1",
        "plan_digest": digest,
        "plan": payload,
    }
    path = output / f"mini-replay-plan-{digest[:16]}.json"
    path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def load_mini_replay_plan(path: Path) -> Mapping[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if envelope.get("schema_version") != "recursive_memory_mini_replay_plan_envelope_v1":
        raise ValueError("unsupported mini replay plan envelope")
    plan = envelope.get("plan")
    if not isinstance(plan, Mapping) or plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported mini replay plan")
    if stable_digest(plan) != envelope.get("plan_digest"):
        raise ValueError("mini replay plan digest mismatch")
    for item in plan["frozen_inputs"].values():
        source = Path(item["path"])
        if not source.is_file() or file_sha256(source) != item["sha256"]:
            raise ValueError("a frozen mini replay input changed")
    checkpoint = _checkpoint(Path(plan["frozen_inputs"]["checkpoint"]["path"]))
    if checkpoint["checkpoint_hash"] != plan["checkpoint_id"]:
        raise ValueError("mini replay checkpoint identity changed")

    candidate = MemoryUpdateCandidate.from_dict(plan["candidate"])
    baseline = load_revision_snapshot(Path(
        plan["branches"]["baseline"]["memory_revision"]["path"]
    ))
    evolved = load_revision_snapshot(Path(
        plan["branches"]["evolved"]["memory_revision"]["path"]
    ))
    for role, revision in (("baseline", baseline), ("evolved", evolved)):
        item = plan["branches"][role]["memory_revision"]
        if file_sha256(Path(item["path"])) != item["sha256"]:
            raise ValueError("a mini replay memory source changed")
        if revision.revision_id != plan["branches"][role]["revision_id"]:
            raise ValueError("a mini replay revision identity changed")
    if baseline.revision_id != plan["parent_revision_id"]:
        raise ValueError("mini replay baseline is not the parent revision")
    if preview_candidate_revision(baseline, candidate) != evolved:
        raise ValueError("mini replay evolved revision is not the candidate preview")
    return plan


def execute_mini_replay_branch(
    plan_path: Path,
    role: str,
    workspace: Path,
    *,
    execute: bool = False,
) -> Mapping[str, Any]:
    """Execute one frozen branch, retaining the existing explicit live gate."""

    if not execute:
        raise ValueError("mini replay branch execution requires explicit execute=True")
    plan = load_mini_replay_plan(plan_path)
    if role not in {"baseline", "evolved"}:
        raise ValueError("mini replay role must be baseline or evolved")
    branch = plan["branches"][role]
    from .server_eval.mini_runner import execute_mini

    return execute_mini(
        Path(plan["frozen_inputs"]["prepared_plan"]["path"]).parent,
        Path(branch["output"]),
        workspace,
        task_id=plan["task_id"],
        model_id=plan["model_id"],
        method=plan["method"],
        budget=plan["budget"],
        execute=True,
        restore=Path(plan["frozen_inputs"]["checkpoint"]["path"]),
        calibration=Path(plan["frozen_inputs"]["calibration_report"]["path"]).parent,
        memory_revision=Path(branch["memory_revision"]["path"]),
        memory_budget=plan["memory_budget"],
        replay_parent_revision_id=plan["parent_revision_id"],
    )


def mini_replay_pair_from_plan(
    plan_path: Path,
    *,
    source_split: str,
    known_failed_action_keys: Sequence[str],
) -> MiniReplayPair:
    """Bind two completed outputs back to the candidate-indexed provider."""

    plan = load_mini_replay_plan(plan_path)
    checkpoint_path = Path(plan["frozen_inputs"]["checkpoint"]["path"])
    source_ledger = checkpoint_path.parent / "provider_ledger.jsonl"
    prior_rows = len(load_jsonl(source_ledger)) if source_ledger.exists() else 0
    return MiniReplayPair(
        baseline_run=Path(plan["branches"]["baseline"]["output"]),
        evolved_run=Path(plan["branches"]["evolved"]["output"]),
        task_id=plan["task_id"],
        checkpoint_id=plan["checkpoint_id"],
        source_split=source_split,
        known_failed_action_keys=tuple(known_failed_action_keys),
        checkpoint_model_calls=_checkpoint(checkpoint_path)["n_calls"],
        baseline_prior_provider_rows=prior_rows,
        evolved_prior_provider_rows=prior_rows,
    )
