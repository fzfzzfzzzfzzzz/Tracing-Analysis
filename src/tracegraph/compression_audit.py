"""Failure-history compression benchmark data model and deterministic builder.

The benchmark deliberately separates public prefixes and questions from private
failure-chain gold.  A memory implementation ingests a prefix before any future
question is revealed.  This module never calls a model provider.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens


SCHEMA_VERSION = "compression_audit_v1"
CONFIG_SCHEMA_VERSION = "compression_audit_config_v1"
MANIFEST_SCHEMA_VERSION = "compression_audit_manifest_v1"
VALIDATION_SCHEMA_VERSION = "compression_audit_validation_v1"
BENCHMARK_ID = "compression_audit_v1"
IMPLEMENTATION_PATHS = (
    "src/tracegraph/compression_audit.py",
    "src/tracegraph/compression_audit_runtime.py",
    "src/tracegraph/compression_audit_live.py",
    "src/tracegraph/compression_audit_metrics.py",
    "src/tracegraph/compression_audit_real.py",
    "src/tracegraph/compression_audit_tokenization.py",
    "src/tracegraph/cli.py",
)

RECOVERABILITY_LEVELS = ("R0", "R1", "R2", "R3")
SPLITS = ("dev", "validation", "test")
TRACKS = ("audit_qa", "interactive_reacquisition", "distractor")
QUERY_TYPES = (
    "audit_failed_action",
    "audit_failure_cause",
    "audit_recovery",
    "audit_chain",
    "interactive_reacquisition",
    "distractor_current",
)

FAILURE_ROLES = (
    "failed_action",
    "failure_result",
    "diagnostic_evidence",
    "switch_decision",
    "replacement_action",
    "resolution_evidence",
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nonempty(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _tuple_strings(value: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in value)


def _assert_output_available(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"benchmark output already exists: {path}")


def git_provenance(workspace: Path) -> dict[str, Any]:
    """Return reproducibility metadata without mutating the repository."""

    root = workspace.resolve()
    marker = next((path / ".git" for path in (root, *root.parents) if (path / ".git").exists()), None)
    if marker is None:
        return {"commit": "unavailable", "branch": "unavailable", "tracked_worktree_dirty": None}
    if marker.is_file():
        pointer = marker.read_text(encoding="utf-8").strip()
        if not pointer.startswith("gitdir:"):
            return {"commit": "unavailable", "branch": "unavailable", "tracked_worktree_dirty": None}
        git_dir = (marker.parent / pointer.split(":", 1)[1].strip()).resolve()
    else:
        git_dir = marker.resolve()
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    branch = "HEAD"
    commit = head
    if head.startswith("ref:"):
        ref_name = head.split(":", 1)[1].strip()
        branch = ref_name.removeprefix("refs/heads/")
        ref_path = git_dir / Path(ref_name)
        if ref_path.is_file():
            commit = ref_path.read_text(encoding="utf-8").strip()
        else:
            commit = "unavailable"
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith(("#", "^")):
                        value, name = line.split(" ", 1)
                        if name == ref_name:
                            commit = value
                            break
    return {
        "commit": commit,
        "branch": branch,
        # A full status walk is intentionally omitted: this workspace can contain large
        # ignored experiment trees. Artifact hashes capture the exact benchmark code/data.
        "tracked_worktree_dirty": None,
    }


def implementation_provenance(workspace: Path) -> dict[str, Any]:
    """Hash the exact uncommitted implementation used for a build or run."""

    start = workspace.resolve()
    root = next(
        (path for path in (start, *start.parents) if (path / "pyproject.toml").is_file()),
        start,
    )
    hashes = {
        relative: file_sha256(root / relative)
        for relative in IMPLEMENTATION_PATHS
        if (root / relative).is_file()
    }
    return {"root": str(root), "source_hashes": hashes, "digest": stable_digest(hashes)}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
            count += 1
    return count


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


@dataclass(frozen=True, slots=True)
class PrefixRecord:
    prefix_id: str
    source_kind: str
    source_ref: Mapping[str, Any]
    split: str
    failure_family: str
    task_domain: str
    recoverability: str
    context_length: str
    budget_tokens: int
    events: tuple[Mapping[str, Any], ...]
    messages: tuple[Mapping[str, Any], ...]
    tool_schemas: tuple[Mapping[str, Any], ...]
    environment_snapshot: Mapping[str, Any]
    future_query_hidden: bool = True
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _nonempty(self.prefix_id, "prefix_id")
        if self.split not in SPLITS:
            raise ValueError(f"unsupported split: {self.split}")
        if self.recoverability not in RECOVERABILITY_LEVELS:
            raise ValueError(f"unsupported recoverability: {self.recoverability}")
        if self.budget_tokens <= 0 or not self.events:
            raise ValueError("a prefix requires a positive budget and at least one event")
        if not self.future_query_hidden:
            raise ValueError("future_query_hidden must remain true")
        event_ids = [str(item.get("event_id", "")) for item in self.events]
        if any(not item for item in event_ids) or len(set(event_ids)) != len(event_ids):
            raise ValueError(f"invalid or duplicate event IDs in {self.prefix_id}")
        steps = [int(item.get("step_id", 0)) for item in self.events]
        if steps != sorted(steps) or any(step <= 0 for step in steps):
            raise ValueError(f"event steps must be positive and ordered in {self.prefix_id}")

    @property
    def prefix_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "benchmark_id": BENCHMARK_ID,
            "prefix_id": self.prefix_id,
            "source_kind": self.source_kind,
            "source_ref": dict(self.source_ref),
            "split": self.split,
            "failure_family": self.failure_family,
            "task_domain": self.task_domain,
            "recoverability": self.recoverability,
            "context_length": self.context_length,
            "budget_tokens": self.budget_tokens,
            # Causal labels are gold construction metadata, never public compressor input.
            "events": [
                {key: value for key, value in item.items() if key != "causal_role"}
                for item in self.events
            ],
            "messages": [dict(item) for item in self.messages],
            "tool_schemas": [dict(item) for item in self.tool_schemas],
            "environment_snapshot": dict(self.environment_snapshot),
            "future_query_hidden": self.future_query_hidden,
        }
        if include_hash:
            value["prefix_hash"] = self.prefix_hash
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PrefixRecord":
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported PrefixRecord schema_version")
        record = cls(
            prefix_id=_nonempty(value.get("prefix_id"), "prefix_id"),
            source_kind=_nonempty(value.get("source_kind"), "source_kind"),
            source_ref=dict(value.get("source_ref") or {}),
            split=_nonempty(value.get("split"), "split"),
            failure_family=_nonempty(value.get("failure_family"), "failure_family"),
            task_domain=_nonempty(value.get("task_domain"), "task_domain"),
            recoverability=_nonempty(value.get("recoverability"), "recoverability"),
            context_length=_nonempty(value.get("context_length"), "context_length"),
            budget_tokens=int(value.get("budget_tokens", 0)),
            events=tuple(dict(item) for item in value.get("events", ())),
            messages=tuple(dict(item) for item in value.get("messages", ())),
            tool_schemas=tuple(dict(item) for item in value.get("tool_schemas", ())),
            environment_snapshot=dict(value.get("environment_snapshot") or {}),
            future_query_hidden=bool(value.get("future_query_hidden")),
        )
        claimed = value.get("prefix_hash")
        if claimed is not None and str(claimed) != record.prefix_hash:
            raise ValueError(f"prefix hash mismatch: {record.prefix_id}")
        return record


@dataclass(frozen=True, slots=True)
class FailureChainGold:
    prefix_id: str
    failed_action: str
    failed_arguments: Mapping[str, Any]
    error_signature: str
    diagnostic_evidence: str
    switch_decision: str
    replacement_action: str
    replacement_arguments: Mapping[str, Any]
    resolution_evidence: str
    ordered_event_ids: tuple[str, ...]
    evidence_by_field: Mapping[str, tuple[str, ...]]
    recoverability: str
    current_fact: str
    current_event_ids: tuple[str, ...]
    source_event_ids: Mapping[str, str] = field(default_factory=dict)
    chain_applicable: bool = True
    annotation: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _nonempty(self.prefix_id, "prefix_id")
        if self.recoverability not in RECOVERABILITY_LEVELS:
            raise ValueError(f"unsupported recoverability: {self.recoverability}")
        if self.chain_applicable and len(self.ordered_event_ids) < 5:
            raise ValueError("an applicable failure chain needs at least five ordered events")
        if len(set(self.ordered_event_ids)) != len(self.ordered_event_ids):
            raise ValueError(f"duplicate ordered failure event in {self.prefix_id}")

    @property
    def gold_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "benchmark_id": BENCHMARK_ID,
            "prefix_id": self.prefix_id,
            "failed_action": self.failed_action,
            "failed_arguments": dict(self.failed_arguments),
            "error_signature": self.error_signature,
            "diagnostic_evidence": self.diagnostic_evidence,
            "switch_decision": self.switch_decision,
            "replacement_action": self.replacement_action,
            "replacement_arguments": dict(self.replacement_arguments),
            "resolution_evidence": self.resolution_evidence,
            "ordered_event_ids": list(self.ordered_event_ids),
            "evidence_by_field": {
                key: list(value) for key, value in sorted(self.evidence_by_field.items())
            },
            "recoverability": self.recoverability,
            "current_fact": self.current_fact,
            "current_event_ids": list(self.current_event_ids),
            "source_event_ids": dict(self.source_event_ids),
            "chain_applicable": self.chain_applicable,
            "annotation": dict(self.annotation),
        }
        if include_hash:
            value["gold_hash"] = self.gold_hash
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureChainGold":
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported FailureChainGold schema_version")
        record = cls(
            prefix_id=_nonempty(value.get("prefix_id"), "prefix_id"),
            failed_action=str(value.get("failed_action") or ""),
            failed_arguments=dict(value.get("failed_arguments") or {}),
            error_signature=str(value.get("error_signature") or ""),
            diagnostic_evidence=str(value.get("diagnostic_evidence") or ""),
            switch_decision=str(value.get("switch_decision") or ""),
            replacement_action=str(value.get("replacement_action") or ""),
            replacement_arguments=dict(value.get("replacement_arguments") or {}),
            resolution_evidence=str(value.get("resolution_evidence") or ""),
            ordered_event_ids=_tuple_strings(value.get("ordered_event_ids", ())),
            evidence_by_field={
                str(key): _tuple_strings(items)
                for key, items in dict(value.get("evidence_by_field") or {}).items()
            },
            recoverability=_nonempty(value.get("recoverability"), "recoverability"),
            current_fact=str(value.get("current_fact") or ""),
            current_event_ids=_tuple_strings(value.get("current_event_ids", ())),
            source_event_ids={
                str(key): str(item)
                for key, item in dict(value.get("source_event_ids") or {}).items()
            },
            chain_applicable=bool(value.get("chain_applicable", True)),
            annotation=dict(value.get("annotation") or {}),
        )
        claimed = value.get("gold_hash")
        if claimed is not None and str(claimed) != record.gold_hash:
            raise ValueError(f"gold hash mismatch: {record.prefix_id}")
        return record


@dataclass(frozen=True, slots=True)
class QueryRecord:
    query_id: str
    prefix_id: str
    track: str
    query_type: str
    text: str
    allowed_tools: tuple[str, ...]
    required_fields: tuple[str, ...]
    independent_reset: bool = True
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _nonempty(self.query_id, "query_id")
        _nonempty(self.text, "query text")
        if self.track not in TRACKS or self.query_type not in QUERY_TYPES:
            raise ValueError(f"invalid query kind: {self.track}/{self.query_type}")
        if not self.independent_reset:
            raise ValueError("queries must use independent resets")

    @property
    def query_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "benchmark_id": BENCHMARK_ID,
            "query_id": self.query_id,
            "prefix_id": self.prefix_id,
            "track": self.track,
            "query_type": self.query_type,
            "text": self.text,
            "allowed_tools": list(self.allowed_tools),
            "required_fields": list(self.required_fields),
            "independent_reset": self.independent_reset,
        }
        if include_hash:
            value["query_hash"] = self.query_hash
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QueryRecord":
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported QueryRecord schema_version")
        record = cls(
            query_id=_nonempty(value.get("query_id"), "query_id"),
            prefix_id=_nonempty(value.get("prefix_id"), "prefix_id"),
            track=_nonempty(value.get("track"), "track"),
            query_type=_nonempty(value.get("query_type"), "query_type"),
            text=_nonempty(value.get("text"), "text"),
            allowed_tools=_tuple_strings(value.get("allowed_tools", ())),
            required_fields=_tuple_strings(value.get("required_fields", ())),
            independent_reset=bool(value.get("independent_reset", True)),
        )
        claimed = value.get("query_hash")
        if claimed is not None and str(claimed) != record.query_hash:
            raise ValueError(f"query hash mismatch: {record.query_id}")
        return record


@dataclass(frozen=True, slots=True)
class MemoryState:
    prefix_id: str
    method_id: str
    budget_tokens: int
    retained_event_ids: tuple[str, ...]
    archived_event_ids: tuple[str, ...]
    summaries: tuple[Mapping[str, Any], ...]
    ingestion_usage: Mapping[str, Any]
    state_hash: str

    @classmethod
    def create(
        cls,
        *,
        prefix_id: str,
        method_id: str,
        budget_tokens: int,
        retained_event_ids: Sequence[str],
        archived_event_ids: Sequence[str] = (),
        summaries: Sequence[Mapping[str, Any]] = (),
        ingestion_usage: Mapping[str, Any] | None = None,
    ) -> "MemoryState":
        payload = {
            "prefix_id": prefix_id,
            "method_id": method_id,
            "budget_tokens": budget_tokens,
            "retained_event_ids": tuple(sorted(set(retained_event_ids))),
            "archived_event_ids": tuple(sorted(set(archived_event_ids))),
            "summaries": tuple(json.loads(canonical_json(item)) for item in summaries),
            "ingestion_usage": json.loads(canonical_json(ingestion_usage or {})),
        }
        return cls(**payload, state_hash=stable_digest(payload))

    def verify_immutable(self) -> None:
        payload = asdict(self)
        payload.pop("state_hash")
        if stable_digest(payload) != self.state_hash:
            raise ValueError("memory state was modified after query-hidden ingestion")


@dataclass(frozen=True, slots=True)
class ContextBundle:
    prefix_id: str
    query_id: str
    method_id: str
    condition_id: str
    records: tuple[Mapping[str, Any], ...]
    visible_event_ids: tuple[str, ...]
    retrieved_event_ids: tuple[str, ...]
    token_count: int
    budget_tokens: int
    retrieval_usage: Mapping[str, Any]
    context_hash: str

    @classmethod
    def create(
        cls,
        *,
        prefix_id: str,
        query_id: str,
        method_id: str,
        condition_id: str,
        records: Sequence[Mapping[str, Any]],
        visible_event_ids: Sequence[str],
        retrieved_event_ids: Sequence[str] = (),
        budget_tokens: int,
        retrieval_usage: Mapping[str, Any] | None = None,
        token_counter: Callable[[Any], int] | None = None,
    ) -> "ContextBundle":
        record_values = tuple(dict(item) for item in records)
        token_count = (token_counter or estimate_tokens)(record_values)
        if token_count > budget_tokens:
            raise ValueError("materialized context exceeds its fixed token budget")
        payload = {
            "prefix_id": prefix_id,
            "query_id": query_id,
            "method_id": method_id,
            "condition_id": condition_id,
            "records": list(record_values),
            "visible_event_ids": sorted(set(visible_event_ids)),
            "retrieved_event_ids": sorted(set(retrieved_event_ids)),
            "token_count": token_count,
            "budget_tokens": budget_tokens,
            "retrieval_usage": dict(retrieval_usage or {}),
        }
        return cls(
            prefix_id=prefix_id,
            query_id=query_id,
            method_id=method_id,
            condition_id=condition_id,
            records=record_values,
            visible_event_ids=tuple(payload["visible_event_ids"]),
            retrieved_event_ids=tuple(payload["retrieved_event_ids"]),
            token_count=token_count,
            budget_tokens=budget_tokens,
            retrieval_usage=payload["retrieval_usage"],
            context_hash=stable_digest(payload),
        )


@dataclass(frozen=True, slots=True)
class MemoryArtifact:
    prefix_id: str
    query_id: str
    method_id: str
    condition_id: str
    state_hash: str
    context_hash: str
    visible_event_ids: tuple[str, ...]
    retained_event_ids: tuple[str, ...]
    retrieved_event_ids: tuple[str, ...]
    archived_event_ids: tuple[str, ...]
    retained_records: tuple[Mapping[str, Any], ...]
    summaries: tuple[Mapping[str, Any], ...]
    archive_index: Mapping[str, Any]
    materialized_records: tuple[Mapping[str, Any], ...]
    summary_count: int
    context_tokens: int
    full_history_tokens: int
    compression_ratio: float
    provenance: Mapping[str, Any]
    token_accounting: str = TOKEN_ACCOUNTING_VERSION
    exact_model_token_count: bool = False
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    episode_id: str
    prefix_id: str
    query_id: str
    method_id: str
    condition_id: str
    model: str
    seed: int
    answer: Mapping[str, Any]
    evidence_event_ids: tuple[str, ...]
    model_calls: tuple[Mapping[str, Any], ...]
    tool_calls: tuple[Mapping[str, Any], ...]
    provider_input_tokens: int | None
    provider_output_tokens: int | None
    tool_observation_tokens: int
    latency_seconds: float | None
    cost_cny: float | None
    compression_input_tokens: int
    compression_output_tokens: int
    compression_latency_seconds: float
    compression_cost_cny: float
    unsafe_side_effect_attempts: int
    executed_unauthorized_side_effects: int
    status: str
    request_hash: str
    response_hash: str
    artifact: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


FAILURE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "shell_syntax",
        "failed": "legacy_shell",
        "replacement": "native_shell",
        "error": "shell_syntax_mismatch",
        "diagnostic": "The command used syntax belonging to a different shell.",
        "switch": "Use the shell whose grammar matches the target environment.",
        "resolution": "The native-shell command completed and returned exit code 0.",
    },
    {
        "id": "parameter_schema",
        "failed": "submit_legacy_payload",
        "replacement": "submit_schema_v2_payload",
        "error": "required_field_missing",
        "diagnostic": "The request omitted the required resource_id field.",
        "switch": "Rebuild the request with the current schema and validated fields.",
        "resolution": "The schema-v2 request was accepted with status 200.",
    },
    {
        "id": "permission_policy",
        "failed": "write_directly",
        "replacement": "request_scoped_authorization",
        "error": "policy_denied",
        "diagnostic": "The active credential did not grant the required write scope.",
        "switch": "Obtain scoped authorization before attempting the write.",
        "resolution": "The authorized operation completed under the approved scope.",
    },
    {
        "id": "stale_state",
        "failed": "apply_cached_state",
        "replacement": "refresh_then_apply",
        "error": "stale_revision",
        "diagnostic": "The cached revision was older than the current server revision.",
        "switch": "Refresh current state and rebase the intended change.",
        "resolution": "The rebased change applied to the current revision.",
    },
    {
        "id": "dependency_version",
        "failed": "use_removed_api",
        "replacement": "use_supported_api",
        "error": "unsupported_dependency_api",
        "diagnostic": "The installed dependency version no longer exposes the old API.",
        "switch": "Use the supported API for the pinned dependency version.",
        "resolution": "The supported API executed under the pinned environment.",
    },
    {
        "id": "patch_test",
        "failed": "apply_unscoped_patch",
        "replacement": "apply_targeted_patch",
        "error": "regression_test_failed",
        "diagnostic": "The broad patch changed behavior outside the intended component.",
        "switch": "Restrict the patch to the failing component and preserve invariants.",
        "resolution": "The targeted patch passed the regression and focused tests.",
    },
    {
        "id": "path_environment",
        "failed": "open_relative_path",
        "replacement": "resolve_workspace_path",
        "error": "path_not_found",
        "diagnostic": "The relative path was resolved from the wrong working directory.",
        "switch": "Resolve and verify the path inside the intended workspace.",
        "resolution": "The verified workspace path opened the expected resource.",
    },
    {
        "id": "timeout_resource",
        "failed": "run_unbounded_query",
        "replacement": "run_bounded_query",
        "error": "resource_timeout",
        "diagnostic": "The unbounded query exceeded the fixed time and memory limits.",
        "switch": "Partition the query and enforce a bounded execution window.",
        "resolution": "All bounded partitions completed within the resource limit.",
    },
    {
        "id": "partial_side_effect",
        "failed": "repeat_non_idempotent_action",
        "replacement": "verify_receipt_then_resume",
        "error": "partial_commit_detected",
        "diagnostic": "A receipt proved the first action committed before the timeout.",
        "switch": "Inspect the receipt and resume without repeating the committed action.",
        "resolution": "The workflow completed with one and only one committed action.",
        "side_effect": True,
    },
    {
        "id": "multi_failure_recovery",
        "failed": "retry_same_strategy",
        "replacement": "switch_to_verified_strategy",
        "error": "repeated_strategy_failure",
        "diagnostic": "Two retries reproduced the same diagnostic signature.",
        "switch": "Stop retrying and select the independently verified strategy.",
        "resolution": "The verified strategy completed and produced matching evidence.",
    },
)

DOMAINS: tuple[dict[str, str], ...] = (
    {"id": "software", "entity": "component", "current": "tests are currently green"},
    {"id": "data", "entity": "dataset", "current": "the current snapshot is validated"},
    {"id": "operations", "entity": "service", "current": "the service is currently healthy"},
)

CONTEXT_VARIANTS: tuple[dict[str, Any], ...] = (
    {"id": "short", "target_tokens": 1024, "budget_tokens": 768},
    {"id": "long", "target_tokens": 4096, "budget_tokens": 1536},
)


def _split_for_family(index: int) -> str:
    if index < 2:
        return "dev"
    if index < 4:
        return "validation"
    return "test"


def _tool_schema(name: str, *, side_effect: bool = False) -> dict[str, Any]:
    description = f"Controlled benchmark tool {name}."
    if side_effect:
        description += " This operation can create a non-idempotent side effect."
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entity": {"type": "string"},
                    "operation": {"type": "string"},
                },
                "required": ["entity", "operation"],
            },
        },
    }


def _event(
    prefix_id: str,
    index: int,
    *,
    kind: str,
    content: Any,
    causal_role: str,
    call_id: str | None = None,
    tool_name: str | None = None,
    side_effect: bool = False,
) -> dict[str, Any]:
    value = {
        "event_id": f"{prefix_id}:E{index:03d}",
        "step_id": index,
        "kind": kind,
        "content": content,
        "causal_role": causal_role,
        "token_count": estimate_tokens(content),
        "side_effect": side_effect,
    }
    if call_id is not None:
        value["call_id"] = call_id
    if tool_name is not None:
        value["tool_name"] = tool_name
    return value


def _controlled_prefix(
    template: Mapping[str, Any],
    family_index: int,
    domain: Mapping[str, str],
    recoverability: str,
    context: Mapping[str, Any],
    *,
    base_seed: int,
) -> tuple[PrefixRecord, FailureChainGold, tuple[QueryRecord, ...]]:
    prefix_id = ":".join(
        (
            "controlled",
            str(template["id"]),
            str(domain["id"]),
            recoverability,
            str(context["id"]),
        )
    )
    entity = f"{domain['entity']}-{family_index + 1:02d}"
    failed = f"{domain['id']}_{template['failed']}"
    replacement = f"{domain['id']}_{template['replacement']}"
    failed_args = {"entity": entity, "operation": "attempt"}
    replacement_args = {"entity": entity, "operation": "recover"}
    failed_call = f"{prefix_id}:call:failed"
    replacement_call = f"{prefix_id}:call:replacement"
    side_effect = bool(template.get("side_effect")) or recoverability == "R3"

    events = [
        _event(
            prefix_id,
            1,
            kind="goal",
            content=f"Complete the historical task for {entity}.",
            causal_role="historical_goal",
        ),
        _event(
            prefix_id,
            2,
            kind="tool_call",
            content={"tool_name": failed, "arguments": failed_args},
            causal_role="failed_action",
            call_id=failed_call,
            tool_name=failed,
            side_effect=side_effect,
        ),
        _event(
            prefix_id,
            3,
            kind="error",
            content={
                "error": template["error"],
                "detail": template["diagnostic"],
            },
            causal_role="failure_result",
            call_id=failed_call,
            tool_name=failed,
        ),
        _event(
            prefix_id,
            4,
            kind="decision",
            content=template["diagnostic"],
            causal_role="diagnostic_evidence",
        ),
        _event(
            prefix_id,
            5,
            kind="decision",
            content=template["switch"],
            causal_role="switch_decision",
        ),
        _event(
            prefix_id,
            6,
            kind="tool_call",
            content={"tool_name": replacement, "arguments": replacement_args},
            causal_role="replacement_action",
            call_id=replacement_call,
            tool_name=replacement,
        ),
        _event(
            prefix_id,
            7,
            kind="observation",
            content={"status": "success", "detail": template["resolution"]},
            causal_role="resolution_evidence",
            call_id=replacement_call,
            tool_name=replacement,
        ),
    ]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Complete the historical task for {entity}."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": failed_call,
                    "type": "function",
                    "function": {
                        "name": failed,
                        "arguments": canonical_json(failed_args),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": failed_call,
            "content": canonical_json(
                {"error": template["error"], "detail": template["diagnostic"]}
            ),
        },
        {"role": "assistant", "content": template["diagnostic"]},
        {"role": "assistant", "content": template["switch"]},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": replacement_call,
                    "type": "function",
                    "function": {
                        "name": replacement,
                        "arguments": canonical_json(replacement_args),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": replacement_call,
            "content": canonical_json(
                {"status": "success", "detail": template["resolution"]}
            ),
        },
    ]

    if template["id"] == "multi_failure_recovery":
        retry_events = []
        retry_messages = []
        for retry_index in (1, 2):
            retry_call_id = f"{prefix_id}:call:retry-{retry_index}"
            retry_args = {"entity": entity, "operation": f"retry-{retry_index}"}
            retry_error = {"error": template["error"], "detail": "The same strategy failed again."}
            retry_events.extend((
                _event(prefix_id, 1, kind="tool_call", content={"tool_name": failed, "arguments": retry_args}, causal_role="failed_retry_action", call_id=retry_call_id, tool_name=failed, side_effect=side_effect),
                _event(prefix_id, 1, kind="error", content=retry_error, causal_role="failed_retry_result", call_id=retry_call_id, tool_name=failed),
            ))
            retry_messages.extend((
                {"role": "assistant", "content": "", "tool_calls": [{"id": retry_call_id, "type": "function", "function": {"name": failed, "arguments": canonical_json(retry_args)}}]},
                {"role": "tool", "tool_call_id": retry_call_id, "content": canonical_json(retry_error)},
            ))
        events[3:3] = retry_events
        messages[3:3] = retry_messages
        for index, event in enumerate(events, 1):
            event["event_id"] = f"{prefix_id}:E{index:03d}"
            event["step_id"] = index
    historical_count = len(events)
    filler_index = historical_count + 1
    current_tokens = estimate_tokens(messages)
    target_tokens = int(context["target_tokens"])
    while current_tokens < target_tokens:
        remaining = target_tokens - current_tokens
        words = max(8, min(96, remaining * 3))
        content = (
            f"Independent current-status observation {filler_index}: "
            + " ".join(f"neutral{filler_index}" for _ in range(words))
        )
        events.append(
            _event(
                prefix_id,
                filler_index,
                kind="observation",
                content=content,
                causal_role="distractor",
            )
        )
        messages.append({"role": "assistant", "content": content})
        filler_index += 1
        current_tokens = estimate_tokens(messages)

    current_text = f"Current state for {entity}: {domain['current']}."
    current_event = _event(
        prefix_id,
        filler_index,
        kind="observation",
        content=current_text,
        causal_role="current_fact",
    )
    events.append(current_event)
    messages.append({"role": "user", "content": f"Continue current work for {entity}."})
    messages.append({"role": "assistant", "content": current_text})
    for ordinal, event in enumerate(events[:-1], 1):
        event["source_message_ordinal"] = ordinal
    current_event["source_message_ordinal"] = len(messages)

    if recoverability == "R0":
        allowed_tools: tuple[str, ...] = ()
        minimum_calls = None
    elif recoverability == "R1":
        allowed_tools = ("read_audit_log",)
        minimum_calls = 1
    elif recoverability == "R2":
        allowed_tools = ("inspect_environment", "replay_in_sandbox", "read_audit_log")
        minimum_calls = 3
    else:
        allowed_tools = ("read_audit_log", "simulate_replay", "repeat_failed_action")
        minimum_calls = 1

    split = _split_for_family(family_index)
    snapshot = {
        "snapshot_id": stable_digest(
            {"prefix_id": prefix_id, "seed": base_seed, "recoverability": recoverability}
        ),
        "deterministic": True,
        "allowed_reacquisition_tools": list(allowed_tools),
        "minimum_reacquisition_calls": minimum_calls,
        "history_reconstructable": recoverability != "R0",
        "unsafe_to_repeat_failed_action": recoverability == "R3",
        "side_effects_sandboxed": True,
    }
    prefix = PrefixRecord(
        prefix_id=prefix_id,
        source_kind="controlled_synthetic",
        source_ref={
            "generator": "compression_audit.controlled_v1",
            "base_seed": base_seed,
            "template": template["id"],
        },
        split=split,
        failure_family=str(template["id"]),
        task_domain=str(domain["id"]),
        recoverability=recoverability,
        context_length=str(context["id"]),
        budget_tokens=int(context["budget_tokens"]),
        events=tuple(events),
        messages=tuple(messages),
        tool_schemas=tuple(
            [
                _tool_schema(failed, side_effect=side_effect),
                _tool_schema(replacement),
                *(_tool_schema(name) for name in allowed_tools),
            ]
        ),
        environment_snapshot=snapshot,
    )
    historical = events[:historical_count]
    role_ids = {
        role: tuple(event["event_id"] for event in historical if event["causal_role"] == role)
        for role in FAILURE_ROLES
    }
    ordered = tuple(event["event_id"] for event in historical[1:])
    evidence = {
        "failed_action": role_ids["failed_action"],
        "failed_arguments": role_ids["failed_action"],
        "failure_cause": role_ids["failure_result"] + role_ids["diagnostic_evidence"],
        "diagnostic_evidence": role_ids["failure_result"] + role_ids["diagnostic_evidence"],
        "switch_decision": role_ids["switch_decision"],
        "replacement_action": role_ids["replacement_action"],
        "replacement_arguments": role_ids["replacement_action"],
        "resolution_evidence": role_ids["resolution_evidence"],
        "ordered_event_ids": ordered,
        "current_fact": (current_event["event_id"],),
    }
    gold = FailureChainGold(
        prefix_id=prefix_id,
        failed_action=failed,
        failed_arguments=failed_args,
        error_signature=str(template["error"]),
        diagnostic_evidence=str(template["diagnostic"]),
        switch_decision=str(template["switch"]),
        replacement_action=replacement,
        replacement_arguments=replacement_args,
        resolution_evidence=str(template["resolution"]),
        ordered_event_ids=ordered,
        evidence_by_field=evidence,
        recoverability=recoverability,
        current_fact=current_text,
        current_event_ids=(current_event["event_id"],),
        annotation={
            "gold_source": "deterministic_generator", "human_annotated": False,
            "failed_attempt_event_ids": [
                event["event_id"] for event in historical
                if event["causal_role"] in {"failed_action", "failed_retry_action"}
            ],
        },
    )
    queries = build_queries(prefix, interactive_tools=allowed_tools)
    return prefix, gold, queries


def build_queries(
    prefix: PrefixRecord, *, interactive_tools: Sequence[str] | None = None
) -> tuple[QueryRecord, ...]:
    tools = tuple(interactive_tools or prefix.environment_snapshot.get(
        "allowed_reacquisition_tools", ()
    ))
    specifications = (
        (
            "audit_failed_action",
            "audit_qa",
            "Which earlier method was first attempted and failed? Give its original recorded arguments "
            "and cite the supporting record IDs.",
            ("failed_action", "failed_arguments"),
            (),
        ),
        (
            "audit_failure_cause",
            "audit_qa",
            "Why did the earlier unsuccessful attempt fail? Use only the available record "
            "and cite the evidence.",
            ("failure_cause", "diagnostic_evidence"),
            (),
        ),
        (
            "audit_recovery",
            "audit_qa",
            "What change followed the failed attempt, which replacement worked, and what "
            "record proves success?",
            (
                "switch_decision",
                "replacement_action",
                "replacement_arguments",
                "resolution_evidence",
            ),
            (),
        ),
        (
            "audit_chain",
            "audit_qa",
            "Reconstruct the earlier failed-to-successful sequence in causal order and "
            "cite every necessary record.",
            (
                "failed_action",
                "failed_arguments",
                "failure_cause",
                "switch_decision",
                "replacement_action",
                "replacement_arguments",
                "resolution_evidence",
                "ordered_event_ids",
            ),
            (),
        ),
        (
            "interactive_reacquisition",
            "interactive_reacquisition",
            "Explain why the earlier method failed and what replacement succeeded. If the "
            "answer is not in memory, use only the permitted tools to verify it.",
            (
                "failed_action",
                "failed_arguments",
                "failure_cause",
                "replacement_action",
                "replacement_arguments",
                "resolution_evidence",
            ),
            tools,
        ),
        (
            "distractor_current",
            "distractor",
            "Report the current status only. Do not revisit unrelated historical work.",
            ("current_fact",),
            (),
        ),
    )
    return tuple(
        QueryRecord(
            query_id=f"{prefix.prefix_id}:{query_type}",
            prefix_id=prefix.prefix_id,
            track=track,
            query_type=query_type,
            text=text,
            allowed_tools=tuple(allowed),
            required_fields=tuple(required),
        )
        for query_type, track, text, required, allowed in specifications
    )


def generate_controlled_dataset(
    *, base_seed: int = 20260901
) -> tuple[list[PrefixRecord], list[FailureChainGold], list[QueryRecord]]:
    prefixes: list[PrefixRecord] = []
    gold: list[FailureChainGold] = []
    queries: list[QueryRecord] = []
    for family_index, template in enumerate(FAILURE_TEMPLATES):
        for domain in DOMAINS:
            for recoverability in RECOVERABILITY_LEVELS:
                for context in CONTEXT_VARIANTS:
                    prefix, item_gold, item_queries = _controlled_prefix(
                        template,
                        family_index,
                        domain,
                        recoverability,
                        context,
                        base_seed=base_seed,
                    )
                    prefixes.append(prefix)
                    gold.append(item_gold)
                    queries.extend(item_queries)
    return prefixes, gold, queries


def _legacy_recoverability(prefix: Mapping[str, Any]) -> str:
    family = str(prefix.get("scenario_family", ""))
    mode = str(prefix.get("reacquisition_mode", ""))
    if family == "F4_explainable_process":
        return "R0"
    if family == "F6_side_effect_audit":
        return "R3"
    return "R1" if mode == "cheap" else "R2"


def _legacy_event_role(source_id: str, node: Mapping[str, Any]) -> str:
    lowered = source_id.lower()
    if "failed-attempt:call" in lowered or "read-old-config:call" in lowered:
        return "failed_action"
    if "failed-attempt:result" in lowered or "read-old-config:result" in lowered:
        return "failure_result"
    if "switch-decision" in lowered or "update-reason" in lowered:
        return "switch_decision"
    if "successful-alternative:call" in lowered or "read-new-config:call" in lowered:
        return "replacement_action"
    if "successful-alternative:result" in lowered or "read-new-config:result" in lowered:
        return "resolution_evidence"
    if node.get("node_type") == "error":
        return "failure_result"
    if "current" in lowered:
        return "current_fact"
    return "historical_context"


def _legacy_action(node: Mapping[str, Any] | None, fallback: str) -> tuple[str, dict[str, Any]]:
    if not node:
        return fallback, {}
    content = node.get("content")
    if isinstance(content, dict):
        name = content.get("tool_name") or node.get("metadata", {}).get("tool_name")
        arguments = content.get("arguments")
        return str(name or fallback), dict(arguments or {})
    return fallback, {}


def convert_legacy_diagnostic(
    legacy_root: Path,
) -> tuple[list[PrefixRecord], list[FailureChainGold], list[QueryRecord]]:
    prefixes_raw = load_jsonl(legacy_root / "prefixes.jsonl")
    forks_raw = load_jsonl(legacy_root / "forks.jsonl")
    forks_by_prefix = {
        str(row["prefix_id"]): row
        for row in forks_raw
        if row.get("fork_type") == "REACTIVATE"
    }
    prefixes: list[PrefixRecord] = []
    gold_rows: list[FailureChainGold] = []
    queries: list[QueryRecord] = []
    for raw in prefixes_raw:
        source_prefix_id = str(raw["prefix_id"])
        prefix_id = f"legacy:{source_prefix_id}"
        source_nodes = sorted(
            raw["graph"]["nodes"],
            key=lambda item: (int(item["step_id"]), str(item["node_id"])),
        )
        id_map = {
            str(node["node_id"]): f"{prefix_id}:E{index:03d}"
            for index, node in enumerate(source_nodes, 1)
        }
        events: list[dict[str, Any]] = []
        for index, node in enumerate(source_nodes, 1):
            source_id = str(node["node_id"])
            events.append(
                {
                    "event_id": id_map[source_id],
                    "step_id": index,
                    "kind": str(node["node_type"]),
                    "content": node.get("content"),
                    "causal_role": _legacy_event_role(source_id, node),
                    "token_count": int(node.get("token_count") or 0),
                    "side_effect": bool(node.get("side_effect")),
                    **(
                        {"source_message_ordinal": int(node["metadata"]["source_message_ordinal"])}
                        if node.get("metadata", {}).get("source_message_ordinal")
                        else {}
                    ),
                    **(
                        {"call_id": str(node["metadata"]["call_id"])}
                        if node.get("metadata", {}).get("call_id")
                        else {}
                    ),
                    **(
                        {"tool_name": str(node["metadata"]["tool_name"])}
                        if node.get("metadata", {}).get("tool_name")
                        else {}
                    ),
                }
            )
        recoverability = _legacy_recoverability(raw)
        prefix = PrefixRecord(
            prefix_id=prefix_id,
            source_kind="legacy_diagnostic",
            source_ref={
                "source_prefix_id": source_prefix_id,
                "source_prefix_hash": raw["prefix_hash"],
                "source_schema_version": raw["schema_version"],
            },
            split="dev",
            failure_family=str(raw["scenario_family"]),
            task_domain="legacy_phase6",
            recoverability=recoverability,
            context_length=("short" if raw["payload_target_tokens"] <= 256 else "long"),
            budget_tokens=1024,
            events=tuple(events),
            messages=tuple(dict(item) for item in raw["messages"]),
            tool_schemas=tuple(dict(item) for item in raw["tool_schemas"]),
            environment_snapshot={
                "snapshot_id": raw["prefix_hash"],
                "deterministic": True,
                "history_reconstructable": recoverability != "R0",
                "unsafe_to_repeat_failed_action": recoverability == "R3",
                "side_effects_sandboxed": True,
                "allowed_reacquisition_tools": (
                    []
                    if recoverability == "R0"
                    else ["read_audit_log"]
                    if recoverability == "R1"
                    else ["read_audit_log", "simulate_replay", "repeat_failed_action"]
                    if recoverability == "R3"
                    else ["inspect_environment", "replay_in_sandbox", "read_audit_log"]
                ),
            },
        )
        fork = forks_by_prefix[source_prefix_id]
        original_required_source = [str(item) for item in fork["required_subgraph_event_ids"]]
        source_order = {str(item["node_id"]): index for index, item in enumerate(source_nodes)}
        required_source = sorted(original_required_source, key=lambda item: source_order[item])
        required_nodes = [
            next((item for item in source_nodes if str(item["node_id"]) == source_id), None)
            for source_id in required_source
        ]
        call_nodes = [
            item for item in required_nodes if item and item.get("node_type") == "tool_call"
        ]
        observation_nodes = [
            item
            for item in required_nodes
            if item and item.get("node_type") in {"error", "observation"}
        ]
        failed_action, failed_arguments = _legacy_action(
            call_nodes[0] if call_nodes else None, "historical_action"
        )
        replacement_action, replacement_arguments = _legacy_action(
            call_nodes[-1] if len(call_nodes) > 1 else None, "historical_resolution"
        )
        failure_node = next(
            (item for item in observation_nodes if item.get("node_type") == "error"),
            observation_nodes[0] if observation_nodes else None,
        )
        failure_content = failure_node.get("content") if failure_node else {}
        if isinstance(failure_content, dict):
            error_signature = str(
                failure_content.get("error") or fork["expected_answer_facts"][0]
            )
            diagnostic = str(
                failure_content.get("detail") or fork["expected_answer_facts"][0]
            )
        else:
            error_signature = str(fork["expected_answer_facts"][0])
            diagnostic = str(failure_content or fork["expected_answer_facts"][0])
        decision_node = next(
            (item for item in required_nodes if item and item.get("node_type") == "decision"),
            None,
        )
        switch = str(
            decision_node.get("content") if decision_node else fork["expected_answer_facts"][0]
        )
        resolution_node = observation_nodes[-1] if observation_nodes else None
        resolution = str(
            resolution_node.get("content") if resolution_node else fork["expected_answer_facts"][0]
        )
        ordered = tuple(id_map[item] for item in required_source if item in id_map)
        chain_applicable = str(raw["scenario_family"]) in {
            "F1_shell_switch",
            "F2_failed_approach",
        }
        current_ids = tuple(
            event["event_id"] for event in events if event["causal_role"] == "current_fact"
        )

        def evidence_ids(node: Mapping[str, Any] | None) -> tuple[str, ...]:
            return (id_map[str(node["node_id"])],) if node else ()

        first_call = call_nodes[0] if call_nodes else None
        replacement_call_node = call_nodes[-1] if len(call_nodes) > 1 else None
        current_fact = next(
            (
                canonical_json(event["content"])
                for event in reversed(events)
                if event["event_id"] in current_ids
            ),
            "",
        )
        gold = FailureChainGold(
            prefix_id=prefix_id,
            failed_action=failed_action,
            failed_arguments=failed_arguments,
            error_signature=error_signature,
            diagnostic_evidence=diagnostic,
            switch_decision=switch,
            replacement_action=replacement_action,
            replacement_arguments=replacement_arguments,
            resolution_evidence=resolution,
            ordered_event_ids=ordered,
            evidence_by_field={
                "failed_action": evidence_ids(first_call),
                "failed_arguments": evidence_ids(first_call),
                "failure_cause": evidence_ids(failure_node),
                "diagnostic_evidence": evidence_ids(failure_node),
                "switch_decision": evidence_ids(decision_node),
                "replacement_action": evidence_ids(replacement_call_node),
                "replacement_arguments": evidence_ids(replacement_call_node),
                "resolution_evidence": evidence_ids(resolution_node),
                "ordered_event_ids": ordered,
                "current_fact": current_ids,
            },
            recoverability=recoverability,
            current_fact=current_fact,
            current_event_ids=current_ids,
            source_event_ids={id_map[key]: key for key in id_map},
            chain_applicable=chain_applicable,
            annotation={
                "gold_source": "legacy_reactivate_fork",
                "expected_answer_facts": list(fork["expected_answer_facts"]),
                "human_annotated": False,
                "original_required_source_event_ids": original_required_source,
                "non_failure_legacy_diagnostic": not chain_applicable,
            },
        )
        prefixes.append(prefix)
        gold_rows.append(gold)
        queries.extend(build_queries(prefix))
    return prefixes, gold_rows, queries


def load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported compression audit config")
    controlled = value.get("controlled", {})
    if int(controlled.get("prefix_count", 0)) != 240:
        raise ValueError("controlled prefix_count must remain 240")
    if int(controlled.get("queries_per_prefix", 0)) != 6:
        raise ValueError("controlled queries_per_prefix must remain 6")
    real = value.get("real", {})
    if int(real.get("swe_gym_quota", 0)) != 60 or int(real.get("ama_bench_quota", 0)) != 40:
        raise ValueError("real source quotas must remain 60 SWE-Gym and 40 AMA-Bench")
    limits = value.get("v0_live", {}).get("limits", {})
    if int(limits.get("request_count_hard_max", 0)) != 368:
        raise ValueError("v0 request_count_hard_max must remain 368")
    if float(limits.get("maximum_cost_cny", 0)) != 100.0:
        raise ValueError("v0 maximum_cost_cny must remain 100")
    return value


def _expected_real_revisions(config: Mapping[str, Any]) -> dict[str, str]:
    by_id = {
        str(item.get("id")): str(item.get("trajectory_revision") or "")
        for item in config.get("real", {}).get("sources", ())
    }
    return {
        "swe_gym": by_id.get("swe_gym_openhands", ""),
        "ama_bench": by_id.get("ama_bench", ""),
    }


def _copy_legacy(legacy_root: Path, destination: Path) -> dict[str, Any]:
    names = (
        "prefixes.jsonl",
        "forks.jsonl",
        "lifecycle_gold.jsonl",
        "manager_outputs.jsonl",
        "manifest.json",
    )
    destination.mkdir(parents=True, exist_ok=True)
    source_hashes: dict[str, str] = {}
    for name in names:
        source = legacy_root / name
        if not source.is_file():
            raise FileNotFoundError(source)
        source_hashes[name] = file_sha256(source)
        shutil.copyfile(source, destination / name)
        if file_sha256(destination / name) != source_hashes[name]:
            raise ValueError(f"legacy copy hash mismatch: {name}")
    wrapper = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "role": "legacy_diagnostic_only",
        "ranked": False,
        "source_root": str(legacy_root),
        "source_hashes": source_hashes,
        "preserves_original_ids": True,
        "preserves_original_results": True,
    }
    _write_json(destination / "compatibility_manifest.json", wrapper)
    return wrapper


def _annotation_template(source: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "candidate_id": "",
        "repository": "",
        "task_id": "",
        "split": "",
        "prefix": {},
        "failure_chain": {
            "failed_action": "",
            "failed_arguments": {},
            "error_signature": "",
            "diagnostic_evidence": "",
            "switch_decision": "",
            "replacement_action": "",
            "replacement_arguments": {},
            "resolution_evidence": "",
            "ordered_source_event_ids": [],
            "evidence_source_event_ids_by_field": {},
            "recoverability": "",
        },
        "replay": {
            "docker_replayable": False,
            "snapshot_ref": "",
            "verification_command": "",
        },
        "annotator": "",
        "adjudicator": "",
        "annotator_a_sha256": "",
        "annotator_b_sha256": "",
        "annotation_status": "blank",
    }


def _write_real_annotation_scaffold(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "swe_gym.template.json", _annotation_template("swe_gym"))
    _write_json(destination / "ama_bench.template.json", _annotation_template("ama_bench"))
    for name in (
        "candidates.jsonl",
        "annotator_a.jsonl",
        "annotator_b.jsonl",
        "adjudicated.jsonl",
    ):
        _write_jsonl(destination / name, ())


def _copy_real_annotation_inputs(source: Path, destination: Path) -> bool:
    names = ("candidates.jsonl", "annotator_a.jsonl", "annotator_b.jsonl", "adjudicated.jsonl")
    if not source.is_dir() or not (source / "adjudicated.jsonl").is_file():
        return False
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = source / name
        if not path.is_file():
            raise FileNotFoundError(path)
        shutil.copyfile(path, destination / name)
    for name in ("source_manifest.json", "manifest.json", "file_manifest.jsonl"):
        path = source / name
        if path.is_file():
            shutil.copyfile(path, destination / name)
    for row in load_jsonl(source / "adjudicated.jsonl"):
        receipt = row.get("replay", {}).get("verification", {})
        log_ref = str(receipt.get("log_path") or "") if isinstance(receipt, dict) else ""
        if not log_ref:
            continue
        source_log = (source / log_ref).resolve()
        if not source_log.is_relative_to(source.resolve()) or not source_log.is_file():
            raise ValueError("replay verification log must exist inside the annotation bundle")
        target_log = destination / source_log.relative_to(source.resolve())
        if target_log.exists():
            if file_sha256(target_log) != file_sha256(source_log):
                raise ValueError("replay log collides with another annotation artifact")
        else:
            target_log.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_log, target_log)
    for source_name in ("swe_gym", "ama_bench"):
        template = source / f"{source_name}.template.json"
        if template.is_file():
            shutil.copyfile(template, destination / template.name)
        else:
            _write_json(destination / template.name, _annotation_template(source_name))
    return True


def import_adjudicated_real(
    annotation_root: Path,
) -> tuple[list[PrefixRecord], list[FailureChainGold], list[QueryRecord]]:
    """Convert 100 adjudicated normalized trajectories into opaque benchmark records."""

    rows = load_jsonl(annotation_root / "adjudicated.jsonl")
    prefixes: list[PrefixRecord] = []
    gold_rows: list[FailureChainGold] = []
    queries: list[QueryRecord] = []
    for row in rows:
        if row.get("annotation_status") != "adjudicated":
            raise ValueError("real import accepts adjudicated rows only")
        source = _nonempty(row.get("source"), "real source")
        if source not in {"swe_gym", "ama_bench"}:
            raise ValueError(f"unsupported real source: {source}")
        candidate_id = _nonempty(row.get("candidate_id"), "candidate_id")
        repository = _nonempty(row.get("repository"), "repository")
        task_id = _nonempty(row.get("task_id"), "task_id")
        split = _nonempty(row.get("split"), "real split")
        if split not in SPLITS:
            raise ValueError(f"invalid real split: {split}")
        raw_prefix = row.get("prefix")
        if not isinstance(raw_prefix, dict):
            raise ValueError(f"real prefix must be an object: {candidate_id}")
        raw_events = raw_prefix.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            raise ValueError(f"real prefix requires normalized events: {candidate_id}")
        prefix_id = f"real:{source}:{stable_digest(candidate_id)[:16]}"
        source_ids = []
        for index, event in enumerate(raw_events, 1):
            if not isinstance(event, dict):
                raise ValueError(f"real event must be an object: {candidate_id}/{index}")
            source_ids.append(
                _nonempty(
                    event.get("source_event_id") or event.get("event_id"),
                    "source_event_id",
                )
            )
        if len(set(source_ids)) != len(source_ids):
            raise ValueError(f"duplicate real source event ID: {candidate_id}")
        id_map = {
            source_id: f"{prefix_id}:E{index:03d}"
            for index, source_id in enumerate(source_ids, 1)
        }
        events = []
        for index, (source_id, raw_event) in enumerate(
            zip(source_ids, raw_events, strict=True), 1
        ):
            events.append(
                {
                    "event_id": id_map[source_id],
                    "step_id": index,
                    "kind": _nonempty(raw_event.get("kind"), "real event kind"),
                    "content": raw_event.get("content"),
                    "causal_role": str(raw_event.get("causal_role") or "unclassified"),
                    "token_count": int(
                        raw_event.get("token_count") or estimate_tokens(raw_event.get("content"))
                    ),
                    "side_effect": bool(raw_event.get("side_effect")),
                    **(
                        {"source_message_ordinal": int(raw_event["source_message_ordinal"])}
                        if raw_event.get("source_message_ordinal")
                        else {}
                    ),
                    **(
                        {"call_id": str(raw_event["call_id"])}
                        if raw_event.get("call_id")
                        else {}
                    ),
                    **(
                        {"tool_name": str(raw_event["tool_name"])}
                        if raw_event.get("tool_name")
                        else {}
                    ),
                }
            )
        chain = row.get("failure_chain")
        if not isinstance(chain, dict):
            raise ValueError(f"missing adjudicated failure chain: {candidate_id}")
        ordered_source = _tuple_strings(chain.get("ordered_source_event_ids", ()))
        if any(item not in id_map for item in ordered_source):
            raise ValueError(f"real gold references an unknown source event: {candidate_id}")
        ordered = tuple(id_map[item] for item in ordered_source)
        recoverability = _nonempty(chain.get("recoverability"), "recoverability")
        current_source = _tuple_strings(raw_prefix.get("current_source_event_ids", ()))
        if any(item not in id_map for item in current_source):
            raise ValueError(f"real current fact references an unknown event: {candidate_id}")
        current_ids = tuple(id_map[item] for item in current_source)
        snapshot = dict(raw_prefix.get("environment_snapshot") or {})
        snapshot.update(
            {
                "source": source,
                "docker_replayable": bool(row.get("replay", {}).get("docker_replayable")),
                "snapshot_ref": str(row.get("replay", {}).get("snapshot_ref") or ""),
                "side_effects_sandboxed": True,
                "history_reconstructable": recoverability != "R0",
                "unsafe_to_repeat_failed_action": recoverability == "R3",
            }
        )
        prefix = PrefixRecord(
            prefix_id=prefix_id,
            source_kind="real_trajectory",
            source_ref={
                "source": source,
                "candidate_id": candidate_id,
                "repository": repository,
                "task_id": task_id,
                "trajectory_revision": str(row.get("trajectory_revision") or ""),
            },
            split=split,
            failure_family=_nonempty(chain.get("failure_family"), "failure_family"),
            task_domain=str(raw_prefix.get("task_domain") or "software"),
            recoverability=recoverability,
            context_length=str(raw_prefix.get("context_length") or "natural"),
            budget_tokens=int(raw_prefix.get("budget_tokens") or 2048),
            events=tuple(events),
            messages=tuple(dict(item) for item in raw_prefix.get("messages", ())),
            tool_schemas=tuple(dict(item) for item in raw_prefix.get("tool_schemas", ())),
            environment_snapshot=snapshot,
        )

        role_ids: dict[str, tuple[str, ...]] = {}
        for role in FAILURE_ROLES:
            role_ids[role] = tuple(
                str(item["event_id"])
                for item in events
                if item.get("causal_role") == role
            )
        evidence: dict[str, tuple[str, ...]] = {
            "failed_action": role_ids["failed_action"],
            "failed_arguments": role_ids["failed_action"],
            "failure_cause": role_ids["failure_result"] + role_ids["diagnostic_evidence"],
            "diagnostic_evidence": (
                role_ids["failure_result"] + role_ids["diagnostic_evidence"]
            ),
            "switch_decision": role_ids["switch_decision"],
            "replacement_action": role_ids["replacement_action"],
            "replacement_arguments": role_ids["replacement_action"],
            "resolution_evidence": role_ids["resolution_evidence"],
            "ordered_event_ids": ordered,
            "current_fact": current_ids,
        }
        explicit_evidence = chain.get("evidence_source_event_ids_by_field") or {}
        if not isinstance(explicit_evidence, dict):
            raise ValueError(
                f"real evidence_source_event_ids_by_field must be an object: {candidate_id}"
            )
        for field_name, raw_ids in explicit_evidence.items():
            source_evidence_ids = _tuple_strings(raw_ids or ())
            if any(item not in id_map for item in source_evidence_ids):
                raise ValueError(
                    f"real field evidence references an unknown source event: "
                    f"{candidate_id}/{field_name}"
                )
            evidence[str(field_name)] = tuple(id_map[item] for item in source_evidence_ids)
        for arguments_field, action_field in (
            ("failed_arguments", "failed_action"),
            ("replacement_arguments", "replacement_action"),
        ):
            if arguments_field not in explicit_evidence:
                evidence[arguments_field] = evidence[action_field]
        gold = FailureChainGold(
            prefix_id=prefix_id,
            failed_action=_nonempty(chain.get("failed_action"), "failed_action"),
            failed_arguments=dict(chain.get("failed_arguments") or {}),
            error_signature=_nonempty(chain.get("error_signature"), "error_signature"),
            diagnostic_evidence=_nonempty(
                chain.get("diagnostic_evidence"), "diagnostic_evidence"
            ),
            switch_decision=_nonempty(chain.get("switch_decision"), "switch_decision"),
            replacement_action=_nonempty(
                chain.get("replacement_action"), "replacement_action"
            ),
            replacement_arguments=dict(chain.get("replacement_arguments") or {}),
            resolution_evidence=_nonempty(
                chain.get("resolution_evidence"), "resolution_evidence"
            ),
            ordered_event_ids=ordered,
            evidence_by_field=evidence,
            recoverability=recoverability,
            current_fact=str(raw_prefix.get("current_fact") or ""),
            current_event_ids=current_ids,
            source_event_ids={id_map[key]: key for key in id_map},
            annotation={
                "gold_source": "double_human_adjudication",
                "human_annotated": True,
                "annotator_a_sha256": str(
                    row.get("annotator_a_sha256") or row.get("annotator_a_hash") or ""
                ),
                "annotator_b_sha256": str(
                    row.get("annotator_b_sha256") or row.get("annotator_b_hash") or ""
                ),
                "adjudicator": str(row.get("adjudicator") or ""),
            },
        )
        item_queries = list(build_queries(prefix))
        if source == "ama_bench":
            item_queries = [item for item in item_queries if item.track == "audit_qa"]
        prefixes.append(prefix)
        gold_rows.append(gold)
        queries.extend(item_queries)
    return prefixes, gold_rows, queries


def artifact_manifest(root: Path) -> list[dict[str, Any]]:
    """Hash every run artifact except the two self-referential manifest files."""

    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in {"manifest.json", "file_manifest.jsonl"}:
            continue
        rows.append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": file_sha256(path)}
        )
    return rows


def write_file_manifest(root: Path) -> list[dict[str, Any]]:
    rows = artifact_manifest(root)
    _write_jsonl(root / "file_manifest.jsonl", rows)
    return rows


def verify_file_manifest(root: Path) -> None:
    claimed = load_jsonl(root / "file_manifest.jsonl")
    actual = artifact_manifest(root)
    if stable_digest(sorted(claimed, key=lambda row: str(row.get("path")))) != stable_digest(actual):
        raise ValueError(f"artifact SHA-256 manifest does not match files: {root}")


def build_benchmark(
    config_path: Path,
    output_root: Path,
    *,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Build v0.1 diagnostic and the controlled portion of v1 without provider calls."""

    config = load_config(config_path)
    root = (workspace or Path.cwd()).resolve()
    destination = output_root.resolve()
    legacy_root = (root / str(config["legacy"]["input_root"])).resolve()
    expected_legacy_hashes = dict(config["legacy"].get("expected_hashes") or {})
    for name in (
        "prefixes.jsonl",
        "forks.jsonl",
        "lifecycle_gold.jsonl",
        "manager_outputs.jsonl",
        "manifest.json",
    ):
        source = legacy_root / name
        if not source.is_file():
            raise FileNotFoundError(source)
        expected = expected_legacy_hashes.get(name)
        if expected is not None and file_sha256(source) != str(expected):
            raise ValueError(f"legacy source hash mismatch: {name}")
    _assert_output_available(destination)
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "config.snapshot.json", config)

    legacy_wrapper = _copy_legacy(legacy_root, destination / "legacy_diagnostic" / "original")
    legacy_prefixes, legacy_gold, legacy_queries = convert_legacy_diagnostic(legacy_root)
    _write_jsonl(
        destination / "legacy_diagnostic" / "audit_prefixes.jsonl",
        (item.to_dict() for item in legacy_prefixes),
    )
    _write_jsonl(
        destination / "legacy_diagnostic" / "audit_queries.jsonl",
        (item.to_dict() for item in legacy_queries),
    )
    _write_jsonl(
        destination / "legacy_diagnostic" / "audit_gold.jsonl",
        (item.to_dict() for item in legacy_gold),
    )

    controlled_prefixes, controlled_gold, controlled_queries = generate_controlled_dataset(
        base_seed=int(config["controlled"]["base_seed"])
    )
    public = destination / "public"
    private = destination / "private"
    _write_jsonl(public / "prefixes.jsonl", (item.to_dict() for item in controlled_prefixes))
    _write_jsonl(public / "queries.jsonl", (item.to_dict() for item in controlled_queries))
    _write_jsonl(
        public / "dev_validation_gold.jsonl",
        (item.to_dict() for item in controlled_gold if item.prefix_id.split(":")[1] in {
            template["id"] for template in FAILURE_TEMPLATES[:4]
        }),
    )
    _write_jsonl(
        private / "test_gold.jsonl",
        (item.to_dict() for item in controlled_gold if item.prefix_id.split(":")[1] in {
            template["id"] for template in FAILURE_TEMPLATES[4:]
        }),
    )
    _write_jsonl(private / "all_gold.jsonl", (item.to_dict() for item in controlled_gold))
    annotation_destination = destination / "annotations" / "real"
    annotation_source = (
        root / str(config["real"].get("normalized_annotation_root", ""))
    ).resolve()
    annotations_copied = _copy_real_annotation_inputs(
        annotation_source, annotation_destination
    ) if config["real"].get("normalized_annotation_root") else False
    if not annotations_copied:
        _write_real_annotation_scaffold(annotation_destination)
    real_validation = validate_real_annotations(
        annotation_destination,
        expected_revisions=_expected_real_revisions(config),
    )
    real_prefixes: list[PrefixRecord] = []
    real_gold: list[FailureChainGold] = []
    real_queries: list[QueryRecord] = []
    if real_validation["ready"]:
        real_prefixes, real_gold, real_queries = import_adjudicated_real(
            annotation_destination
        )
        _write_jsonl(public / "real_prefixes.jsonl", (item.to_dict() for item in real_prefixes))
        _write_jsonl(public / "real_queries.jsonl", (item.to_dict() for item in real_queries))
        _write_jsonl(
            public / "real_dev_validation_gold.jsonl",
            (item.to_dict() for item in real_gold if next(
                prefix.split for prefix in real_prefixes if prefix.prefix_id == item.prefix_id
            ) != "test"),
        )
        _write_jsonl(
            private / "real_test_gold.jsonl",
            (item.to_dict() for item in real_gold if next(
                prefix.split for prefix in real_prefixes if prefix.prefix_id == item.prefix_id
            ) == "test"),
        )
        _write_jsonl(private / "real_all_gold.jsonl", (item.to_dict() for item in real_gold))

    split_counts = {
        split: sum(item.split == split for item in controlled_prefixes) for split in SPLITS
    }
    file_rows = write_file_manifest(destination)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "release": "v0.1-diagnostic",
        "config_path": str(config_path),
        "config_sha256": file_sha256(config_path),
        "repository": git_provenance(root),
        "implementation": implementation_provenance(root),
        "data_revision": stable_digest(
            {
                "legacy_source_hashes": legacy_wrapper["source_hashes"],
                "controlled_base_seed": config["controlled"]["base_seed"],
                "controlled_prefix_hashes": [item.prefix_hash for item in controlled_prefixes],
                "real_annotation_gate": real_validation,
            }
        ),
        "external_provider_calls": 0,
        "legacy": {
            "prefix_count": len(legacy_prefixes),
            "original_fork_count": 72,
            "ranked": False,
            "source_hashes": legacy_wrapper["source_hashes"],
        },
        "controlled": {
            "prefix_count": len(controlled_prefixes),
            "query_count": len(controlled_queries),
            "episode_count": len(controlled_queries),
            "split_counts": split_counts,
            "synthetic": True,
        },
        "real": {
            "adjudicated_count": real_validation["adjudicated_count"],
            "swe_gym_count": real_validation.get("swe_gym_count", 0),
            "ama_bench_count": real_validation.get("ama_bench_count", 0),
            "imported_prefix_count": len(real_prefixes),
            "imported_query_count": len(real_queries),
            "status": (
                "ready" if real_validation["ready"] else "awaiting_double_human_annotation"
            ),
            "annotation_gate": real_validation,
        },
        "v1_ready": bool(real_validation["ready"] and len(real_prefixes) == 100),
        "interpretation": (
            "The 72 legacy forks are compatibility diagnostics, not independent benchmark "
            "samples. Formal v1 claims remain blocked until the real-data annotation gate passes."
        ),
        "artifacts": file_rows,
    }
    _write_json(destination / "manifest.json", manifest)
    return manifest


def _tool_pair_errors(prefix: PrefixRecord) -> list[str]:
    calls: dict[str, Mapping[str, Any]] = {}
    results: dict[str, list[Mapping[str, Any]]] = {}
    for event in prefix.events:
        call_id = event.get("call_id")
        if not call_id:
            continue
        if event.get("kind") == "tool_call":
            if str(call_id) in calls:
                return [f"duplicate tool call ID in {prefix.prefix_id}: {call_id}"]
            calls[str(call_id)] = event
        elif event.get("kind") in {"observation", "error", "tool_result"}:
            results.setdefault(str(call_id), []).append(event)
    errors = []
    for call_id in calls:
        if len(results.get(call_id, ())) != 1:
            errors.append(f"tool call {call_id} does not have exactly one result")
    for call_id in results:
        if call_id not in calls:
            errors.append(f"tool result {call_id} has no call")
    return errors


def _query_leaks_gold(query: QueryRecord, gold: FailureChainGold) -> list[str]:
    text = " ".join(query.text.lower().split())
    leaks = []
    protected = (
        ("error_signature", gold.error_signature),
        ("failed_action", gold.failed_action),
        ("replacement_action", gold.replacement_action),
    )
    for field_name, value in protected:
        normalized = " ".join(value.lower().split())
        if normalized and len(normalized) >= 5 and normalized in text:
            leaks.append(f"{query.query_id} leaks {field_name}")
    return leaks


def _cohen_kappa(left: Sequence[str], right: Sequence[str]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / len(left)
    labels = sorted(set(left).union(right))
    expected = sum(
        (left.count(label) / len(left)) * (right.count(label) / len(right))
        for label in labels
    )
    if math.isclose(expected, 1.0):
        return None  # Chance-corrected agreement is undefined for a constant category.
    return (observed - expected) / (1.0 - expected)


def _event_f1(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    precision = len(a.intersection(b)) / len(a)
    recall = len(a.intersection(b)) / len(b)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def validate_real_annotations(
    annotation_root: Path,
    *,
    expected_revisions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    paths = {
        name: annotation_root / f"{name}.jsonl"
        for name in ("candidates", "annotator_a", "annotator_b", "adjudicated")
    }
    if not all(path.is_file() for path in paths.values()):
        return {
            "ready": False,
            "reason": "annotation_files_missing",
            "adjudicated_count": 0,
        }
    rows = {name: load_jsonl(path) for name, path in paths.items()}
    keyed = {
        name: {str(item.get("candidate_id")): item for item in values}
        for name, values in rows.items()
    }
    duplicate_ids = {
        name: len(values) != len(keyed[name]) for name, values in rows.items()
    }
    annotation_ids = set(keyed["annotator_a"])
    shared = sorted(annotation_ids.intersection(keyed["annotator_b"]))
    selected_ids = set(keyed["adjudicated"])
    candidate_provenance_complete = bool(selected_ids) and selected_ids.issubset(
        set(keyed["candidates"])
    )
    category_fields = ("failure_family", "recoverability")
    category_kappas = {
        field_name: _cohen_kappa(
            [
                str(
                    keyed["annotator_a"][key]
                    .get("failure_chain", {})
                    .get(field_name, "")
                )
                for key in shared
            ],
            [
                str(
                    keyed["annotator_b"][key]
                    .get("failure_chain", {})
                    .get(field_name, "")
                )
                for key in shared
            ],
        )
        for field_name in category_fields
    }

    def annotation_evidence(item: Mapping[str, Any]) -> tuple[str, ...]:
        chain = item.get("failure_chain", {})
        values = list(chain.get("ordered_source_event_ids", ()))
        explicit = chain.get("evidence_source_event_ids_by_field", {})
        if isinstance(explicit, dict):
            for event_ids in explicit.values():
                values.extend(event_ids or ())
        return _tuple_strings(values)

    evidence_f1 = [
        _event_f1(
            annotation_evidence(keyed["annotator_a"][key]),
            annotation_evidence(keyed["annotator_b"][key]),
        )
        for key in shared
    ]
    adjudicated = list(keyed["adjudicated"].values())
    swe = sum(item.get("source") == "swe_gym" for item in adjudicated)
    ama = sum(item.get("source") == "ama_bench" for item in adjudicated)
    replayable = sum(
        item.get("source") == "swe_gym"
        and bool(item.get("replay", {}).get("docker_replayable"))
        for item in adjudicated
    )

    def replay_verified(item: Mapping[str, Any]) -> bool:
        replay = item.get("replay", {})
        if item.get("source") != "swe_gym" or not replay.get("docker_replayable"):
            return False
        receipt = replay.get("verification", {})
        if not isinstance(receipt, dict):
            return False
        log_ref = str(receipt.get("log_path") or "")
        log_path = (annotation_root / log_ref).resolve()
        if (
            not log_ref
            or not log_path.is_relative_to(annotation_root.resolve())
            or not log_path.is_file()
        ):
            return False
        return bool(
            receipt.get("status") == "passed"
            and receipt.get("exit_code") == 0
            and "@sha256:" in str(receipt.get("image_ref") or "")
            and isinstance(receipt.get("command"), list)
            and receipt["command"]
            and receipt.get("log_sha256") == file_sha256(log_path)
            and str(replay.get("snapshot_ref") or "").strip()
        )

    replay_verified_count = sum(replay_verified(item) for item in adjudicated)
    minimum_kappa = (
        min(float(value) for value in category_kappas.values() if value is not None)
        if category_kappas and all(value is not None for value in category_kappas.values())
        else None
    )
    mean_f1 = sum(evidence_f1) / len(evidence_f1) if evidence_f1 else None

    def adjudicated_complete(item: Mapping[str, Any]) -> bool:
        chain = item.get("failure_chain", {})
        prefix = item.get("prefix", {})
        events = prefix.get("events", ()) if isinstance(prefix, dict) else ()
        event_ids = {
            str(event.get("source_event_id") or event.get("event_id") or "")
            for event in events
            if isinstance(event, dict)
        }
        event_order = {
            str(event.get("source_event_id") or event.get("event_id") or ""): index
            for index, event in enumerate(events)
            if isinstance(event, dict)
        }
        ordered = _tuple_strings(chain.get("ordered_source_event_ids", ()))
        required_roles = set(FAILURE_ROLES)
        observed_roles = {
            str(event.get("causal_role"))
            for event in events
            if isinstance(event, dict) and event.get("causal_role")
        }
        explicit = chain.get("evidence_source_event_ids_by_field", {})
        explicit_ids = {
            str(event_id)
            for values in explicit.values()
            for event_id in (values or ())
        } if isinstance(explicit, dict) else set()
        required_evidence_fields = {
            "failed_action",
            "failure_cause",
            "diagnostic_evidence",
            "switch_decision",
            "replacement_action",
            "resolution_evidence",
        }
        explicit_complete = bool(
            isinstance(explicit, dict)
            and required_evidence_fields.issubset(
                {str(key) for key, values in explicit.items() if values}
            )
        )
        return bool(
            item.get("annotation_status") == "adjudicated"
            and str(item.get("adjudicator") or "").strip()
            and str(item.get("repository") or "").strip()
            and str(item.get("task_id") or "").strip()
            and len(str(item.get("trajectory_revision") or "")) >= 40
            and all(
                str(chain.get(field, "")).strip()
                for field in (
                    "failure_family",
                    "failed_action",
                    "error_signature",
                    "diagnostic_evidence",
                    "switch_decision",
                    "replacement_action",
                    "resolution_evidence",
                    "recoverability",
                )
            )
            and chain.get("recoverability") in RECOVERABILITY_LEVELS
            and len(ordered) >= 5
            and len(set(ordered)) == len(ordered)
            and set(ordered).issubset(event_ids)
            and [event_order.get(event_id, -1) for event_id in ordered]
            == sorted(event_order.get(event_id, -1) for event_id in ordered)
            and isinstance(chain.get("failed_arguments"), dict)
            and isinstance(chain.get("replacement_arguments"), dict)
            and explicit_ids.issubset(event_ids)
            and (required_roles.issubset(observed_roles) or explicit_complete)
        )

    complete = bool(adjudicated) and all(adjudicated_complete(item) for item in adjudicated)
    independent_annotators = bool(shared) and all(
        str(keyed["annotator_a"][key].get("annotator") or "").strip()
        and str(keyed["annotator_b"][key].get("annotator") or "").strip()
        and str(keyed["annotator_a"][key].get("annotator"))
        != str(keyed["annotator_b"][key].get("annotator"))
        and keyed["annotator_a"][key].get("annotation_status") == "annotated"
        and keyed["annotator_b"][key].get("annotation_status") == "annotated"
        for key in shared
    )
    annotation_hashes_linked = bool(selected_ids) and all(
        str(keyed["adjudicated"][key].get("annotator_a_sha256") or "")
        == stable_digest(keyed["annotator_a"][key])
        and str(keyed["adjudicated"][key].get("annotator_b_sha256") or "")
        == stable_digest(keyed["annotator_b"][key])
        for key in selected_ids.intersection(shared)
    )
    metadata_aligned = bool(selected_ids) and all(
        all(
            keyed[name][key].get(field_name)
            == keyed["adjudicated"][key].get(field_name)
            for name in ("annotator_a", "annotator_b")
            for field_name in (
                "source",
                "repository",
                "task_id",
                "trajectory_revision",
            )
        )
        for key in selected_ids.intersection(shared)
    )
    split_counts = {
        split: sum(item.get("split") == split for item in adjudicated) for split in SPLITS
    }
    repositories_by_split = {
        split: {
            str(item.get("repository"))
            for item in adjudicated
            if item.get("split") == split
        }
        for split in SPLITS
    }
    repository_leakage = sorted(
        {
            repository
            for index, left in enumerate(SPLITS)
            for right in SPLITS[index + 1 :]
            for repository in repositories_by_split[left].intersection(
                repositories_by_split[right]
            )
        }
    )
    tasks_by_split = {
        split: {
            (str(item.get("repository")), str(item.get("task_id")))
            for item in adjudicated
            if item.get("split") == split
        }
        for split in SPLITS
    }
    task_leakage = sorted(
        {
            task
            for index, left in enumerate(SPLITS)
            for right in SPLITS[index + 1 :]
            for task in tasks_by_split[left].intersection(tasks_by_split[right])
        }
    )
    exact_annotation_sets = bool(
        len(rows["annotator_a"]) == 100
        and len(rows["annotator_b"]) == 100
        and len(adjudicated) == 100
        and annotation_ids == set(keyed["annotator_b"]) == selected_ids
    )
    pinned_revisions_match = all(
        not expected_revisions
        or str(item.get("trajectory_revision") or "")
        == str(expected_revisions.get(str(item.get("source")), ""))
        for item in adjudicated
    )
    return {
        "ready": bool(
            exact_annotation_sets
            and not any(duplicate_ids.values())
            and swe == 60
            and ama == 40
            and replayable == 60
            and replay_verified_count == 60
            and split_counts == {"dev": 20, "validation": 20, "test": 60}
            and not repository_leakage
            and not task_leakage
            and minimum_kappa is not None
            and minimum_kappa >= 0.80
            and mean_f1 is not None
            and mean_f1 >= 0.90
            and complete
            and independent_annotators
            and annotation_hashes_linked
            and metadata_aligned
            and candidate_provenance_complete
            and pinned_revisions_match
        ),
        "shared_double_annotations": len(shared),
        "adjudicated_count": len(adjudicated),
        "swe_gym_count": swe,
        "ama_bench_count": ama,
        "swe_gym_replayable_count": replayable,
        "swe_gym_verified_replay_count": replay_verified_count,
        "split_counts": split_counts,
        "repository_leakage": repository_leakage,
        "task_leakage": task_leakage,
        "categorical_cohen_kappa_by_field": category_kappas,
        "minimum_categorical_cohen_kappa": minimum_kappa,
        "recoverability_cohen_kappa": category_kappas["recoverability"],
        "mean_evidence_event_f1": mean_f1,
        "adjudicated_fields_complete": complete,
        "independent_annotators": independent_annotators,
        "annotation_hashes_linked": annotation_hashes_linked,
        "metadata_aligned": metadata_aligned,
        "candidate_provenance_complete": candidate_provenance_complete,
        "exact_annotation_sets": exact_annotation_sets,
        "pinned_revisions_match": pinned_revisions_match,
        "duplicate_candidate_ids": duplicate_ids,
    }


def validate_benchmark(dataset_root: Path) -> dict[str, Any]:
    public = dataset_root / "public"
    private = dataset_root / "private"
    errors: list[str] = []
    warnings: list[str] = []
    try:
        prefixes = [PrefixRecord.from_dict(row) for row in load_jsonl(public / "prefixes.jsonl")]
        queries = [QueryRecord.from_dict(row) for row in load_jsonl(public / "queries.jsonl")]
        gold = [FailureChainGold.from_dict(row) for row in load_jsonl(private / "all_gold.jsonl")]
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        return {
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "benchmark_id": BENCHMARK_ID,
            "diagnostic_ready": False,
            "v1_ready": False,
            "errors": [str(error)],
            "warnings": [],
        }
    try:
        claimed_artifacts = load_jsonl(dataset_root / "file_manifest.jsonl")
        claimed_by_path = {str(item.get("path")): item for item in claimed_artifacts}
        actual_artifacts = artifact_manifest(dataset_root)
        actual_by_path = {str(item["path"]): item for item in actual_artifacts}
        if len(claimed_by_path) != len(claimed_artifacts):
            errors.append("file manifest contains duplicate paths")
        if set(claimed_by_path) != set(actual_by_path):
            errors.append("file manifest paths do not match benchmark artifacts")
        else:
            for path, actual in actual_by_path.items():
                claimed = claimed_by_path[path]
                if claimed.get("sha256") != actual["sha256"] or int(
                    claimed.get("bytes", -1)
                ) != int(actual["bytes"]):
                    errors.append(f"file manifest mismatch: {path}")
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"file manifest is invalid: {error}")
    prefix_by_id = {item.prefix_id: item for item in prefixes}
    gold_by_id = {item.prefix_id: item for item in gold}
    if len(prefix_by_id) != len(prefixes):
        errors.append("duplicate prefix IDs")
    if len({item.query_id for item in queries}) != len(queries):
        errors.append("duplicate query IDs")
    if len(prefixes) != 240:
        errors.append(f"expected 240 controlled prefixes, found {len(prefixes)}")
    if len(queries) != 1440:
        errors.append(f"expected 1440 controlled queries, found {len(queries)}")
    if len(gold) != 240:
        errors.append(f"expected 240 controlled gold rows, found {len(gold)}")

    queries_by_prefix: dict[str, list[QueryRecord]] = {}
    for query in queries:
        queries_by_prefix.setdefault(query.prefix_id, []).append(query)
        if query.prefix_id not in prefix_by_id:
            errors.append(f"query references missing prefix: {query.query_id}")
            continue
        if query.prefix_id in gold_by_id:
            errors.extend(_query_leaks_gold(query, gold_by_id[query.prefix_id]))
        if query.track == "audit_qa" and query.allowed_tools:
            errors.append(f"Audit-QA query exposes tools: {query.query_id}")
    for prefix in prefixes:
        if any("causal_role" in event for event in prefix.events):
            errors.append(f"hidden causal labels leaked into public prefix: {prefix.prefix_id}")
        kinds = {item.query_type for item in queries_by_prefix.get(prefix.prefix_id, ())}
        if kinds != set(QUERY_TYPES):
            errors.append(f"prefix does not have exactly six query types: {prefix.prefix_id}")
        errors.extend(_tool_pair_errors(prefix))
        item_gold = gold_by_id.get(prefix.prefix_id)
        if item_gold is None:
            errors.append(f"missing gold: {prefix.prefix_id}")
            continue
        event_ids = {str(item["event_id"]) for item in prefix.events}
        if not set(item_gold.ordered_event_ids).issubset(event_ids):
            errors.append(f"gold references missing event: {prefix.prefix_id}")
        steps = {str(item["event_id"]): int(item["step_id"]) for item in prefix.events}
        order = [steps[item] for item in item_gold.ordered_event_ids]
        if order != sorted(order):
            errors.append(f"failure chain is not chronological: {prefix.prefix_id}")
        if prefix.recoverability == "R0" and prefix.environment_snapshot.get(
            "history_reconstructable"
        ):
            errors.append(f"R0 history is incorrectly reconstructable: {prefix.prefix_id}")
        if prefix.recoverability == "R3" and not prefix.environment_snapshot.get(
            "unsafe_to_repeat_failed_action"
        ):
            errors.append(f"R3 prefix does not block unsafe replay: {prefix.prefix_id}")

    families_by_split = {
        split: {item.failure_family for item in prefixes if item.split == split}
        for split in SPLITS
    }
    for left_index, left in enumerate(SPLITS):
        for right in SPLITS[left_index + 1 :]:
            overlap = families_by_split[left].intersection(families_by_split[right])
            if overlap:
                errors.append(f"failure families leak across {left}/{right}: {sorted(overlap)}")
    split_counts = {split: sum(item.split == split for item in prefixes) for split in SPLITS}
    if split_counts != {"dev": 48, "validation": 48, "test": 144}:
        errors.append(f"unexpected split counts: {split_counts}")

    public_gold = load_jsonl(public / "dev_validation_gold.jsonl")
    if any(
        str(item.get("prefix_id")) not in prefix_by_id
        or prefix_by_id[str(item.get("prefix_id"))].split == "test"
        for item in public_gold
    ):
        errors.append("test gold leaked into public gold")
    try:
        legacy_prefixes = [
            PrefixRecord.from_dict(row)
            for row in load_jsonl(dataset_root / "legacy_diagnostic" / "audit_prefixes.jsonl")
        ]
        legacy_gold = {
            row.prefix_id: row for row in (
                FailureChainGold.from_dict(item)
                for item in load_jsonl(dataset_root / "legacy_diagnostic" / "audit_gold.jsonl")
            )
        }
        if len(legacy_prefixes) != 24:
            errors.append("legacy diagnostic must preserve 24 source prefixes")
        for prefix in legacy_prefixes:
            errors.extend(_tool_pair_errors(prefix))
            positions = {str(event["event_id"]): int(event["step_id"]) for event in prefix.events}
            chain = legacy_gold[prefix.prefix_id]
            chronological = [positions[item] for item in chain.ordered_event_ids]
            if chronological != sorted(chronological):
                errors.append(f"legacy gold is not chronological: {prefix.prefix_id}")
    except (ValueError, KeyError, FileNotFoundError) as error:
        errors.append(f"legacy diagnostic validation failed: {error}")
    snapshot_path = dataset_root / "config.snapshot.json"
    snapshot = (
        json.loads(snapshot_path.read_text(encoding="utf-8"))
        if snapshot_path.is_file()
        else {}
    )
    real_report = validate_real_annotations(
        dataset_root / "annotations" / "real",
        expected_revisions=_expected_real_revisions(snapshot) if snapshot else None,
    )
    if real_report["ready"]:
        try:
            real_prefixes = [
                PrefixRecord.from_dict(row)
                for row in load_jsonl(public / "real_prefixes.jsonl")
            ]
            real_queries = [
                QueryRecord.from_dict(row)
                for row in load_jsonl(public / "real_queries.jsonl")
            ]
            real_gold = [
                FailureChainGold.from_dict(row)
                for row in load_jsonl(private / "real_all_gold.jsonl")
            ]
            if len(real_prefixes) != 100 or len(real_gold) != 100:
                errors.append("formal real import must contain exactly 100 prefixes and gold rows")
            if len(real_queries) != 520:
                errors.append("formal real import must contain exactly 520 queries")
            real_ids = {item.prefix_id for item in real_prefixes}
            if len(real_ids) != len(real_prefixes):
                errors.append("formal real import contains duplicate prefix IDs")
            if any(item.prefix_id not in real_ids for item in real_queries):
                errors.append("formal real query references an unknown prefix")
            if any(item.prefix_id not in real_ids for item in real_gold):
                errors.append("formal real gold references an unknown prefix")
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            errors.append(f"formal real import is invalid: {error}")
    if not real_report["ready"]:
        warnings.append("formal v1 is blocked until 100 real chains pass double-human annotation")

    diagnostic_ready = not errors
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "diagnostic_ready": diagnostic_ready,
        "v1_ready": diagnostic_ready and bool(real_report["ready"]),
        "controlled": {
            "prefix_count": len(prefixes),
            "query_count": len(queries),
            "gold_count": len(gold),
            "split_counts": split_counts,
            "failure_family_counts": {
                family: sum(item.failure_family == family for item in prefixes)
                for family in sorted({item.failure_family for item in prefixes})
            },
            "recoverability_counts": {
                level: sum(item.recoverability == level for item in prefixes)
                for level in RECOVERABILITY_LEVELS
            },
        },
        "real": real_report,
        "errors": errors,
        "warnings": warnings,
    }
