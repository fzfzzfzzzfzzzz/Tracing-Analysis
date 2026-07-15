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
    node_id: str = field(default_factory=lambda: new_id("node"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["node_type"] = self.node_type.value
        data["lifecycle"] = self.lifecycle.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Node":
        values = dict(data)
        values["node_type"] = NodeType(values["node_type"])
        values["lifecycle"] = LifecycleState(values["lifecycle"])
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

