"""Immutable generation artifacts and retryable offline evaluation records.

The store makes a completed trajectory durable before any evaluator can run.
Evaluation attempts are append-only and rewards are merged by simulation id and
generation hash, so an evaluator failure cannot erase or silently replace the
agent/user conversation.
"""

from __future__ import annotations

import json
import os
import re
import traceback
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .schema import utc_now


GENERATION_SCHEMA_VERSION = "1.0"
EVALUATION_SCHEMA_VERSION = "1.0"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_SENSITIVE_PARTS = ("api_key", "secret", "credential", "password", "access_token")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_simulation_id(simulation_id: str) -> str:
    value = str(simulation_id)
    if not _SAFE_ID.fullmatch(value):
        raise ValueError("simulation_id must use 1-200 letters, digits, dot, dash, or underscore")
    return value


def _reject_secrets(value: Any, path: str = "config") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_PARTS):
                raise ValueError(f"sensitive key is forbidden in artifact: {path}.{key}")
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _without_hash(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    model: str
    args: Mapping[str, Any]
    evaluation_type: str
    json_mode: str
    evaluator_version: str = "tau3_offline_v1"

    def to_dict(self) -> dict[str, Any]:
        value = {
            "model": self.model,
            "args": dict(self.args),
            "evaluation_type": self.evaluation_type,
            "json_mode": self.json_mode,
            "evaluator_version": self.evaluator_version,
        }
        if not self.model or not self.evaluation_type or not self.json_mode:
            raise ValueError("model, evaluation_type, and json_mode are required")
        _reject_secrets(value, "evaluator")
        return value


class EvaluationRecorder:
    """Append raw evaluator responses to one immutable attempt directory."""

    def __init__(self, attempt_dir: Path):
        self.attempt_dir = attempt_dir
        self.raw_response_count = 0

    def record_raw(self, response: str | bytes) -> Path:
        self.raw_response_count += 1
        suffix = "bin" if isinstance(response, bytes) else "txt"
        path = self.attempt_dir / (f"raw_response_{self.raw_response_count:04d}.{suffix}")
        if path.exists():
            raise ValueError(f"raw evaluator response already exists: {path}")
        if isinstance(response, bytes):
            path.write_bytes(response)
        else:
            path.write_text(str(response), encoding="utf-8")
        return path


class TrajectoryArtifactStore:
    """Filesystem store with immutable generation and append-only evaluation."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def simulation_dir(self, simulation_id: str) -> Path:
        return self.root / _validate_simulation_id(simulation_id)

    def persist_generation(
        self,
        *,
        simulation_id: str,
        simulation: Mapping[str, Any],
        task: Mapping[str, Any],
        environment_summary: Mapping[str, Any],
        usage: Mapping[str, Any],
        provenance: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist and verify a generation artifact before marking it complete."""

        simulation_id = _validate_simulation_id(simulation_id)
        simulation_value = dict(simulation)
        if str(simulation_value.get("id") or "") != simulation_id:
            raise ValueError("simulation payload id does not match simulation_id")
        if simulation_value.get("reward_info") is not None:
            raise ValueError("generation artifact must be persisted before reward evaluation")
        conversation = simulation_value.get("messages")
        if conversation is None:
            conversation = simulation_value.get("ticks")
        if not isinstance(conversation, list):
            raise ValueError("generation artifact requires a messages or ticks list")
        _reject_secrets(provenance, "provenance")
        component_hashes = {
            "simulation_sha256": sha256_json(simulation_value),
            "conversation_sha256": sha256_json(conversation),
            "task_sha256": sha256_json(task),
            "environment_summary_sha256": sha256_json(environment_summary),
            "usage_sha256": sha256_json(usage),
        }
        artifact = {
            "schema_version": GENERATION_SCHEMA_VERSION,
            "status": "generation_complete",
            "simulation_id": simulation_id,
            "persisted_at": utc_now(),
            "simulation": simulation_value,
            "task": dict(task),
            "conversation": conversation,
            "environment_summary": dict(environment_summary),
            "usage": dict(usage),
            "provenance": dict(provenance),
            "component_hashes": component_hashes,
        }
        artifact["generation_sha256"] = sha256_json(artifact)
        directory = self.simulation_dir(simulation_id)
        generation_path = directory / "generation.json"
        marker_path = directory / "generation_complete.json"
        if generation_path.exists() or marker_path.exists():
            existing = self.load_generation(simulation_id)
            if existing["generation_sha256"] != artifact["generation_sha256"]:
                # persisted_at makes a second construction different. Compare the
                # scientific payload so identical retries remain idempotent.
                comparable_fields = (
                    "simulation",
                    "task",
                    "environment_summary",
                    "usage",
                    "provenance",
                    "component_hashes",
                )
                if any(existing[field] != artifact[field] for field in comparable_fields):
                    raise ValueError(
                        "conflicting generation artifact already exists for simulation_id"
                    )
            return existing

        _atomic_write_json(generation_path, artifact)
        persisted = _read_object(generation_path)
        self._validate_generation_payload(persisted)
        marker = {
            "schema_version": GENERATION_SCHEMA_VERSION,
            "status": "generation_complete",
            "simulation_id": simulation_id,
            "generation_sha256": persisted["generation_sha256"],
            "marked_at": utc_now(),
        }
        _atomic_write_json(marker_path, marker)
        return persisted

    def record_generation_error(
        self,
        simulation_id: str,
        error: BaseException,
        *,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        simulation_id = _validate_simulation_id(simulation_id)
        _reject_secrets(provenance or {}, "provenance")
        path = self.simulation_dir(simulation_id) / "generation_error.json"
        value = {
            "schema_version": GENERATION_SCHEMA_VERSION,
            "status": "generation_error",
            "simulation_id": simulation_id,
            "recorded_at": utc_now(),
            "error_type": type(error).__name__,
            "error": str(error),
            "provenance": dict(provenance or {}),
        }
        if path.exists():
            return _read_object(path)
        _atomic_write_json(path, value)
        return value

    def _validate_generation_payload(self, artifact: Mapping[str, Any]) -> None:
        if artifact.get("schema_version") != GENERATION_SCHEMA_VERSION:
            raise ValueError("unsupported generation artifact schema")
        if artifact.get("status") != "generation_complete":
            raise ValueError("generation artifact is not complete")
        simulation_id = _validate_simulation_id(str(artifact.get("simulation_id") or ""))
        simulation = artifact.get("simulation")
        if not isinstance(simulation, Mapping) or str(simulation.get("id") or "") != simulation_id:
            raise ValueError("generation simulation id mismatch")
        expected_artifact_hash = sha256_json(_without_hash(artifact, "generation_sha256"))
        if artifact.get("generation_sha256") != expected_artifact_hash:
            raise ValueError("generation artifact SHA-256 mismatch")
        components = artifact.get("component_hashes")
        if not isinstance(components, Mapping):
            raise ValueError("generation component hashes are missing")
        expected_components = {
            "simulation_sha256": sha256_json(simulation),
            "conversation_sha256": sha256_json(artifact.get("conversation")),
            "task_sha256": sha256_json(artifact.get("task")),
            "environment_summary_sha256": sha256_json(artifact.get("environment_summary")),
            "usage_sha256": sha256_json(artifact.get("usage")),
        }
        if dict(components) != expected_components:
            raise ValueError("generation component SHA-256 mismatch")

    def load_generation(self, simulation_id: str) -> dict[str, Any]:
        directory = self.simulation_dir(simulation_id)
        generation_path = directory / "generation.json"
        marker_path = directory / "generation_complete.json"
        if not generation_path.is_file() or not marker_path.is_file():
            raise ValueError(f"generation is not complete for {simulation_id}")
        artifact = _read_object(generation_path)
        self._validate_generation_payload(artifact)
        marker = _read_object(marker_path)
        if (
            marker.get("status") != "generation_complete"
            or marker.get("simulation_id") != simulation_id
            or marker.get("generation_sha256") != artifact["generation_sha256"]
        ):
            raise ValueError("generation completion marker does not match artifact")
        return artifact

    def _next_attempt_dir(self, simulation_id: str) -> Path:
        root = self.simulation_dir(simulation_id) / "evaluation_attempts"
        root.mkdir(parents=True, exist_ok=True)
        existing = [
            int(path.name.split("_", 1)[1])
            for path in root.glob("attempt_[0-9][0-9][0-9][0-9]")
            if path.is_dir()
        ]
        return root / f"attempt_{max(existing, default=0) + 1:04d}"

    def run_offline_evaluation(
        self,
        simulation_id: str,
        config: EvaluationConfig,
        evaluator: Callable[[Mapping[str, Any], EvaluationRecorder], Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Run one evaluator attempt against an already-frozen generation."""

        generation = self.load_generation(simulation_id)
        config_value = config.to_dict()
        attempt_dir = self._next_attempt_dir(simulation_id)
        attempt_dir.mkdir(parents=False, exist_ok=False)
        manifest = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "status": "evaluation_started",
            "simulation_id": simulation_id,
            "generation_sha256": generation["generation_sha256"],
            "started_at": utc_now(),
            "evaluator": config_value,
        }
        manifest["manifest_sha256"] = sha256_json(manifest)
        _atomic_write_json(attempt_dir / "attempt_manifest.json", manifest)
        recorder = EvaluationRecorder(attempt_dir)
        try:
            evaluation = evaluator(generation, recorder)
            if not isinstance(evaluation, Mapping):
                raise TypeError("offline evaluator must return a mapping")
            reward_info = evaluation.get("reward_info")
            if not isinstance(reward_info, Mapping) or reward_info.get("reward") is None:
                raise ValueError("offline evaluator result requires reward_info.reward")
            result = {
                "schema_version": EVALUATION_SCHEMA_VERSION,
                "status": "evaluation_complete",
                "simulation_id": simulation_id,
                "generation_sha256": generation["generation_sha256"],
                "completed_at": utc_now(),
                "raw_response_count": recorder.raw_response_count,
                "evaluation": dict(evaluation),
            }
            result["evaluation_sha256"] = sha256_json(result)
        except Exception as error:
            result = {
                "schema_version": EVALUATION_SCHEMA_VERSION,
                "status": "evaluation_error",
                "simulation_id": simulation_id,
                "generation_sha256": generation["generation_sha256"],
                "completed_at": utc_now(),
                "raw_response_count": recorder.raw_response_count,
                "error_type": type(error).__name__,
                "error": str(error),
                "traceback": traceback.format_exc(),
            }
            result["evaluation_sha256"] = sha256_json(result)
            _atomic_write_json(attempt_dir / "result.json", result)
            raise
        _atomic_write_json(attempt_dir / "result.json", result)
        return self.merge_evaluation(simulation_id, attempt_dir / "result.json")

    def merge_evaluation(self, simulation_id: str, evaluation_result_path: Path) -> dict[str, Any]:
        """Merge a successful reward without changing the generation artifact."""

        generation = self.load_generation(simulation_id)
        result = _read_object(evaluation_result_path)
        if result.get("status") != "evaluation_complete":
            raise ValueError("only a complete evaluation can be merged")
        if (
            result.get("simulation_id") != simulation_id
            or result.get("generation_sha256") != generation["generation_sha256"]
        ):
            raise ValueError("evaluation does not match generation id/hash")
        expected_evaluation_hash = sha256_json(_without_hash(result, "evaluation_sha256"))
        if result.get("evaluation_sha256") != expected_evaluation_hash:
            raise ValueError("evaluation SHA-256 mismatch")
        evaluation = result.get("evaluation")
        reward_info = evaluation.get("reward_info") if isinstance(evaluation, Mapping) else None
        if not isinstance(reward_info, Mapping):
            raise ValueError("evaluation reward_info is missing")
        simulation = dict(generation["simulation"])
        simulation["reward_info"] = dict(reward_info)
        merged = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "status": "merged",
            "simulation_id": simulation_id,
            "generation_sha256": generation["generation_sha256"],
            "evaluation_sha256": result["evaluation_sha256"],
            "merged_at": utc_now(),
            "simulation": simulation,
            "evaluation": dict(evaluation),
        }
        merged["merged_sha256"] = sha256_json(merged)
        path = self.simulation_dir(simulation_id) / "merged.json"
        if path.exists():
            existing = _read_object(path)
            if (
                existing.get("generation_sha256") == merged["generation_sha256"]
                and existing.get("evaluation_sha256") == merged["evaluation_sha256"]
            ):
                return existing
            raise ValueError("a conflicting evaluation is already merged")
        _atomic_write_json(path, merged)
        return merged

    def generation_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        ids = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or not _SAFE_ID.fullmatch(directory.name):
                continue
            if (directory / "generation.json").is_file() and (
                directory / "generation_complete.json"
            ).is_file():
                ids.append(directory.name)
        return ids

    def summary(self) -> dict[str, Any]:
        counts = Counter(
            {
                "generation_complete": 0,
                "generation_error": 0,
                "evaluation_complete": 0,
                "evaluation_error": 0,
                "merged": 0,
            }
        )
        if self.root.exists():
            for directory in self.root.iterdir():
                if not directory.is_dir():
                    continue
                if (directory / "generation_complete.json").is_file():
                    counts["generation_complete"] += 1
                if (directory / "generation_error.json").is_file():
                    counts["generation_error"] += 1
                if (directory / "merged.json").is_file():
                    counts["merged"] += 1
                for result_path in directory.glob("evaluation_attempts/attempt_*/result.json"):
                    status = _read_object(result_path).get("status")
                    if status in {"evaluation_complete", "evaluation_error"}:
                        counts[str(status)] += 1
        return {
            "schema_version": "1.0",
            "root": self.root.as_posix(),
            "counts": dict(counts),
            "generation_ids": self.generation_ids(),
        }


def merge_rewards_into_results(
    results: Mapping[str, Any], store: TrajectoryArtifactStore
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize offline rewards into a τ³ Results payload by simulation id."""

    output = deepcopy(dict(results))
    simulations = output.get("simulations")
    if not isinstance(simulations, list):
        raise ValueError("results payload requires a simulations list")
    ids = [
        str(simulation.get("id") or "")
        for simulation in simulations
        if isinstance(simulation, Mapping)
    ]
    if len(ids) != len(simulations) or any(not value for value in ids):
        raise ValueError("every simulation requires an id")
    if len(ids) != len(set(ids)):
        raise ValueError("results payload contains duplicate simulation ids")

    records: list[dict[str, Any]] = []
    for simulation in simulations:
        simulation_id = str(simulation["id"])
        generation = store.load_generation(simulation_id)
        source_conversation = simulation.get("messages")
        if source_conversation is None:
            source_conversation = simulation.get("ticks")
        if (
            sha256_json(source_conversation)
            != generation["component_hashes"]["conversation_sha256"]
        ):
            raise ValueError(f"results conversation does not match generation: {simulation_id}")
        merged_path = store.simulation_dir(simulation_id) / "merged.json"
        if not merged_path.is_file():
            raise ValueError(f"offline reward is not merged for {simulation_id}")
        merged = _read_object(merged_path)
        if (
            merged.get("status") != "merged"
            or merged.get("simulation_id") != simulation_id
            or merged.get("generation_sha256") != generation["generation_sha256"]
        ):
            raise ValueError(f"merged reward does not match generation: {simulation_id}")
        expected_merged_hash = sha256_json(_without_hash(merged, "merged_sha256"))
        if merged.get("merged_sha256") != expected_merged_hash:
            raise ValueError(f"merged reward SHA-256 mismatch: {simulation_id}")
        reward_info = (merged.get("simulation") or {}).get("reward_info")
        if not isinstance(reward_info, Mapping):
            raise ValueError(f"merged reward_info is missing: {simulation_id}")
        simulation["reward_info"] = dict(reward_info)
        records.append(
            {
                "simulation_id": simulation_id,
                "generation_sha256": generation["generation_sha256"],
                "evaluation_sha256": merged["evaluation_sha256"],
                "reward": reward_info.get("reward"),
            }
        )
    audit = {
        "schema_version": "1.0",
        "merge": "offline_rewards_by_simulation_id_and_generation_hash",
        "simulation_count": len(simulations),
        "merged_count": len(records),
        "complete": len(records) == len(simulations),
        "records": records,
        "output_results_sha256": sha256_json(output),
    }
    return output, audit
