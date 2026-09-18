"""Immutable public values produced by the graph-constrained context policy."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any


POLICY_API_VERSION = "context_policy_v4"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def stable_value_hash(value: Any) -> str:
    encoded = json.dumps(
        _thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    """Four independent lifecycle dimensions for one visible graph event."""

    event_id: str
    relevance: str
    validity: str
    storage: str
    retention_obligations: tuple[str, ...] = ()
    uncertain: bool = False
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "retention_obligations",
            tuple(sorted(set(map(str, self.retention_obligations)))),
        )
        object.__setattr__(self, "reasons", tuple(sorted(set(map(str, self.reasons)))))

    @property
    def hard(self) -> bool:
        return bool(self.retention_obligations) or self.uncertain

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "relevance": self.relevance,
            "validity": self.validity,
            "storage": self.storage,
            "retention_obligations": list(self.retention_obligations),
            "uncertain": self.uncertain,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LifecycleRecord":
        return cls(
            event_id=str(value["event_id"]),
            relevance=str(value["relevance"]),
            validity=str(value["validity"]),
            storage=str(value["storage"]),
            retention_obligations=tuple(
                map(str, value.get("retention_obligations", ()))
            ),
            uncertain=bool(value.get("uncertain", False)),
            reasons=tuple(map(str, value.get("reasons", ()))),
        )


@dataclass(frozen=True, slots=True)
class MemorySpan:
    """Atomic provider-history unit and its deterministic retrieval features."""

    span_id: str
    node_ids: tuple[str, ...]
    message_ordinals: tuple[int, ...]
    messages: tuple[Mapping[str, Any], ...]
    token_count: int
    hard: bool
    live: bool
    uncertain: bool
    archive_refs: tuple[str, ...] = ()
    dependency_span_ids: tuple[str, ...] = ()
    entity_terms: tuple[str, ...] = ()
    action_terms: tuple[str, ...] = ()
    error_terms: tuple[str, ...] = ()
    lexical_terms: tuple[str, ...] = ()
    chronological_index: int = 0

    def __post_init__(self) -> None:
        for field in (
            "node_ids",
            "message_ordinals",
            "archive_refs",
            "dependency_span_ids",
            "entity_terms",
            "action_terms",
            "error_terms",
            "lexical_terms",
        ):
            values = getattr(self, field)
            object.__setattr__(self, field, tuple(sorted(set(values))))
        object.__setattr__(
            self, "messages", tuple(_freeze(dict(message)) for message in self.messages)
        )
        if self.token_count < 0:
            raise ValueError("span token_count must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "node_ids": list(self.node_ids),
            "message_ordinals": list(self.message_ordinals),
            "messages": [_thaw(message) for message in self.messages],
            "token_count": self.token_count,
            "hard": self.hard,
            "live": self.live,
            "uncertain": self.uncertain,
            "archive_refs": list(self.archive_refs),
            "dependency_span_ids": list(self.dependency_span_ids),
            "entity_terms": list(self.entity_terms),
            "action_terms": list(self.action_terms),
            "error_terms": list(self.error_terms),
            "lexical_terms": list(self.lexical_terms),
            "chronological_index": self.chronological_index,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemorySpan":
        return cls(
            span_id=str(value["span_id"]),
            node_ids=tuple(map(str, value.get("node_ids", ()))),
            message_ordinals=tuple(map(int, value.get("message_ordinals", ()))),
            messages=tuple(dict(item) for item in value.get("messages", ())),
            token_count=int(value.get("token_count", 0)),
            hard=bool(value.get("hard", False)),
            live=bool(value.get("live", False)),
            uncertain=bool(value.get("uncertain", False)),
            archive_refs=tuple(map(str, value.get("archive_refs", ()))),
            dependency_span_ids=tuple(
                map(str, value.get("dependency_span_ids", ()))
            ),
            entity_terms=tuple(map(str, value.get("entity_terms", ()))),
            action_terms=tuple(map(str, value.get("action_terms", ()))),
            error_terms=tuple(map(str, value.get("error_terms", ()))),
            lexical_terms=tuple(map(str, value.get("lexical_terms", ()))),
            chronological_index=int(value.get("chronological_index", 0)),
        )


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """Immutable prefix-only input to query-time context materialization."""

    cutoff_step: int
    graph_hash: str
    lifecycle_records: tuple[LifecycleRecord, ...]
    hard_span_ids: tuple[str, ...]
    archive_refs: tuple[str, ...]
    policy_id: str
    policy_version: str
    budget_tokens: int
    spans: tuple[MemorySpan, ...]
    selected_span_ids: tuple[str, ...]
    goal_context: Mapping[str, Any]
    snapshot_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "lifecycle_records",
            tuple(sorted(self.lifecycle_records, key=lambda item: item.event_id)),
        )
        object.__setattr__(self, "spans", tuple(sorted(
            self.spans, key=lambda item: (item.chronological_index, item.span_id)
        )))
        for field in ("hard_span_ids", "archive_refs", "selected_span_ids"):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))
        object.__setattr__(self, "goal_context", _freeze(dict(self.goal_context)))
        expected = stable_value_hash(self.to_dict(include_hash=False))
        if self.snapshot_hash and self.snapshot_hash != expected:
            raise ValueError("MemorySnapshot hash mismatch")
        object.__setattr__(self, "snapshot_hash", expected)

    def span_map(self) -> dict[str, MemorySpan]:
        return {span.span_id: span for span in self.spans}

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": "memory_snapshot_v4",
            "cutoff_step": self.cutoff_step,
            "graph_hash": self.graph_hash,
            "lifecycle_records": [item.to_dict() for item in self.lifecycle_records],
            "hard_span_ids": list(self.hard_span_ids),
            "archive_refs": list(self.archive_refs),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "budget_tokens": self.budget_tokens,
            "spans": [span.to_dict() for span in self.spans],
            "selected_span_ids": list(self.selected_span_ids),
            "goal_context": _thaw(self.goal_context),
        }
        if include_hash:
            result["snapshot_hash"] = self.snapshot_hash
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemorySnapshot":
        return cls(
            cutoff_step=int(value["cutoff_step"]),
            graph_hash=str(value["graph_hash"]),
            lifecycle_records=tuple(
                LifecycleRecord.from_dict(item)
                for item in value.get("lifecycle_records", ())
            ),
            hard_span_ids=tuple(map(str, value.get("hard_span_ids", ()))),
            archive_refs=tuple(map(str, value.get("archive_refs", ()))),
            policy_id=str(value["policy_id"]),
            policy_version=str(value.get("policy_version", POLICY_API_VERSION)),
            budget_tokens=int(value["budget_tokens"]),
            spans=tuple(MemorySpan.from_dict(item) for item in value.get("spans", ())),
            selected_span_ids=tuple(map(str, value.get("selected_span_ids", ()))),
            goal_context=dict(value.get("goal_context", {})),
            snapshot_hash=str(value.get("snapshot_hash", "")),
        )


@dataclass(frozen=True, slots=True)
class ContextPlan:
    """Final provider input and auditable safety decision."""

    messages: tuple[Mapping[str, Any], ...]
    selected_span_ids: tuple[str, ...]
    retrieved_span_ids: tuple[str, ...]
    omitted_span_ids: tuple[str, ...]
    token_statistics: Mapping[str, int | float]
    send_eligible: bool
    safety_reasons: tuple[str, ...]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "messages", tuple(_freeze(dict(message)) for message in self.messages)
        )
        for field in ("selected_span_ids", "retrieved_span_ids", "omitted_span_ids"):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))
        object.__setattr__(
            self, "safety_reasons", tuple(sorted(set(map(str, self.safety_reasons))))
        )
        selected = set(self.selected_span_ids)
        if not set(self.retrieved_span_ids).issubset(selected):
            raise ValueError("retrieved spans must be selected")
        if selected.intersection(self.omitted_span_ids):
            raise ValueError("selected and omitted spans overlap")
        object.__setattr__(self, "token_statistics", _freeze(dict(self.token_statistics)))
        object.__setattr__(self, "provenance", _freeze(dict(self.provenance)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "context_plan_v4",
            "messages": [_thaw(message) for message in self.messages],
            "selected_span_ids": list(self.selected_span_ids),
            "retrieved_span_ids": list(self.retrieved_span_ids),
            "omitted_span_ids": list(self.omitted_span_ids),
            "token_statistics": _thaw(self.token_statistics),
            "send_eligible": self.send_eligible,
            "safety_reasons": list(self.safety_reasons),
            "provenance": _thaw(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextPlan":
        return cls(
            messages=tuple(dict(item) for item in value.get("messages", ())),
            selected_span_ids=tuple(map(str, value.get("selected_span_ids", ()))),
            retrieved_span_ids=tuple(map(str, value.get("retrieved_span_ids", ()))),
            omitted_span_ids=tuple(map(str, value.get("omitted_span_ids", ()))),
            token_statistics=dict(value.get("token_statistics", {})),
            send_eligible=bool(value.get("send_eligible", False)),
            safety_reasons=tuple(map(str, value.get("safety_reasons", ()))),
            provenance=dict(value.get("provenance", {})),
        )
def thaw_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return mutable provider payload copies without exposing snapshot state."""

    return [_thaw(message) for message in messages]
