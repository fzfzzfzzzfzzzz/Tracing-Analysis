"""Typed, serializable schema for runtime capability-trace graphs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp that is stable in JSON."""

    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class NodeType(str, Enum):
    GOAL = "goal"
    SUBGOAL = "subgoal"
    TOOL_CALL = "tool_call"
    MCP_CALL = "mcp_call"
    OBSERVATION = "observation"
    ERROR = "error"
    DECISION = "decision"
    SUMMARY = "summary"
    CONSTRAINT = "constraint"
    ARCHIVE_HANDLE = "external_archive_handle"


class EdgeType(str, Enum):
    PRODUCES = "produces"
    FAILED_WITH = "failed_with"
    USES = "uses"
    SUPPORTS = "supports"
    BLOCKS = "blocks"
    RESOLVES = "resolves"
    SUPERSEDES = "supersedes"
    COMPRESSES = "compresses"
    RETRIES = "retries"
    LEADS_TO = "leads_to"
    # Canonical v2 relations are directed from earlier evidence/action to the
    # later event that consumes, revises, or discharges it.  The legacy
    # relations above remain readable so existing experiment traces migrate
    # without a destructive rewrite.
    PROVIDES_INPUT = "provides_input"
    RETRIED_BY = "retried_by"
    RESOLVED_BY = "resolved_by"
    SUPERSEDED_BY = "superseded_by"
    SUMMARIZED_BY = "summarized_by"


class LifecycleState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    CRITICAL_EVIDENCE = "critical_evidence"
    CONSUMED = "consumed"
    UNRESOLVED_FAILURE = "unresolved_failure"
    RESOLVED_FAILURE = "resolved_failure"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    AUDIT_REQUIRED = "audit_required"


class RelevanceState(str, Enum):
    """Whether a record is still useful to the agent's current decision."""

    UNCLASSIFIED = "unclassified"
    ACTIVE = "active"
    DORMANT = "dormant"
    CONSUMED = "consumed"


class ValidityState(str, Enum):
    """Epistemic state, kept independent from relevance and storage."""

    UNKNOWN = "unknown"
    VALID = "valid"
    NEGATIVE_UNRESOLVED = "negative_unresolved"
    NEGATIVE_RESOLVED = "negative_resolved"
    SUPERSEDED = "superseded"


class StorageState(str, Enum):
    """Physical representation of a record in the active context system."""

    RAW_IN_CONTEXT = "raw_in_context"
    SUMMARIZED_IN_CONTEXT = "summarized_in_context"
    ARCHIVED = "archived"
    EVICTED = "evicted"


class RetentionObligation(str, Enum):
    """Hard reasons that prevent a record from being silently discarded."""

    CRITICAL_EVIDENCE = "critical_evidence"
    ACTIVE_CONSTRAINT = "active_constraint"
    AUDIT_REQUIRED = "audit_required"
    RETAIN_UNTIL_ACTION_COMPLETE = "retain_until_action_complete"


class SemanticOutcome(str, Enum):
    """Semantic result of a tool call, separate from transport execution."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    INCONCLUSIVE = "inconclusive"
    POLICY_DENIED = "policy_denied"
    TEST_FAILED = "test_failed"


class FailureClass(str, Enum):
    """Decision-facing class for compact negative-evidence cards."""

    ACTIONABLE = "actionable"
    TERMINAL = "terminal"
    POLICY_DENIED = "policy_denied"
    MALFORMED = "malformed"
    STALE = "stale"


class FailureExpiryTrigger(str, Enum):
    """Why a failure card no longer belongs in protected active context."""

    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    ALTERNATIVE_COMPLETED = "alternative_completed"
    USER_ABANDONED = "user_abandoned"
    CONSTRAINT_CHANGED = "constraint_changed"
    FINAL_ACCEPTED = "final_accepted"
    CORRECTED_SYNTAX = "corrected_syntax"
    TTL_EXPIRED = "ttl_expired"
    TERMINAL = "terminal"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class FailureCard:
    """Compact, scoped representation of an unresolved negative outcome.

    A card is a context representation, not a replacement for source trace
    records. ``raw_archive_refs`` keep the full call/result chain recoverable
    without forcing historical provider messages back into every prompt.
    """

    card_id: str
    operation_scope: str
    action_family: str
    entity_ids: tuple[str, ...]
    failure_class: FailureClass
    latest_failure_cause: str
    failed_argument_diff: dict[str, Any]
    next_admissible_correction: str
    confidence: float
    created_step: int
    last_relevant_step: int
    expiry_trigger: FailureExpiryTrigger | None
    raw_archive_refs: tuple[str, ...]
    source_node_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("failure card confidence must be between 0 and 1")
        object.__setattr__(self, "entity_ids", tuple(dict.fromkeys(self.entity_ids)))
        object.__setattr__(
            self,
            "raw_archive_refs",
            tuple(dict.fromkeys(self.raw_archive_refs)),
        )
        object.__setattr__(
            self,
            "source_node_ids",
            tuple(dict.fromkeys(self.source_node_ids)),
        )

    @property
    def active(self) -> bool:
        return self.expiry_trigger is None and self.failure_class not in {
            FailureClass.TERMINAL,
            FailureClass.STALE,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "operation_scope": self.operation_scope,
            "action_family": self.action_family,
            "entity_ids": list(self.entity_ids),
            "failure_class": self.failure_class.value,
            "latest_failure_cause": self.latest_failure_cause,
            "failed_argument_diff": dict(self.failed_argument_diff),
            "next_admissible_correction": self.next_admissible_correction,
            "confidence": self.confidence,
            "created_step": self.created_step,
            "last_relevant_step": self.last_relevant_step,
            "expiry_trigger": (
                self.expiry_trigger.value if self.expiry_trigger is not None else None
            ),
            "raw_archive_refs": list(self.raw_archive_refs),
            "source_node_ids": list(self.source_node_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailureCard":
        values = dict(data)
        values["failure_class"] = FailureClass(values["failure_class"])
        trigger = values.get("expiry_trigger")
        values["expiry_trigger"] = FailureExpiryTrigger(trigger) if trigger else None
        values["entity_ids"] = tuple(values.get("entity_ids", ()))
        values["raw_archive_refs"] = tuple(values.get("raw_archive_refs", ()))
        values["source_node_ids"] = tuple(values.get("source_node_ids", ()))
        return cls(**values)


@dataclass(slots=True)
class LifecycleProfile:
    """Factorized lifecycle state used by schema v2.

    A node can now be consumed but still be critical evidence, or archived but
    still audit-required.  Those combinations were impossible to represent in
    the legacy single-label lifecycle enum.
    """

    relevance: RelevanceState = RelevanceState.UNCLASSIFIED
    validity: ValidityState = ValidityState.UNKNOWN
    storage: StorageState = StorageState.RAW_IN_CONTEXT
    obligations: tuple[RetentionObligation, ...] = ()
    scope: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    inferred_by: str = "uninitialized"
    inference_version: str = "lifecycle_profile_v2"
    trigger_node_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("lifecycle profile confidence must be between 0 and 1")
        self.obligations = tuple(dict.fromkeys(self.obligations))
        self.trigger_node_ids = tuple(dict.fromkeys(self.trigger_node_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "relevance": self.relevance.value,
            "validity": self.validity.value,
            "storage": self.storage.value,
            "obligations": [item.value for item in self.obligations],
            "scope": dict(self.scope),
            "confidence": self.confidence,
            "inferred_by": self.inferred_by,
            "inference_version": self.inference_version,
            "trigger_node_ids": list(self.trigger_node_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LifecycleProfile":
        values = dict(data)
        values["relevance"] = RelevanceState(
            values.get("relevance", RelevanceState.UNCLASSIFIED.value)
        )
        values["validity"] = ValidityState(values.get("validity", ValidityState.UNKNOWN.value))
        values["storage"] = StorageState(values.get("storage", StorageState.RAW_IN_CONTEXT.value))
        values["obligations"] = tuple(
            RetentionObligation(item) for item in values.get("obligations", ())
        )
        values["trigger_node_ids"] = tuple(values.get("trigger_node_ids", ()))
        return cls(**values)

    @classmethod
    def from_legacy(
        cls,
        *,
        lifecycle: LifecycleState,
        node_type: NodeType,
        active: bool,
        side_effect: bool,
    ) -> "LifecycleProfile":
        relevance = RelevanceState.ACTIVE if active else RelevanceState.CONSUMED
        validity = ValidityState.UNKNOWN
        storage = StorageState.RAW_IN_CONTEXT
        obligations: list[RetentionObligation] = []
        if lifecycle == LifecycleState.UNRESOLVED_FAILURE:
            validity = ValidityState.NEGATIVE_UNRESOLVED
            obligations.append(RetentionObligation.RETAIN_UNTIL_ACTION_COMPLETE)
        elif lifecycle == LifecycleState.RESOLVED_FAILURE:
            validity = ValidityState.NEGATIVE_RESOLVED
        elif lifecycle == LifecycleState.SUPERSEDED:
            validity = ValidityState.SUPERSEDED
        elif node_type in {NodeType.OBSERVATION, NodeType.SUMMARY}:
            validity = ValidityState.VALID
        if lifecycle == LifecycleState.ARCHIVED:
            storage = StorageState.ARCHIVED
        if lifecycle == LifecycleState.CRITICAL_EVIDENCE:
            obligations.append(RetentionObligation.CRITICAL_EVIDENCE)
        if lifecycle == LifecycleState.AUDIT_REQUIRED or side_effect:
            obligations.append(RetentionObligation.AUDIT_REQUIRED)
        if node_type == NodeType.CONSTRAINT and active:
            obligations.append(RetentionObligation.ACTIVE_CONSTRAINT)
        return cls(
            relevance=relevance,
            validity=validity,
            storage=storage,
            obligations=tuple(obligations),
            confidence=0.5,
            inferred_by="legacy_projection",
            inference_version="legacy_to_lifecycle_profile_v2",
        )


class ToolStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    PARTIAL_SUCCESS = "partial_success"


@dataclass(slots=True)
class Node:
    """A trace-graph node.

    ``content`` is the context-facing representation. Full raw tool payloads can
    additionally be stored under ``raw_ref`` in the external archive.
    """

    node_type: NodeType
    content: Any
    step_id: int
    lifecycle: LifecycleState = LifecycleState.CREATED
    token_count: int = 0
    raw_ref: str | None = None
    side_effect: bool = False
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    lifecycle_profile: LifecycleProfile = field(default_factory=LifecycleProfile)
    node_id: str = field(default_factory=lambda: new_id("node"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["node_type"] = self.node_type.value
        data["lifecycle"] = self.lifecycle.value
        data["lifecycle_profile"] = self.lifecycle_profile.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Node":
        values = dict(data)
        values["node_type"] = NodeType(values["node_type"])
        values["lifecycle"] = LifecycleState(values["lifecycle"])
        profile = values.get("lifecycle_profile")
        if isinstance(profile, dict):
            values["lifecycle_profile"] = LifecycleProfile.from_dict(profile)
        else:
            values["lifecycle_profile"] = LifecycleProfile.from_legacy(
                lifecycle=values["lifecycle"],
                node_type=values["node_type"],
                active=bool(values.get("active", True)),
                side_effect=bool(values.get("side_effect", False)),
            )
        return cls(**values)


@dataclass(slots=True)
class Edge:
    source: str
    target: str
    edge_type: EdgeType
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    edge_id: str = field(default_factory=lambda: new_id("edge"))
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("edge confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["edge_type"] = self.edge_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Edge":
        values = dict(data)
        values["edge_type"] = EdgeType(values["edge_type"])
        return cls(**values)
