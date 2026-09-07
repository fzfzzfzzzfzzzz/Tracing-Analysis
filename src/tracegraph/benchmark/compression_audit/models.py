"""Definitions moved from ``tracegraph.compression_audit``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from ...capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens

from .constants import (
    BENCHMARK_ID as BENCHMARK_ID,
    QUERY_TYPES as QUERY_TYPES,
    RECOVERABILITY_LEVELS as RECOVERABILITY_LEVELS,
    SCHEMA_VERSION as SCHEMA_VERSION,
    SPLITS as SPLITS,
    TRACKS as TRACKS,
)



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


# Imported after definitions so mutually-referential helpers initialize safely.
from .io import (
    _nonempty as _nonempty,
    _tuple_strings as _tuple_strings,
    canonical_json as canonical_json,
    stable_digest as stable_digest,
)
