"""Bounded recursive memory-improvement contracts and immutable version ledger.

This module implements *external-memory* evolution, not model-weight self-modification.
Every update is proposed against an immutable parent revision, must carry explicit
validation evidence, and can be rolled back.  The core is deterministic and offline;
extractors, generators, replay validators, and task executors are injected protocols.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Protocol, Sequence

from ..capture import estimate_tokens
from .compression_audit.io import canonical_json, stable_digest
from .compression_audit.models import FailureChainGold, PrefixRecord
from .failure_experience import ContinuationTrial


EXPERIENCE_SCHEMA_VERSION = "failure_experience_memory_v1"
ENTRY_SCHEMA_VERSION = "recursive_memory_entry_v1"
REVISION_SCHEMA_VERSION = "recursive_memory_revision_v1"
CANDIDATE_SCHEMA_VERSION = "recursive_memory_update_candidate_v1"
VALIDATION_SCHEMA_VERSION = "recursive_memory_validation_v1"
DECISION_SCHEMA_VERSION = "recursive_memory_update_decision_v1"
LEDGER_SCHEMA_VERSION = "recursive_memory_ledger_v1"

UPDATE_OPERATIONS = ("add", "replace", "retire")
REVISION_OPERATIONS = ("genesis", "update", "rollback")


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _unique(values: Sequence[str], name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    result = tuple(_required(value, name) for value in values)
    if not allow_empty and not result:
        raise ValueError(f"{name} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must be unique")
    return result


def action_key(event: Mapping[str, Any]) -> str:
    """Return a stable action identity without including the action's outcome."""

    if event.get("kind") not in {"tool_call", "mcp_call"}:
        raise ValueError("action_key requires a tool or MCP call")
    content = event.get("content")
    arguments = content.get("arguments") if isinstance(content, Mapping) else None
    payload = {
        "tool_name": event.get("tool_name") or (
            content.get("name") if isinstance(content, Mapping) else None
        ),
        "arguments": arguments if isinstance(arguments, Mapping) else content,
    }
    return "action:" + stable_digest(payload)[:24]


@dataclass(frozen=True, slots=True)
class FailureExperience:
    experience_id: str
    source_task_id: str
    source_checkpoint_id: str
    source_prefix_hash: str
    source_split: str
    failure_signature: str
    failed_action_key: str
    failure_reason: str
    diagnosis: str
    recovery_steps: tuple[str, ...]
    successful_action_key: str
    applicability_conditions: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    extractor_id: str
    estimated_tokens: int
    schema_version: str = EXPERIENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EXPERIENCE_SCHEMA_VERSION:
            raise ValueError("unsupported failure experience schema")
        for name in (
            "experience_id", "source_task_id", "source_checkpoint_id",
            "source_prefix_hash", "failure_signature", "failed_action_key",
            "failure_reason", "diagnosis", "successful_action_key", "extractor_id",
        ):
            _required(getattr(self, name), name)
        if self.source_split not in {"dev", "validation", "test"}:
            raise ValueError("unsupported experience source split")
        _unique(self.recovery_steps, "recovery steps")
        _unique(self.applicability_conditions, "applicability conditions", allow_empty=True)
        _unique(self.evidence_event_ids, "experience evidence IDs")
        if type(self.estimated_tokens) is not int or self.estimated_tokens <= 0:
            raise ValueError("estimated_tokens must be a positive integer")
        expected = self._computed_id(self.to_dict(include_id=False))
        if self.experience_id != expected:
            raise ValueError("failure experience ID does not match its content")

    @staticmethod
    def _computed_id(payload: Mapping[str, Any]) -> str:
        return "experience:" + stable_digest(payload)[:24]

    @classmethod
    def create(cls, **values: Any) -> "FailureExperience":
        payload = {**values, "schema_version": EXPERIENCE_SCHEMA_VERSION}
        payload.pop("experience_id", None)
        experience_id = cls._computed_id(payload)
        return cls(experience_id=experience_id, **values)

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_id:
            value.pop("experience_id")
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureExperience":
        return cls(
            experience_id=str(value["experience_id"]),
            source_task_id=str(value["source_task_id"]),
            source_checkpoint_id=str(value["source_checkpoint_id"]),
            source_prefix_hash=str(value["source_prefix_hash"]),
            source_split=str(value["source_split"]),
            failure_signature=str(value["failure_signature"]),
            failed_action_key=str(value["failed_action_key"]),
            failure_reason=str(value["failure_reason"]),
            diagnosis=str(value["diagnosis"]),
            recovery_steps=tuple(map(str, value.get("recovery_steps", ()))),
            successful_action_key=str(value["successful_action_key"]),
            applicability_conditions=tuple(map(
                str, value.get("applicability_conditions", ())
            )),
            evidence_event_ids=tuple(map(str, value.get("evidence_event_ids", ()))),
            extractor_id=str(value["extractor_id"]),
            estimated_tokens=int(value["estimated_tokens"]),
            schema_version=str(value.get("schema_version", "")),
        )


def experience_from_failure_episode(
    prefix: PrefixRecord,
    gold: FailureChainGold,
    *,
    source_task_id: str,
    source_checkpoint_id: str,
    extractor_id: str,
    applicability_conditions: Sequence[str] = (),
) -> FailureExperience:
    """Adapt an annotated episode into the runtime experience contract.

    This adapter is suitable for controlled experiments and evaluator fixtures.  It
    does not turn gold into a production extractor; live extraction must implement
    :class:`ExperienceExtractor` without access to hidden labels.
    """

    if gold.prefix_id != prefix.prefix_id or gold.failure_episode is None:
        raise ValueError("matching failure_episode_gold_v1 is required")
    episode = gold.failure_episode
    episode.validate_against_prefix(prefix)
    events = {str(event["event_id"]): event for event in prefix.events}
    successful_action = episode.repair_steps[-1].action_event_id
    payload = {
        "source_task_id": _required(source_task_id, "source_task_id"),
        "source_checkpoint_id": _required(source_checkpoint_id, "source_checkpoint_id"),
        "source_prefix_hash": prefix.prefix_hash,
        "source_split": prefix.split,
        "failure_signature": gold.error_signature,
        "failed_action_key": action_key(events[episode.initial_action_event_id]),
        "failure_reason": gold.error_signature,
        "diagnosis": gold.diagnostic_evidence,
        "recovery_steps": tuple(step.semantic_change for step in episode.repair_steps),
        "successful_action_key": action_key(events[successful_action]),
        "applicability_conditions": tuple(map(str, applicability_conditions)),
        "evidence_event_ids": tuple(
            event_id for event_id in _ordered_prefix_ids(prefix)
            if event_id in episode.referenced_event_ids()
        ),
        "extractor_id": _required(extractor_id, "extractor_id"),
    }
    payload["estimated_tokens"] = max(1, estimate_tokens(canonical_json(payload)))
    return FailureExperience.create(**payload)


def _ordered_prefix_ids(prefix: PrefixRecord) -> tuple[str, ...]:
    return tuple(
        str(event["event_id"])
        for event in sorted(prefix.events, key=lambda event: int(event["step_id"]))
    )


class ExperienceExtractor(Protocol):
    extractor_id: str

    def extract(
        self, prefix: PrefixRecord, *, task_id: str, checkpoint_id: str
    ) -> Sequence[FailureExperience]: ...


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    entry_id: str
    experience: FailureExperience
    created_round_id: str
    schema_version: str = ENTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ENTRY_SCHEMA_VERSION:
            raise ValueError("unsupported memory entry schema")
        _required(self.entry_id, "entry_id")
        _required(self.created_round_id, "created_round_id")
        expected = "memory:" + stable_digest({
            "experience_id": self.experience.experience_id,
            "created_round_id": self.created_round_id,
        })[:24]
        if self.entry_id != expected:
            raise ValueError("memory entry ID does not match its content")

    @classmethod
    def from_experience(cls, experience: FailureExperience, round_id: str) -> "MemoryEntry":
        round_id = _required(round_id, "round_id")
        entry_id = "memory:" + stable_digest({
            "experience_id": experience.experience_id,
            "created_round_id": round_id,
        })[:24]
        return cls(entry_id=entry_id, experience=experience, created_round_id=round_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "entry_id": self.entry_id,
            "experience": self.experience.to_dict(),
            "created_round_id": self.created_round_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemoryEntry":
        return cls(
            entry_id=str(value["entry_id"]),
            experience=FailureExperience.from_dict(value["experience"]),
            created_round_id=str(value["created_round_id"]),
            schema_version=str(value.get("schema_version", "")),
        )


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    revision_id: str
    parent_revision_id: str | None
    round_id: str
    operation: str
    entries: tuple[MemoryEntry, ...]
    accepted_candidate_ids: tuple[str, ...] = ()
    rollback_target_revision_id: str | None = None
    rollback_reason: str | None = None
    schema_version: str = REVISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REVISION_SCHEMA_VERSION:
            raise ValueError("unsupported memory revision schema")
        _required(self.revision_id, "revision_id")
        _required(self.round_id, "round_id")
        if self.operation not in REVISION_OPERATIONS:
            raise ValueError("unsupported revision operation")
        if self.operation == "genesis" and self.parent_revision_id is not None:
            raise ValueError("genesis revision cannot have a parent")
        if self.operation != "genesis" and not self.parent_revision_id:
            raise ValueError("non-genesis revision requires a parent")
        entry_ids = tuple(entry.entry_id for entry in self.entries)
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("memory revision contains duplicate entries")
        _unique(self.accepted_candidate_ids, "accepted candidates", allow_empty=True)
        if self.operation == "rollback":
            _required(self.rollback_target_revision_id, "rollback target")
            _required(self.rollback_reason, "rollback reason")
        elif self.rollback_target_revision_id is not None or self.rollback_reason is not None:
            raise ValueError("only rollback revisions may declare rollback metadata")
        expected = self._computed_id(self.to_dict(include_id=False))
        if self.revision_id != expected:
            raise ValueError("memory revision ID does not match its content")

    @staticmethod
    def _computed_id(payload: Mapping[str, Any]) -> str:
        return "memory-revision:" + stable_digest(payload)[:24]

    @classmethod
    def create(
        cls,
        *,
        parent_revision_id: str | None,
        round_id: str,
        operation: str,
        entries: Sequence[MemoryEntry],
        accepted_candidate_ids: Sequence[str] = (),
        rollback_target_revision_id: str | None = None,
        rollback_reason: str | None = None,
    ) -> "MemoryRevision":
        values = {
            "parent_revision_id": parent_revision_id,
            "round_id": round_id,
            "operation": operation,
            "entries": tuple(sorted(entries, key=lambda entry: entry.entry_id)),
            "accepted_candidate_ids": tuple(accepted_candidate_ids),
            "rollback_target_revision_id": rollback_target_revision_id,
            "rollback_reason": rollback_reason,
        }
        provisional = {
            **values,
            "entries": [entry.to_dict() for entry in values["entries"]],
            "accepted_candidate_ids": list(values["accepted_candidate_ids"]),
            "schema_version": REVISION_SCHEMA_VERSION,
        }
        revision_id = cls._computed_id(provisional)
        return cls(revision_id=revision_id, **values)

    @property
    def total_tokens(self) -> int:
        return sum(entry.experience.estimated_tokens for entry in self.entries)

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "revision_id": self.revision_id,
            "parent_revision_id": self.parent_revision_id,
            "round_id": self.round_id,
            "operation": self.operation,
            "entries": [entry.to_dict() for entry in self.entries],
            "accepted_candidate_ids": list(self.accepted_candidate_ids),
            "rollback_target_revision_id": self.rollback_target_revision_id,
            "rollback_reason": self.rollback_reason,
        }
        if not include_id:
            value.pop("revision_id")
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemoryRevision":
        return cls(
            revision_id=str(value["revision_id"]),
            parent_revision_id=(
                str(value["parent_revision_id"])
                if value.get("parent_revision_id") is not None else None
            ),
            round_id=str(value["round_id"]),
            operation=str(value["operation"]),
            entries=tuple(MemoryEntry.from_dict(item) for item in value.get("entries", ())),
            accepted_candidate_ids=tuple(map(
                str, value.get("accepted_candidate_ids", ())
            )),
            rollback_target_revision_id=(
                str(value["rollback_target_revision_id"])
                if value.get("rollback_target_revision_id") is not None else None
            ),
            rollback_reason=(
                str(value["rollback_reason"])
                if value.get("rollback_reason") is not None else None
            ),
            schema_version=str(value.get("schema_version", "")),
        )


@dataclass(frozen=True, slots=True)
class MemoryUpdateCandidate:
    candidate_id: str
    round_id: str
    parent_revision_id: str
    operation: str
    source_experience_ids: tuple[str, ...]
    generator_id: str
    rationale: str
    proposed_entry: MemoryEntry | None = None
    target_entry_id: str | None = None
    hidden_evaluation_observed: bool = False
    schema_version: str = CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CANDIDATE_SCHEMA_VERSION:
            raise ValueError("unsupported update candidate schema")
        for name in ("candidate_id", "round_id", "parent_revision_id", "generator_id",
                     "rationale"):
            _required(getattr(self, name), name)
        if self.operation not in UPDATE_OPERATIONS:
            raise ValueError("unsupported update operation")
        _unique(self.source_experience_ids, "source experience IDs")
        if self.operation == "add":
            if self.proposed_entry is None or self.target_entry_id is not None:
                raise ValueError("add requires a proposed entry and no target")
        elif self.operation == "replace":
            if self.proposed_entry is None or not self.target_entry_id:
                raise ValueError("replace requires a proposed entry and target")
        elif self.proposed_entry is not None or not self.target_entry_id:
            raise ValueError("retire requires a target and no proposed entry")
        if self.proposed_entry is not None:
            if self.proposed_entry.created_round_id != self.round_id:
                raise ValueError("proposed entry was created for a different round")
            if self.proposed_entry.experience.experience_id not in self.source_experience_ids:
                raise ValueError("proposed entry is not backed by a source experience")
        expected = self._computed_id(self.to_dict(include_id=False))
        if self.candidate_id != expected:
            raise ValueError("candidate ID does not match its content")

    @staticmethod
    def _computed_id(payload: Mapping[str, Any]) -> str:
        return "memory-update:" + stable_digest(payload)[:24]

    @classmethod
    def create(
        cls,
        *,
        round_id: str,
        parent_revision_id: str,
        operation: str,
        source_experience_ids: Sequence[str],
        generator_id: str,
        rationale: str,
        proposed_entry: MemoryEntry | None = None,
        target_entry_id: str | None = None,
        hidden_evaluation_observed: bool = False,
    ) -> "MemoryUpdateCandidate":
        values = {
            "round_id": round_id,
            "parent_revision_id": parent_revision_id,
            "operation": operation,
            "source_experience_ids": tuple(source_experience_ids),
            "generator_id": generator_id,
            "rationale": rationale,
            "proposed_entry": proposed_entry,
            "target_entry_id": target_entry_id,
            "hidden_evaluation_observed": hidden_evaluation_observed,
        }
        payload = {
            **values,
            "source_experience_ids": list(values["source_experience_ids"]),
            "proposed_entry": (
                values["proposed_entry"].to_dict()
                if values.get("proposed_entry") is not None else None
            ),
            "schema_version": CANDIDATE_SCHEMA_VERSION,
        }
        candidate_id = cls._computed_id(payload)
        return cls(candidate_id=candidate_id, **values)

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "round_id": self.round_id,
            "parent_revision_id": self.parent_revision_id,
            "operation": self.operation,
            "source_experience_ids": list(self.source_experience_ids),
            "generator_id": self.generator_id,
            "rationale": self.rationale,
            "proposed_entry": self.proposed_entry.to_dict() if self.proposed_entry else None,
            "target_entry_id": self.target_entry_id,
            "hidden_evaluation_observed": self.hidden_evaluation_observed,
        }
        if not include_id:
            value.pop("candidate_id")
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemoryUpdateCandidate":
        entry = value.get("proposed_entry")
        return cls(
            candidate_id=str(value["candidate_id"]),
            round_id=str(value["round_id"]),
            parent_revision_id=str(value["parent_revision_id"]),
            operation=str(value["operation"]),
            source_experience_ids=tuple(map(
                str, value.get("source_experience_ids", ())
            )),
            generator_id=str(value["generator_id"]),
            rationale=str(value["rationale"]),
            proposed_entry=MemoryEntry.from_dict(entry) if entry is not None else None,
            target_entry_id=(
                str(value["target_entry_id"])
                if value.get("target_entry_id") is not None else None
            ),
            hidden_evaluation_observed=value.get("hidden_evaluation_observed", False),
            schema_version=str(value.get("schema_version", "")),
        )


class MemoryUpdateGenerator(Protocol):
    generator_id: str

    def propose(
        self,
        experiences: Sequence[FailureExperience],
        parent: MemoryRevision,
        *,
        round_id: str,
    ) -> Sequence[MemoryUpdateCandidate]: ...


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    candidate_id: str
    validator_id: str
    source_split: str
    provenance_pass: bool
    replay_pass: bool
    regression_pass: bool
    contradiction_free: bool
    safety_pass: bool
    hidden_evaluation_observed: bool
    evaluated_task_ids: tuple[str, ...]
    metrics: Mapping[str, float] = field(default_factory=dict)
    schema_version: str = VALIDATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VALIDATION_SCHEMA_VERSION:
            raise ValueError("unsupported validation evidence schema")
        _required(self.candidate_id, "candidate_id")
        _required(self.validator_id, "validator_id")
        if self.source_split not in {"dev", "validation", "test"}:
            raise ValueError("unsupported validation source split")
        for name in (
            "provenance_pass", "replay_pass", "regression_pass",
            "contradiction_free", "safety_pass", "hidden_evaluation_observed",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        _unique(self.evaluated_task_ids, "evaluated task IDs")
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in self.metrics.values()):
            raise ValueError("validation metrics must be finite numbers")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["metrics"] = dict(self.metrics)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationEvidence":
        return cls(
            candidate_id=str(value["candidate_id"]),
            validator_id=str(value["validator_id"]),
            source_split=str(value["source_split"]),
            provenance_pass=value["provenance_pass"],
            replay_pass=value["replay_pass"],
            regression_pass=value["regression_pass"],
            contradiction_free=value["contradiction_free"],
            safety_pass=value["safety_pass"],
            hidden_evaluation_observed=value["hidden_evaluation_observed"],
            evaluated_task_ids=tuple(map(str, value.get("evaluated_task_ids", ()))),
            metrics={str(key): float(metric) for key, metric in value.get("metrics", {}).items()},
            schema_version=str(value.get("schema_version", "")),
        )


class UpdateValidator(Protocol):
    validator_id: str

    def validate(
        self, candidate: MemoryUpdateCandidate, parent: MemoryRevision
    ) -> ValidationEvidence: ...


@dataclass(frozen=True, slots=True)
class EvolutionPolicy:
    policy_id: str = "bounded_recursive_memory_dev_v1"
    allowed_experience_splits: tuple[str, ...] = ("dev",)
    allowed_validation_splits: tuple[str, ...] = ("dev",)
    max_entries: int = 256
    max_total_tokens: int = 32768
    max_updates_per_round: int = 8
    require_provenance: bool = True
    require_replay: bool = True
    require_regression: bool = True
    require_contradiction_free: bool = True
    require_safety: bool = True

    def __post_init__(self) -> None:
        _required(self.policy_id, "policy_id")
        for name in ("allowed_experience_splits", "allowed_validation_splits"):
            values = _unique(getattr(self, name), name)
            if set(values) - {"dev", "validation"}:
                raise ValueError("test split cannot authorize a memory update")
        for name in ("max_entries", "max_total_tokens", "max_updates_per_round"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "require_provenance", "require_replay", "require_regression",
            "require_contradiction_free", "require_safety",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")


@dataclass(frozen=True, slots=True)
class UpdateDecision:
    decision_id: str
    candidate_id: str
    policy_id: str
    accepted: bool
    reasons: tuple[str, ...]
    validation_digest: str
    schema_version: str = DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported update decision schema")
        _required(self.decision_id, "decision_id")
        _required(self.candidate_id, "candidate_id")
        _required(self.policy_id, "policy_id")
        _required(self.validation_digest, "validation_digest")
        if type(self.accepted) is not bool:
            raise ValueError("accepted must be boolean")
        if self.accepted and self.reasons:
            raise ValueError("accepted updates cannot have rejection reasons")
        if not self.accepted:
            _unique(self.reasons, "rejection reasons")
        expected = "update-decision:" + stable_digest(self.to_dict(include_id=False))[:24]
        if self.decision_id != expected:
            raise ValueError("decision ID does not match its content")

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_id:
            value.pop("decision_id")
        return value

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        policy_id: str,
        accepted: bool,
        reasons: Sequence[str],
        validation_digest: str,
    ) -> "UpdateDecision":
        values = {
            "candidate_id": candidate_id,
            "policy_id": policy_id,
            "accepted": accepted,
            "reasons": tuple(reasons),
            "validation_digest": validation_digest,
        }
        payload = {**values, "reasons": list(values["reasons"]),
                   "schema_version": DECISION_SCHEMA_VERSION}
        decision_id = "update-decision:" + stable_digest(payload)[:24]
        return cls(decision_id=decision_id, **values)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpdateDecision":
        return cls(
            decision_id=str(value["decision_id"]),
            candidate_id=str(value["candidate_id"]),
            policy_id=str(value["policy_id"]),
            accepted=value["accepted"],
            reasons=tuple(map(str, value.get("reasons", ()))),
            validation_digest=str(value["validation_digest"]),
            schema_version=str(value.get("schema_version", "")),
        )


def decide_update(
    candidate: MemoryUpdateCandidate,
    experience_by_id: Mapping[str, FailureExperience],
    evidence: ValidationEvidence,
    policy: EvolutionPolicy,
) -> UpdateDecision:
    """Apply the frozen policy; validation evidence is never silently repaired."""

    if evidence.candidate_id != candidate.candidate_id:
        raise ValueError("candidate/validation mismatch")
    missing_experiences = set(candidate.source_experience_ids) - set(experience_by_id)
    if missing_experiences:
        raise ValueError(f"candidate references unknown experiences: {sorted(missing_experiences)}")
    reasons = []
    if candidate.hidden_evaluation_observed or evidence.hidden_evaluation_observed:
        reasons.append("hidden_evaluation_observed")
    source_splits = {experience_by_id[item].source_split
                     for item in candidate.source_experience_ids}
    if not source_splits <= set(policy.allowed_experience_splits):
        reasons.append("experience_split_not_allowed")
    if evidence.source_split not in policy.allowed_validation_splits:
        reasons.append("validation_split_not_allowed")
    checks = {
        "provenance_failed": (policy.require_provenance, evidence.provenance_pass),
        "replay_failed": (policy.require_replay, evidence.replay_pass),
        "regression_failed": (policy.require_regression, evidence.regression_pass),
        "contradiction_detected": (
            policy.require_contradiction_free, evidence.contradiction_free
        ),
        "safety_failed": (policy.require_safety, evidence.safety_pass),
    }
    reasons.extend(name for name, (required, passed) in checks.items()
                   if required and not passed)
    digest = stable_digest(evidence.to_dict())
    return UpdateDecision.create(
        candidate_id=candidate.candidate_id,
        policy_id=policy.policy_id,
        accepted=not reasons,
        reasons=tuple(reasons),
        validation_digest=digest,
    )


def preview_candidate_revision(
    parent: MemoryRevision,
    candidate: MemoryUpdateCandidate,
) -> MemoryRevision:
    """Materialize one uncommitted candidate for an isolated replay branch.

    The returned revision is content-addressed but is not appended to a ledger and
    therefore cannot be mistaken for an accepted update.  Final policy checks and
    ledger mutation still happen only in :meth:`MemoryEvolutionLedger.finalize_round`.
    """

    if candidate.parent_revision_id != parent.revision_id:
        raise ValueError("preview candidate/parent mismatch")
    entries = {entry.entry_id: entry for entry in parent.entries}
    proposed = candidate.proposed_entry
    target = candidate.target_entry_id
    if candidate.operation == "add":
        if proposed.entry_id in entries:
            raise ValueError("preview add duplicates an existing memory entry")
        entries[proposed.entry_id] = proposed
    elif candidate.operation == "replace":
        if target not in entries:
            raise ValueError("preview replace target is absent")
        entries.pop(target)
        if proposed.entry_id in entries:
            raise ValueError("preview replacement duplicates another memory entry")
        entries[proposed.entry_id] = proposed
    else:
        if target not in entries:
            raise ValueError("preview retire target is absent")
        entries.pop(target)
    return MemoryRevision.create(
        parent_revision_id=parent.revision_id,
        round_id=candidate.round_id,
        operation="update",
        entries=tuple(entries.values()),
        accepted_candidate_ids=(candidate.candidate_id,),
    )


class MemoryEvolutionLedger:
    """Append-only in-memory ledger with deterministic serialization and rollback."""

    def __init__(self, genesis: MemoryRevision | None = None) -> None:
        genesis = genesis or MemoryRevision.create(
            parent_revision_id=None,
            round_id="genesis",
            operation="genesis",
            entries=(),
        )
        if genesis.operation != "genesis":
            raise ValueError("ledger must start from a genesis revision")
        self.revisions: dict[str, MemoryRevision] = {genesis.revision_id: genesis}
        self.revision_order: list[str] = [genesis.revision_id]
        self.candidates: dict[str, MemoryUpdateCandidate] = {}
        self.validations: dict[str, ValidationEvidence] = {}
        self.decisions: dict[str, UpdateDecision] = {}
        self.experiences: dict[str, FailureExperience] = {
            entry.experience.experience_id: entry.experience for entry in genesis.entries
        }
        if len(self.experiences) != len(genesis.entries):
            raise ValueError("genesis contains duplicate experiences")
        self.current_revision_id = genesis.revision_id

    @property
    def current(self) -> MemoryRevision:
        return self.revisions[self.current_revision_id]

    def finalize_round(
        self,
        *,
        round_id: str,
        experiences: Sequence[FailureExperience],
        candidates: Sequence[MemoryUpdateCandidate],
        validations: Sequence[ValidationEvidence],
        policy: EvolutionPolicy,
    ) -> dict[str, Any]:
        """Validate a batch and atomically create at most one new revision."""

        round_id = _required(round_id, "round_id")
        if len(candidates) > policy.max_updates_per_round:
            raise ValueError("round exceeds max_updates_per_round")
        experience_by_id = {item.experience_id: item for item in experiences}
        if len(experience_by_id) != len(experiences):
            raise ValueError("round contains duplicate experiences")
        for experience_id, experience in experience_by_id.items():
            existing = self.experiences.get(experience_id)
            if existing is not None and existing != experience:
                raise ValueError("experience ID collision")
        validation_by_id = {item.candidate_id: item for item in validations}
        if len(validation_by_id) != len(validations):
            raise ValueError("round contains duplicate validations")
        candidate_ids = [item.candidate_id for item in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("round contains duplicate candidates")
        if set(validation_by_id) != set(candidate_ids):
            raise ValueError("every candidate needs exactly one validation")
        parent = self.current
        for candidate in candidates:
            if candidate.round_id != round_id or candidate.parent_revision_id != parent.revision_id:
                raise ValueError("candidate round or parent differs from current revision")
            if candidate.candidate_id in self.candidates:
                raise ValueError("candidate was already finalized")

        decisions = [
            decide_update(candidate, experience_by_id, validation_by_id[candidate.candidate_id], policy)
            for candidate in candidates
        ]
        accepted = [candidate for candidate, decision in zip(candidates, decisions)
                    if decision.accepted]
        revision = self._build_revision(round_id, parent, accepted, policy) if accepted else None
        self.experiences.update(experience_by_id)
        self.candidates.update({item.candidate_id: item for item in candidates})
        self.validations.update(validation_by_id)
        self.decisions.update({item.decision_id: item for item in decisions})
        if revision is not None:
            self._append_revision(revision)
        return {
            "schema_version": "recursive_memory_round_result_v1",
            "round_id": round_id,
            "parent_revision_id": parent.revision_id,
            "new_revision_id": revision.revision_id if revision else None,
            "current_revision_id": self.current_revision_id,
            "accepted_candidate_ids": [item.candidate_id for item in accepted],
            "rejected_candidate_ids": [
                candidate.candidate_id for candidate, decision in zip(candidates, decisions)
                if not decision.accepted
            ],
            "decisions": [decision.to_dict() for decision in decisions],
        }

    def _build_revision(
        self,
        round_id: str,
        parent: MemoryRevision,
        candidates: Sequence[MemoryUpdateCandidate],
        policy: EvolutionPolicy,
    ) -> MemoryRevision:
        entries = {entry.entry_id: entry for entry in parent.entries}
        touched_targets = [candidate.target_entry_id for candidate in candidates
                           if candidate.target_entry_id]
        if len(touched_targets) != len(set(touched_targets)):
            raise ValueError("multiple accepted updates target the same memory entry")
        for candidate in candidates:
            if candidate.operation == "add":
                if candidate.proposed_entry.entry_id in entries:
                    raise ValueError("add candidate duplicates an existing memory entry")
                entries[candidate.proposed_entry.entry_id] = candidate.proposed_entry
            elif candidate.operation == "replace":
                if candidate.target_entry_id not in entries:
                    raise ValueError("replace target is absent from parent revision")
                entries.pop(candidate.target_entry_id)
                if candidate.proposed_entry.entry_id in entries:
                    raise ValueError("replacement duplicates another memory entry")
                entries[candidate.proposed_entry.entry_id] = candidate.proposed_entry
            else:
                if candidate.target_entry_id not in entries:
                    raise ValueError("retire target is absent from parent revision")
                entries.pop(candidate.target_entry_id)
        if len(entries) > policy.max_entries:
            raise ValueError("accepted batch exceeds memory entry limit")
        if sum(entry.experience.estimated_tokens for entry in entries.values()) > policy.max_total_tokens:
            raise ValueError("accepted batch exceeds memory token limit")
        return MemoryRevision.create(
            parent_revision_id=parent.revision_id,
            round_id=round_id,
            operation="update",
            entries=tuple(entries.values()),
            accepted_candidate_ids=tuple(item.candidate_id for item in candidates),
        )

    def rollback(self, target_revision_id: str, *, round_id: str, reason: str) -> MemoryRevision:
        if target_revision_id not in self.revisions:
            raise ValueError("rollback target is unknown")
        ancestors = set()
        cursor: str | None = self.current_revision_id
        while cursor is not None:
            ancestors.add(cursor)
            cursor = self.revisions[cursor].parent_revision_id
        if target_revision_id not in ancestors:
            raise ValueError("rollback target is not an ancestor of the current revision")
        target = self.revisions[target_revision_id]
        revision = MemoryRevision.create(
            parent_revision_id=self.current_revision_id,
            round_id=_required(round_id, "round_id"),
            operation="rollback",
            entries=target.entries,
            rollback_target_revision_id=target_revision_id,
            rollback_reason=_required(reason, "rollback reason"),
        )
        self._append_revision(revision)
        return revision

    def _append_revision(self, revision: MemoryRevision) -> None:
        if revision.parent_revision_id != self.current_revision_id:
            raise ValueError("revision does not extend the current ledger head")
        if revision.revision_id in self.revisions:
            raise ValueError("revision already exists")
        self.revisions[revision.revision_id] = revision
        self.revision_order.append(revision.revision_id)
        self.current_revision_id = revision.revision_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "current_revision_id": self.current_revision_id,
            "revision_order": list(self.revision_order),
            "revisions": [self.revisions[item].to_dict() for item in self.revision_order],
            "experiences": [
                self.experiences[item].to_dict() for item in sorted(self.experiences)
            ],
            "candidates": [self.candidates[item].to_dict() for item in sorted(self.candidates)],
            "validations": [self.validations[item].to_dict() for item in sorted(self.validations)],
            "decisions": [self.decisions[item].to_dict() for item in sorted(self.decisions)],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemoryEvolutionLedger":
        if value.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise ValueError("unsupported memory ledger schema")
        revisions = [MemoryRevision.from_dict(item) for item in value.get("revisions", ())]
        if not revisions or revisions[0].operation != "genesis":
            raise ValueError("serialized ledger has no genesis revision")
        ledger = cls(revisions[0])
        for revision in revisions[1:]:
            ledger._append_revision(revision)
        declared_order = list(map(str, value.get("revision_order", ())))
        if declared_order != ledger.revision_order:
            raise ValueError("serialized ledger revision order differs")
        if value.get("current_revision_id") != ledger.current_revision_id:
            raise ValueError("serialized ledger head differs")
        ledger.experiences = {
            item.experience_id: item
            for item in map(FailureExperience.from_dict, value.get("experiences", ()))
        }
        for revision in ledger.revisions.values():
            for entry in revision.entries:
                if ledger.experiences.get(entry.experience.experience_id) != entry.experience:
                    raise ValueError("serialized revision references missing experience content")
        ledger.candidates = {
            item.candidate_id: item
            for item in map(MemoryUpdateCandidate.from_dict, value.get("candidates", ()))
        }
        ledger.validations = {
            item.candidate_id: item
            for item in map(ValidationEvidence.from_dict, value.get("validations", ()))
        }
        ledger.decisions = {
            item.decision_id: item
            for item in map(UpdateDecision.from_dict, value.get("decisions", ()))
        }
        if set(ledger.candidates) != set(ledger.validations):
            raise ValueError("serialized candidates and validations differ")
        referenced_experiences = {
            experience_id for candidate in ledger.candidates.values()
            for experience_id in candidate.source_experience_ids
        }
        if not referenced_experiences <= set(ledger.experiences):
            raise ValueError("serialized candidates reference missing experiences")
        if {item.candidate_id for item in ledger.decisions.values()} != set(ledger.candidates):
            raise ValueError("serialized decisions do not cover all candidates")
        decisions_by_candidate = {
            item.candidate_id: item for item in ledger.decisions.values()
        }
        for candidate_id, evidence in ledger.validations.items():
            if decisions_by_candidate[candidate_id].validation_digest != stable_digest(
                evidence.to_dict()
            ):
                raise ValueError("serialized validation differs from its decision digest")
        return ledger


def write_ledger_snapshot(ledger: MemoryEvolutionLedger, directory: Path) -> Path:
    """Write a content-addressed ledger snapshot without overwriting another state."""

    payload = ledger.to_dict()
    envelope = {
        "schema_version": "recursive_memory_ledger_snapshot_v1",
        "ledger_digest": stable_digest(payload),
        "ledger": payload,
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"ledger-{envelope['ledger_digest'][:16]}.json"
    encoded = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError("content-addressed ledger snapshot differs on disk")
    else:
        path.write_text(encoded, encoding="utf-8", newline="\n")
    return path


def load_ledger_snapshot(path: Path) -> MemoryEvolutionLedger:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "recursive_memory_ledger_snapshot_v1":
        raise ValueError("unsupported ledger snapshot schema")
    if stable_digest(value.get("ledger")) != value.get("ledger_digest"):
        raise ValueError("ledger snapshot digest mismatch")
    return MemoryEvolutionLedger.from_dict(value["ledger"])


def write_revision_snapshot(revision: MemoryRevision, directory: Path) -> Path:
    """Write a content-addressed revision for baseline or candidate replay."""

    payload = revision.to_dict()
    envelope = {
        "schema_version": "recursive_memory_revision_snapshot_v1",
        "revision_digest": stable_digest(payload),
        "revision": payload,
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"revision-{envelope['revision_digest'][:16]}.json"
    encoded = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError("content-addressed revision snapshot differs on disk")
    else:
        path.write_text(encoded, encoding="utf-8", newline="\n")
    return path


def load_revision_snapshot(path: Path) -> MemoryRevision:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "recursive_memory_revision_snapshot_v1":
        raise ValueError("unsupported memory revision snapshot schema")
    if stable_digest(value.get("revision")) != value.get("revision_digest"):
        raise ValueError("memory revision snapshot digest mismatch")
    return MemoryRevision.from_dict(value["revision"])


class RevisionEvaluator(Protocol):
    def evaluate(self, revision: MemoryRevision) -> Sequence[ContinuationTrial]: ...


@dataclass(frozen=True, slots=True)
class PostCommitGuard:
    """Frozen thresholds for retaining or rolling back a committed revision."""

    min_pairs: int = 1
    min_success_rate_delta: float = 0.0
    max_repeated_failure_rate_delta: float = 0.0
    max_mean_token_delta: float | None = None
    max_mean_latency_delta: float | None = None

    def __post_init__(self) -> None:
        if type(self.min_pairs) is not int or self.min_pairs <= 0:
            raise ValueError("min_pairs must be a positive integer")
        for name in (
            "min_success_rate_delta", "max_repeated_failure_rate_delta",
            "max_mean_token_delta", "max_mean_latency_delta",
        ):
            value = getattr(self, name)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number or null")


def evaluate_post_commit_guard(
    comparison: Mapping[str, Any], guard: PostCommitGuard
) -> dict[str, Any]:
    if comparison.get("schema_version") != "recursive_memory_revision_comparison_v1":
        raise ValueError("post-commit guard received an unsupported comparison")
    checks = {
        "minimum_pairs": int(comparison["pair_count"]) >= guard.min_pairs,
        "success_not_regressed": (
            float(comparison["success_rate_delta"]) >= guard.min_success_rate_delta
        ),
        "repeated_failure_not_regressed": (
            float(comparison["repeated_failure_rate_delta"])
            <= guard.max_repeated_failure_rate_delta
        ),
    }
    if guard.max_mean_token_delta is not None:
        checks["token_cost_within_limit"] = (
            float(comparison["mean_token_delta"]) <= guard.max_mean_token_delta
        )
    if guard.max_mean_latency_delta is not None:
        checks["latency_within_limit"] = (
            float(comparison["mean_latency_delta"]) <= guard.max_mean_latency_delta
        )
    return {
        "schema_version": "recursive_memory_post_commit_guard_v1",
        "thresholds": asdict(guard),
        "checks": checks,
        "pass": all(checks.values()),
        "failure_reasons": sorted(name for name, passed in checks.items() if not passed),
    }


def compare_revisions(
    baseline_trials: Sequence[ContinuationTrial],
    evolved_trials: Sequence[ContinuationTrial],
    *,
    round_id: str,
    baseline_revision_id: str,
    evolved_revision_id: str,
) -> dict[str, Any]:
    """Compute paired behavior deltas for a committed memory revision."""

    def keyed(trials: Sequence[ContinuationTrial]) -> dict[tuple[str, str], dict[str, Any]]:
        rows = {(trial.task_id, trial.checkpoint_id): trial.score() for trial in trials}
        if len(rows) != len(trials):
            raise ValueError("revision evaluation contains duplicate paired units")
        return rows

    before, after = keyed(baseline_trials), keyed(evolved_trials)
    if not before or set(before) != set(after):
        raise ValueError("revision comparison requires identical nonempty paired units")
    pairs = []
    for key in sorted(before):
        left, right = before[key], after[key]
        pairs.append({
            "task_id": key[0],
            "checkpoint_id": key[1],
            "success_delta": int(right["task_success"]) - int(left["task_success"]),
            "repeated_failure_delta": (
                int(right["repeated_failure"]) - int(left["repeated_failure"])
            ),
            "reacquisition_delta": right["reacquisition_calls"] - left["reacquisition_calls"],
            "tool_call_delta": right["tool_calls"] - left["tool_calls"],
            "token_delta": right["total_tokens"] - left["total_tokens"],
            "latency_delta": right["latency_seconds"] - left["latency_seconds"],
        })
    return {
        "schema_version": "recursive_memory_revision_comparison_v1",
        "round_id": _required(round_id, "round_id"),
        "baseline_revision_id": _required(baseline_revision_id, "baseline_revision_id"),
        "evolved_revision_id": _required(evolved_revision_id, "evolved_revision_id"),
        "paired_unit": ["task_id", "checkpoint_id"],
        "pair_count": len(pairs),
        "success_rate_delta": fmean(item["success_delta"] for item in pairs),
        "repeated_failure_rate_delta": fmean(
            item["repeated_failure_delta"] for item in pairs
        ),
        "mean_reacquisition_delta": fmean(item["reacquisition_delta"] for item in pairs),
        "mean_tool_call_delta": fmean(item["tool_call_delta"] for item in pairs),
        "mean_token_delta": fmean(item["token_delta"] for item in pairs),
        "mean_latency_delta": fmean(item["latency_delta"] for item in pairs),
        "pairs": pairs,
    }


class BoundedRecursiveMemoryLoop:
    """Orchestrate one bounded extract→propose→validate→commit cycle at a time."""

    def __init__(
        self,
        *,
        extractor: ExperienceExtractor,
        generator: MemoryUpdateGenerator,
        validator: UpdateValidator,
        policy: EvolutionPolicy,
        ledger: MemoryEvolutionLedger | None = None,
    ) -> None:
        self.extractor = extractor
        self.generator = generator
        self.validator = validator
        self.policy = policy
        self.ledger = ledger or MemoryEvolutionLedger()

    def run_update_round(
        self,
        prefix: PrefixRecord,
        *,
        task_id: str,
        checkpoint_id: str,
        round_id: str,
    ) -> dict[str, Any]:
        """Run injected components; only the ledger may change persistent state."""

        task_id = _required(task_id, "task_id")
        checkpoint_id = _required(checkpoint_id, "checkpoint_id")
        round_id = _required(round_id, "round_id")
        parent = self.ledger.current
        experiences = tuple(self.extractor.extract(
            prefix, task_id=task_id, checkpoint_id=checkpoint_id
        ))
        for experience in experiences:
            if (
                experience.source_task_id != task_id
                or experience.source_checkpoint_id != checkpoint_id
                or experience.source_prefix_hash != prefix.prefix_hash
                or experience.source_split != prefix.split
            ):
                raise ValueError("extractor returned experience with mismatched provenance")
        candidates = tuple(self.generator.propose(
            experiences, parent, round_id=round_id
        ))
        validations = tuple(
            self.validator.validate(candidate, parent) for candidate in candidates
        )
        result = self.ledger.finalize_round(
            round_id=round_id,
            experiences=experiences,
            candidates=candidates,
            validations=validations,
            policy=self.policy,
        )
        return {
            **result,
            "extractor_id": self.extractor.extractor_id,
            "generator_id": self.generator.generator_id,
            "validator_id": self.validator.validator_id,
            "experience_ids": [item.experience_id for item in experiences],
            "candidate_ids": [item.candidate_id for item in candidates],
        }

    def evaluate_and_guard(
        self,
        round_result: Mapping[str, Any],
        baseline_trials: Sequence[ContinuationTrial],
        evolved_trials: Sequence[ContinuationTrial],
        *,
        guard: PostCommitGuard,
        rollback_round_id: str,
    ) -> dict[str, Any]:
        """Evaluate the new head and create an auditable rollback on regression."""

        new_revision_id = round_result.get("new_revision_id")
        if not new_revision_id:
            raise ValueError("cannot evaluate a round that committed no revision")
        if new_revision_id != self.ledger.current_revision_id:
            raise ValueError("post-commit evaluation must target the current ledger head")
        comparison = compare_revisions(
            baseline_trials,
            evolved_trials,
            round_id=str(round_result["round_id"]),
            baseline_revision_id=str(round_result["parent_revision_id"]),
            evolved_revision_id=str(new_revision_id),
        )
        guard_result = evaluate_post_commit_guard(comparison, guard)
        rollback = None
        if not guard_result["pass"]:
            rollback = self.ledger.rollback(
                str(round_result["parent_revision_id"]),
                round_id=rollback_round_id,
                reason=";".join(guard_result["failure_reasons"]),
            )
        return {
            "schema_version": "recursive_memory_guarded_evaluation_v1",
            "comparison": comparison,
            "guard": guard_result,
            "rollback_revision_id": rollback.revision_id if rollback else None,
            "current_revision_id": self.ledger.current_revision_id,
        }


def summarize_recursive_improvement(
    ledger: MemoryEvolutionLedger,
    comparisons: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Report longitudinal memory growth and behavior change without causal overclaim."""

    for item in comparisons:
        if item.get("schema_version") != "recursive_memory_revision_comparison_v1":
            raise ValueError("unsupported revision comparison")
        if item.get("evolved_revision_id") not in ledger.revisions:
            raise ValueError("comparison references an unknown evolved revision")
    updates = [revision for revision in ledger.revisions.values()
               if revision.operation == "update"]
    rollbacks = [revision for revision in ledger.revisions.values()
                 if revision.operation == "rollback"]
    return {
        "schema_version": "recursive_memory_longitudinal_summary_v1",
        "current_revision_id": ledger.current_revision_id,
        "revision_count": len(ledger.revisions),
        "update_revision_count": len(updates),
        "rollback_revision_count": len(rollbacks),
        "current_entry_count": len(ledger.current.entries),
        "current_memory_tokens": ledger.current.total_tokens,
        "accepted_update_count": sum(len(item.accepted_candidate_ids) for item in updates),
        "rejected_update_count": sum(not item.accepted for item in ledger.decisions.values()),
        "evaluated_round_count": len(comparisons),
        "mean_success_rate_delta": (
            fmean(float(item["success_rate_delta"]) for item in comparisons)
            if comparisons else None
        ),
        "mean_repeated_failure_rate_delta": (
            fmean(float(item["repeated_failure_rate_delta"]) for item in comparisons)
            if comparisons else None
        ),
        "mean_token_delta": (
            fmean(float(item["mean_token_delta"]) for item in comparisons)
            if comparisons else None
        ),
        "interpretation": "bounded_external_memory_improvement_not_weight_level_rsi",
    }
