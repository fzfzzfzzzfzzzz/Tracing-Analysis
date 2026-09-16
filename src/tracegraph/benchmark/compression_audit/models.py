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


FAILURE_EPISODE_SCHEMA_VERSION = "compression_audit_failure_episode_gold_v1"
FAILURE_EPISODE_SCOPE = "task_level_failure_episode"
FAILURE_EPISODE_OUTCOMES = ("intermediate_failure", "resolved")


def _require_unique(values: Sequence[str], name: str) -> tuple[str, ...]:
    result = tuple(map(str, values))
    if not result or any(not value for value in result):
        raise ValueError(f"{name} must contain nonempty event IDs")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} contains duplicate event IDs")
    return result


def _validate_acyclic(nodes: set[str], edges: Sequence[tuple[str, str]], name: str) -> None:
    if any(left == right or left not in nodes or right not in nodes for left, right in edges):
        raise ValueError(f"{name} contains an edge outside its evidence path")
    pending = set(nodes)
    while pending:
        roots = {
            node
            for node in pending
            if not any(right == node and left in pending for left, right in edges)
        }
        if not roots:
            raise ValueError(f"{name} contains a causal cycle")
        pending -= roots



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
class FailureEpisodeEvidencePath:
    evidence_ids: tuple[str, ...]
    constraints: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        evidence = _require_unique(self.evidence_ids, "episode evidence path")
        edges = tuple((str(left), str(right)) for left, right in self.constraints)
        if len(edges) != len(set(edges)):
            raise ValueError("episode evidence path contains duplicate causal edges")
        _validate_acyclic(set(evidence), edges, "episode evidence path")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_ids": list(self.evidence_ids),
            "constraints": [list(edge) for edge in self.constraints],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureEpisodeEvidencePath":
        raw_constraints = value.get("constraints", ())
        constraints: list[tuple[str, str]] = []
        for edge in raw_constraints:
            if not isinstance(edge, (list, tuple)) or len(edge) != 2:
                raise ValueError("episode causal constraints must contain two event IDs")
            constraints.append((str(edge[0]), str(edge[1])))
        return cls(
            evidence_ids=_tuple_strings(value.get("evidence_ids", ())),
            constraints=tuple(constraints),
        )


@dataclass(frozen=True, slots=True)
class FailureEpisodeEvidencePolicy:
    alternative_evidence_sets: tuple[tuple[str, ...], ...]
    relevant_evidence_ids: tuple[str, ...]
    causal_paths: tuple[FailureEpisodeEvidencePath, ...]

    def __post_init__(self) -> None:
        alternatives = tuple(
            _require_unique(values, "episode evidence alternative")
            for values in self.alternative_evidence_sets
        )
        if not alternatives:
            raise ValueError("episode evidence policy needs at least one alternative")
        if len({frozenset(values) for values in alternatives}) != len(alternatives):
            raise ValueError("episode evidence alternatives must be distinct")
        relevant = set(_require_unique(
            self.relevant_evidence_ids, "episode relevant evidence"
        ))
        if any(not set(values) <= relevant for values in alternatives):
            raise ValueError("episode evidence alternative is not relevant evidence")
        paths = tuple(self.causal_paths)
        if (
            len(paths) != len(alternatives)
            or {frozenset(path.evidence_ids) for path in paths}
            != {frozenset(values) for values in alternatives}
        ):
            raise ValueError("every episode evidence alternative needs one causal path")

    def to_dict(self) -> dict[str, Any]:
        return {
            "alternative_evidence_sets": [
                list(values) for values in self.alternative_evidence_sets
            ],
            "relevant_evidence_ids": list(self.relevant_evidence_ids),
            "causal_paths": [path.to_dict() for path in self.causal_paths],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureEpisodeEvidencePolicy":
        return cls(
            alternative_evidence_sets=tuple(
                _tuple_strings(values)
                for values in value.get("alternative_evidence_sets", ())
            ),
            relevant_evidence_ids=_tuple_strings(value.get("relevant_evidence_ids", ())),
            causal_paths=tuple(
                FailureEpisodeEvidencePath.from_dict(path)
                for path in value.get("causal_paths", ())
            ),
        )


@dataclass(frozen=True, slots=True)
class FailureEpisodeRepairStep:
    step_id: str
    decision_event_ids: tuple[str, ...]
    action_event_id: str
    result_event_ids: tuple[str, ...]
    outcome: str
    semantic_change: str

    def __post_init__(self) -> None:
        _nonempty(self.step_id, "repair step_id")
        _nonempty(self.action_event_id, "repair action_event_id")
        _require_unique(self.result_event_ids, "repair result_event_ids")
        if (
            any(not item for item in self.decision_event_ids)
            or len(self.decision_event_ids) != len(set(self.decision_event_ids))
        ):
            raise ValueError("repair decision_event_ids contains duplicates")
        if self.outcome not in FAILURE_EPISODE_OUTCOMES:
            raise ValueError(f"unsupported repair outcome: {self.outcome}")
        _nonempty(self.semantic_change, "repair semantic_change")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "decision_event_ids": list(self.decision_event_ids),
            "action_event_id": self.action_event_id,
            "result_event_ids": list(self.result_event_ids),
            "outcome": self.outcome,
            "semantic_change": self.semantic_change,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureEpisodeRepairStep":
        return cls(
            step_id=_nonempty(value.get("step_id"), "repair step_id"),
            decision_event_ids=_tuple_strings(value.get("decision_event_ids", ())),
            action_event_id=_nonempty(value.get("action_event_id"), "repair action_event_id"),
            result_event_ids=_tuple_strings(value.get("result_event_ids", ())),
            outcome=_nonempty(value.get("outcome"), "repair outcome"),
            semantic_change=_nonempty(value.get("semantic_change"), "repair semantic_change"),
        )


@dataclass(frozen=True, slots=True)
class FailureEpisodeGold:
    anchor_event_id: str
    initial_action_event_id: str
    initial_result_event_ids: tuple[str, ...]
    repair_steps: tuple[FailureEpisodeRepairStep, ...]
    resolution_event_ids: tuple[str, ...]
    recovery_sequence: str
    required_core_event_ids: tuple[str, ...]
    optional_support_event_ids: tuple[str, ...]
    chain_policy: FailureEpisodeEvidencePolicy
    query_policies: Mapping[str, FailureEpisodeEvidencePolicy] = field(default_factory=dict)
    scope: str = FAILURE_EPISODE_SCOPE
    schema_version: str = FAILURE_EPISODE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FAILURE_EPISODE_SCHEMA_VERSION:
            raise ValueError("unsupported failure episode schema_version")
        if self.scope != FAILURE_EPISODE_SCOPE:
            raise ValueError(f"unsupported failure episode scope: {self.scope}")
        if self.anchor_event_id != self.initial_action_event_id:
            raise ValueError("failure episode anchor must be its initial action")
        _require_unique(self.initial_result_event_ids, "initial failure results")
        steps = tuple(self.repair_steps)
        if not steps or len({step.step_id for step in steps}) != len(steps):
            raise ValueError("failure episode needs uniquely named repair steps")
        if steps[-1].outcome != "resolved" or any(
            step.outcome == "resolved" for step in steps[:-1]
        ):
            raise ValueError("only the final failure episode repair step may resolve the task")
        resolution = set(_require_unique(
            self.resolution_event_ids, "failure episode resolution"
        ))
        if not resolution <= set(steps[-1].result_event_ids):
            raise ValueError("resolution evidence must come from the final repair result")
        _nonempty(self.recovery_sequence, "failure episode recovery_sequence")
        _require_unique(self.required_core_event_ids, "failure episode required core")
        optional = tuple(map(str, self.optional_support_event_ids))
        if len(optional) != len(set(optional)) or any(not item for item in optional):
            raise ValueError("failure episode optional support contains invalid event IDs")
        if set(optional) & set(self.required_core_event_ids):
            raise ValueError("required and optional failure episode evidence must be disjoint")
        unknown_query_types = set(self.query_policies) - {
            "audit_recovery", "audit_chain", "interactive_reacquisition"
        }
        if unknown_query_types:
            raise ValueError(f"unsupported failure episode query policies: {unknown_query_types}")

    def policy_for_query(self, query_type: str) -> FailureEpisodeEvidencePolicy | None:
        if query_type == "audit_chain":
            return self.chain_policy
        return self.query_policies.get(query_type)

    def referenced_event_ids(self) -> set[str]:
        result = {
            self.anchor_event_id,
            self.initial_action_event_id,
            *self.initial_result_event_ids,
            *self.resolution_event_ids,
            *self.required_core_event_ids,
            *self.optional_support_event_ids,
        }
        policies = [self.chain_policy, *self.query_policies.values()]
        for policy in policies:
            result.update(policy.relevant_evidence_ids)
        for step in self.repair_steps:
            result.add(step.action_event_id)
            result.update(step.decision_event_ids)
            result.update(step.result_event_ids)
        return result

    def validate_against_prefix(self, prefix: PrefixRecord) -> None:
        events = {str(event["event_id"]): event for event in prefix.events}
        missing = self.referenced_event_ids() - set(events)
        if missing:
            raise ValueError(f"failure episode references missing events: {sorted(missing)}")
        if events[self.initial_action_event_id].get("kind") != "tool_call":
            raise ValueError("failure episode initial action must be a tool_call")
        for step in self.repair_steps:
            if events[step.action_event_id].get("kind") != "tool_call":
                raise ValueError(f"repair action must be a tool_call: {step.step_id}")
        order = {
            str(event["event_id"]): int(event["step_id"]) for event in prefix.events
        }
        if any(order[self.initial_action_event_id] >= order[event_id]
               for event_id in self.initial_result_event_ids):
            raise ValueError("initial failure results must follow the anchored action")
        previous = max(order[event_id] for event_id in self.initial_result_event_ids)
        for step in self.repair_steps:
            if order[step.action_event_id] <= previous:
                raise ValueError("failure episode repair actions must be chronological")
            if any(order[event_id] <= order[step.action_event_id]
                   for event_id in step.result_event_ids):
                raise ValueError("repair results must follow their action")
            previous = max(order[event_id] for event_id in step.result_event_ids)
        for policy in [self.chain_policy, *self.query_policies.values()]:
            for path in policy.causal_paths:
                if any(order[left] >= order[right] for left, right in path.constraints):
                    raise ValueError("failure episode causal edge contradicts public chronology")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope,
            "anchor_event_id": self.anchor_event_id,
            "initial_action_event_id": self.initial_action_event_id,
            "initial_result_event_ids": list(self.initial_result_event_ids),
            "repair_steps": [step.to_dict() for step in self.repair_steps],
            "resolution_event_ids": list(self.resolution_event_ids),
            "recovery_sequence": self.recovery_sequence,
            "required_core_event_ids": list(self.required_core_event_ids),
            "optional_support_event_ids": list(self.optional_support_event_ids),
            "chain_policy": self.chain_policy.to_dict(),
            "query_policies": {
                key: policy.to_dict() for key, policy in sorted(self.query_policies.items())
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureEpisodeGold":
        if value.get("schema_version") != FAILURE_EPISODE_SCHEMA_VERSION:
            raise ValueError("unsupported failure episode schema_version")
        chain_policy = value.get("chain_policy")
        if not isinstance(chain_policy, Mapping):
            raise ValueError("failure episode chain_policy is required")
        query_policies = value.get("query_policies") or {}
        if not isinstance(query_policies, Mapping):
            raise ValueError("failure episode query_policies must be an object")
        return cls(
            anchor_event_id=_nonempty(value.get("anchor_event_id"), "episode anchor"),
            initial_action_event_id=_nonempty(
                value.get("initial_action_event_id"), "episode initial action"
            ),
            initial_result_event_ids=_tuple_strings(
                value.get("initial_result_event_ids", ())
            ),
            repair_steps=tuple(
                FailureEpisodeRepairStep.from_dict(step)
                for step in value.get("repair_steps", ())
            ),
            resolution_event_ids=_tuple_strings(value.get("resolution_event_ids", ())),
            recovery_sequence=_nonempty(
                value.get("recovery_sequence"), "episode recovery_sequence"
            ),
            required_core_event_ids=_tuple_strings(
                value.get("required_core_event_ids", ())
            ),
            optional_support_event_ids=_tuple_strings(
                value.get("optional_support_event_ids", ())
            ),
            chain_policy=FailureEpisodeEvidencePolicy.from_dict(chain_policy),
            query_policies={
                str(key): FailureEpisodeEvidencePolicy.from_dict(policy)
                for key, policy in query_policies.items()
            },
            scope=str(value.get("scope") or ""),
        )


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
    failure_episode: FailureEpisodeGold | None = None
    source_event_ids: Mapping[str, str] = field(default_factory=dict)
    chain_applicable: bool = True
    annotation: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _nonempty(self.prefix_id, "prefix_id")
        if self.recoverability not in RECOVERABILITY_LEVELS:
            raise ValueError(f"unsupported recoverability: {self.recoverability}")
        if self.chain_applicable and len(self.ordered_event_ids) < 4:
            raise ValueError("an applicable failure chain needs at least four ordered events")
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
        if self.failure_episode is not None:
            value["failure_episode"] = self.failure_episode.to_dict()
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
            failure_episode=(
                FailureEpisodeGold.from_dict(value["failure_episode"])
                if isinstance(value.get("failure_episode"), Mapping)
                else None
            ),
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
