"""Immutable common-prefix artifacts and representation fork plans."""

from __future__ import annotations

import json
import math
import os
import statistics
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .trajectory_artifacts import sha256_json


PREFIX_SCHEMA_VERSION = "gdsc_frozen_prefix_v1"
FORK_PLAN_SCHEMA_VERSION = "gdsc_representation_forks_v1"
ALLOWED_TREATMENTS = ("raw_message", "compiled", "drop")


def _as_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return None


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _without_hash(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


@dataclass(frozen=True, slots=True)
class FrozenPrefix:
    prefix_id: str
    task: Mapping[str, Any]
    domain: str
    seed: int
    conversation: Sequence[Mapping[str, Any]]
    environment_snapshot: Mapping[str, Any]
    event_graph: Mapping[str, Any]
    decision_state: Mapping[str, Any]
    decision_query: Mapping[str, Any]
    tool_schemas: Sequence[Mapping[str, Any]]
    representation_payload: Mapping[str, Any]
    model_config: Mapping[str, Any]
    object_class: str

    def to_dict(self) -> dict[str, Any]:
        components = {
            "task_sha256": sha256_json(self.task),
            "conversation_sha256": sha256_json(self.conversation),
            "environment_snapshot_sha256": sha256_json(self.environment_snapshot),
            "event_graph_sha256": sha256_json(self.event_graph),
            "decision_state_sha256": sha256_json(self.decision_state),
            "decision_query_sha256": sha256_json(self.decision_query),
            "tool_schemas_sha256": sha256_json(self.tool_schemas),
            "representation_payload_sha256": sha256_json(self.representation_payload),
            "model_config_sha256": sha256_json(self.model_config),
        }
        value: dict[str, Any] = {
            "schema_version": PREFIX_SCHEMA_VERSION,
            "status": "frozen",
            "prefix_id": self.prefix_id,
            "task": deepcopy(dict(self.task)),
            "domain": self.domain,
            "seed": self.seed,
            "conversation": deepcopy(list(self.conversation)),
            "environment_snapshot": deepcopy(dict(self.environment_snapshot)),
            "event_graph": deepcopy(dict(self.event_graph)),
            "decision_state": deepcopy(dict(self.decision_state)),
            "decision_query": deepcopy(dict(self.decision_query)),
            "tool_schemas": deepcopy(list(self.tool_schemas)),
            "representation_payload": deepcopy(dict(self.representation_payload)),
            "model_config": deepcopy(dict(self.model_config)),
            "object_class": self.object_class,
            "component_hashes": components,
        }
        value["prefix_sha256"] = sha256_json(value)
        return value


def validate_frozen_prefix(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != PREFIX_SCHEMA_VERSION or value.get("status") != "frozen":
        raise ValueError("unsupported or incomplete frozen prefix")
    expected = sha256_json(_without_hash(value, "prefix_sha256"))
    if value.get("prefix_sha256") != expected:
        raise ValueError("frozen prefix SHA-256 mismatch")
    component_fields = {
        "task_sha256": "task",
        "conversation_sha256": "conversation",
        "environment_snapshot_sha256": "environment_snapshot",
        "event_graph_sha256": "event_graph",
        "decision_state_sha256": "decision_state",
        "decision_query_sha256": "decision_query",
        "tool_schemas_sha256": "tool_schemas",
        "representation_payload_sha256": "representation_payload",
        "model_config_sha256": "model_config",
    }
    hashes = value.get("component_hashes")
    if not isinstance(hashes, Mapping):
        raise ValueError("frozen prefix component hashes are missing")
    for hash_field, payload_field in component_fields.items():
        if hashes.get(hash_field) != sha256_json(value.get(payload_field)):
            raise ValueError(f"frozen prefix component mismatch: {payload_field}")


def persist_frozen_prefix(root: Path | str, prefix: FrozenPrefix) -> Path:
    value = prefix.to_dict()
    validate_frozen_prefix(value)
    directory = Path(root) / prefix.prefix_id
    path = directory / "prefix.json"
    marker = directory / "prefix_complete.json"
    if path.exists() or marker.exists():
        existing = load_frozen_prefix(directory)
        if existing["prefix_sha256"] != value["prefix_sha256"]:
            raise ValueError(f"conflicting frozen prefix already exists: {prefix.prefix_id}")
        return path
    _atomic_write(path, value)
    _atomic_write(
        marker,
        {
            "schema_version": PREFIX_SCHEMA_VERSION,
            "status": "frozen",
            "prefix_id": prefix.prefix_id,
            "prefix_sha256": value["prefix_sha256"],
        },
    )
    return path


def load_frozen_prefix(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    directory = source if source.is_dir() else source.parent
    prefix_path = directory / "prefix.json" if source.is_dir() else source
    marker_path = directory / "prefix_complete.json"
    if not prefix_path.is_file() or not marker_path.is_file():
        raise ValueError(f"prefix is not completely frozen: {directory}")
    value = json.loads(prefix_path.read_text(encoding="utf-8"))
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    validate_frozen_prefix(value)
    if (
        marker.get("status") != "frozen"
        or marker.get("prefix_id") != value.get("prefix_id")
        or marker.get("prefix_sha256") != value.get("prefix_sha256")
    ):
        raise ValueError("frozen prefix completion marker mismatch")
    return value


def discover_frozen_prefixes(root: Path | str) -> list[dict[str, Any]]:
    source = Path(root)
    paths = [source] if source.is_file() else sorted(source.glob("*/prefix.json"))
    return [load_frozen_prefix(path) for path in paths]


def _branch_invariants(prefix: Mapping[str, Any]) -> dict[str, Any]:
    hashes = prefix["component_hashes"]
    return {
        "task_sha256": hashes["task_sha256"],
        "conversation_sha256": hashes["conversation_sha256"],
        "environment_snapshot_sha256": hashes["environment_snapshot_sha256"],
        "event_graph_sha256": hashes["event_graph_sha256"],
        "decision_state_sha256": hashes["decision_state_sha256"],
        "decision_query_sha256": hashes["decision_query_sha256"],
        "tool_schemas_sha256": hashes["tool_schemas_sha256"],
        "model_config_sha256": hashes["model_config_sha256"],
    }


def build_fork_plan(
    prefixes: Sequence[Mapping[str, Any]],
    *,
    treatments: Sequence[str] = ALLOWED_TREATMENTS,
    replicates: int = 2,
    max_agent_tool_actions: int = 3,
    session_cap: int = 340,
) -> dict[str, Any]:
    if replicates <= 0 or max_agent_tool_actions <= 0:
        raise ValueError("replicates and max_agent_tool_actions must be positive")
    normalized = tuple(str(value) for value in treatments)
    if len(normalized) != len(set(normalized)) or not normalized:
        raise ValueError("treatments must be unique and non-empty")
    if any(value not in ALLOWED_TREATMENTS for value in normalized):
        raise ValueError(f"unsupported representation treatment: {normalized}")
    branches = []
    for prefix in sorted(prefixes, key=lambda value: str(value.get("prefix_id") or "")):
        validate_frozen_prefix(prefix)
        for treatment in normalized:
            for replicate in range(1, replicates + 1):
                identity = {
                    "prefix_sha256": prefix["prefix_sha256"],
                    "treatment": treatment,
                    "replicate": replicate,
                }
                branches.append(
                    {
                        "branch_id": f"fork_{sha256_json(identity)[:24]}",
                        "prefix_id": prefix["prefix_id"],
                        "prefix_sha256": prefix["prefix_sha256"],
                        "domain": prefix["domain"],
                        "task_id": str((prefix.get("task") or {}).get("task_id") or ""),
                        "object_class": prefix["object_class"],
                        "treatment": treatment,
                        "replicate": replicate,
                        "temperature": 0,
                        "max_agent_tool_actions": max_agent_tool_actions,
                        "auto_retry": False,
                        "invariants": _branch_invariants(prefix),
                    }
                )
    if len(branches) > session_cap:
        raise ValueError(
            f"fork plan has {len(branches)} branches, exceeding session cap {session_cap}"
        )
    result: dict[str, Any] = {
        "schema_version": FORK_PLAN_SCHEMA_VERSION,
        "execution_mode": "offline_manifest_only",
        "external_api_calls": 0,
        "prefix_count": len(prefixes),
        "branch_count": len(branches),
        "session_cap": session_cap,
        "treatments": list(normalized),
        "replicates": replicates,
        "max_agent_tool_actions": max_agent_tool_actions,
        "branches": branches,
    }
    result["plan_sha256"] = sha256_json(result)
    return result


def validate_branch_result(result: Mapping[str, Any], branch: Mapping[str, Any]) -> None:
    """Verify treatment injection and every common-prefix invariant."""

    if result.get("branch_id") != branch.get("branch_id"):
        raise ValueError("fork result branch id mismatch")
    if result.get("prefix_sha256") != branch.get("prefix_sha256"):
        raise ValueError("fork result prefix hash mismatch")
    if result.get("treatment") != branch.get("treatment"):
        raise ValueError("fork result treatment mismatch")
    if result.get("invariants") != branch.get("invariants"):
        raise ValueError("fork result changed a common-prefix invariant")
    if result.get("treatment_injected") is not True:
        raise ValueError("representation treatment was not demonstrably injected")
    actions = result.get("agent_tool_actions")
    if not isinstance(actions, int) or actions < 0 or actions > int(branch["max_agent_tool_actions"]):
        raise ValueError("fork result exceeds the preregistered action limit")


def score_representation_harm(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Score local harm and the preregistered R3-to-R4 gate."""

    normalized: list[dict[str, Any]] = []
    by_pair: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for source in rows:
        row = dict(source)
        harm = any(
            bool(row.get(field))
            for field in (
                "invalid_action",
                "violated_precondition",
                "policy_violation",
                "unallowed_side_effect",
            )
        ) or bool(row.get("no_progress_when_raw_progresses")) or bool(
            row.get("task_failure_when_raw_succeeds")
        )
        row["harm"] = harm
        normalized.append(row)
        key = (str(row.get("prefix_id") or ""), int(row.get("replicate") or 0))
        treatment = str(row.get("treatment") or "")
        if not key[0] or key[1] <= 0 or treatment not in ALLOWED_TREATMENTS:
            raise ValueError("each fork row requires prefix_id, positive replicate, and treatment")
        if treatment in by_pair[key]:
            raise ValueError(f"duplicate fork result: {key}/{treatment}")
        by_pair[key][treatment] = row

    complete = bool(by_pair) and all(set(group) == set(ALLOWED_TREATMENTS) for group in by_pair.values())
    prefixes = sorted({key[0] for key in by_pair})
    compiled_unique_harm: set[str] = set()
    policy_or_side_effect_unique: set[str] = set()
    discordant_prefixes: set[str] = set()
    compiled_better: set[str] = set()
    input_reductions: list[float] = []
    injection_ok = True
    hashes_ok = True
    for (prefix_id, _replicate), group in by_pair.items():
        if set(group) != set(ALLOWED_TREATMENTS):
            continue
        raw, compiled, drop = group["raw_message"], group["compiled"], group["drop"]
        injection_ok = injection_ok and all(row.get("treatment_injected") is True for row in group.values())
        hashes_ok = hashes_ok and len(
            {sha256_json(row.get("invariants") or {}) for row in group.values()}
        ) == 1
        if compiled["harm"] and not raw["harm"]:
            compiled_unique_harm.add(prefix_id)
        if (
            bool(compiled.get("policy_violation") or compiled.get("unallowed_side_effect"))
            and not bool(raw.get("policy_violation") or raw.get("unallowed_side_effect"))
        ):
            policy_or_side_effect_unique.add(prefix_id)
        if compiled["harm"] != drop["harm"]:
            discordant_prefixes.add(prefix_id)
            if not compiled["harm"] and drop["harm"]:
                compiled_better.add(prefix_id)
        raw_tokens = _as_number(raw.get("provider_input_tokens"))
        compiled_tokens = _as_number(compiled.get("provider_input_tokens"))
        if raw_tokens and compiled_tokens is not None:
            input_reductions.append((raw_tokens - compiled_tokens) / raw_tokens)
    median_reduction = statistics.median(input_reductions) if input_reductions else None
    checks = {
        "complete_branches": complete,
        "treatment_injection": injection_ok and bool(normalized),
        "snapshot_and_hashes": hashes_ok and bool(normalized),
        "compiled_input_reduction": median_reduction is not None and median_reduction >= 0.15,
        "no_compiled_unique_policy_or_irreversible_side_effect": not policy_or_side_effect_unique,
        "representation_harm_at_most_one_prefix": len(compiled_unique_harm) <= 1,
        "discordance_identifiable": len(discordant_prefixes) >= 5,
        "discordance_majority_supports_compiled": (
            len(discordant_prefixes) >= 5
            and len(compiled_better) > len(discordant_prefixes) / 2
        ),
    }
    return {
        "schema_version": "gdsc_representation_harm_v1",
        "r3_to_r4_passed": all(checks.values()),
        "decision": "proceed_to_r4" if all(checks.values()) else "stop_before_r4",
        "checks": checks,
        "prefix_count": len(prefixes),
        "branch_result_count": len(normalized),
        "median_compiled_provider_input_reduction": median_reduction,
        "compiled_unique_harm_prefixes": sorted(compiled_unique_harm),
        "compiled_unique_policy_or_side_effect_prefixes": sorted(policy_or_side_effect_unique),
        "compiled_drop_discordant_prefixes": sorted(discordant_prefixes),
        "compiled_better_discordant_prefixes": sorted(compiled_better),
        "rows": normalized,
    }
