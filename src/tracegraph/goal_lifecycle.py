"""Goal-conditioned lifecycle view for Phase 6.

This module deliberately sits above the immutable EventGraph compatibility
layer.  It does not mutate legacy lifecycle fields or add new EventGraph edge
types.  Goal membership is read from event metadata and every uncertain case
fails closed into the active projection.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .capture import estimate_tokens
from .decision_state import stable_digest
from .graph import TraceGraph
from .schema import EdgeType, Node, NodeType


ArchiveReader = Callable[[str], Any]


class GoalLifecycleState(str, Enum):
    PINNED = "pinned"
    ACTIVE = "active"
    DORMANT = "dormant"
    SUPERSEDED = "superseded"
    EPHEMERAL = "ephemeral"
    UNCERTAIN = "uncertain"


class ProjectionAction(str, Enum):
    KEEP_RAW = "keep_raw"
    KEEP_STRUCTURED = "keep_structured"
    COMPRESS_TO_GUARD = "compress_to_guard"
    ARCHIVE_WITH_HANDLE = "archive_with_handle"
    OMIT_FROM_ACTIVE = "omit_from_active"


@dataclass(frozen=True, slots=True)
class GoalContext:
    current_goal_id: str
    open_subgoal_ids: tuple[str, ...] = ()
    paused_or_cancelled_goal_ids: tuple[str, ...] = ()
    referenced_event_ids: tuple[str, ...] = ()
    referenced_entities: tuple[str, ...] = ()
    referenced_files: tuple[str, ...] = ()
    referenced_operations: tuple[str, ...] = ()
    error_signatures: tuple[str, ...] = ()
    context_version: str = "phase6_goal_context_v1"

    def __post_init__(self) -> None:
        if not self.current_goal_id:
            raise ValueError("current_goal_id must be non-empty")
        for field in (
            "open_subgoal_ids",
            "paused_or_cancelled_goal_ids",
            "referenced_event_ids",
            "referenced_entities",
            "referenced_files",
            "referenced_operations",
            "error_signatures",
        ):
            object.__setattr__(self, field, tuple(sorted(set(map(str, getattr(self, field))))))

    @property
    def context_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "context_version": self.context_version,
            "current_goal_id": self.current_goal_id,
            "open_subgoal_ids": list(self.open_subgoal_ids),
            "paused_or_cancelled_goal_ids": list(self.paused_or_cancelled_goal_ids),
            "referenced_event_ids": list(self.referenced_event_ids),
            "referenced_entities": list(self.referenced_entities),
            "referenced_files": list(self.referenced_files),
            "referenced_operations": list(self.referenced_operations),
            "error_signatures": list(self.error_signatures),
        }
        if include_hash:
            value["context_hash"] = self.context_hash
        return value

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoalContext":
        value = cls(
            current_goal_id=str(data["current_goal_id"]),
            open_subgoal_ids=tuple(map(str, data.get("open_subgoal_ids", ()))),
            paused_or_cancelled_goal_ids=tuple(
                map(str, data.get("paused_or_cancelled_goal_ids", ()))
            ),
            referenced_event_ids=tuple(map(str, data.get("referenced_event_ids", ()))),
            referenced_entities=tuple(map(str, data.get("referenced_entities", ()))),
            referenced_files=tuple(map(str, data.get("referenced_files", ()))),
            referenced_operations=tuple(map(str, data.get("referenced_operations", ()))),
            error_signatures=tuple(map(str, data.get("error_signatures", ()))),
            context_version=str(data.get("context_version", "phase6_goal_context_v1")),
        )
        declared = data.get("context_hash")
        if declared is not None and declared != value.context_hash:
            raise ValueError("GoalContext hash mismatch")
        return value


@dataclass(frozen=True, slots=True)
class GoalLifecycleRecord:
    event_id: str
    state: GoalLifecycleState
    projection_action: ProjectionAction
    reason: str
    goal_ids: tuple[str, ...]
    token_count: int
    raw_ref: str | None
    protocol_span_id: str
    evidence_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.token_count < 0:
            raise ValueError("token_count must be non-negative")
        object.__setattr__(self, "goal_ids", tuple(sorted(set(self.goal_ids))))
        object.__setattr__(
            self,
            "evidence_event_ids",
            tuple(sorted(set(self.evidence_event_ids))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "state": self.state.value,
            "projection_action": self.projection_action.value,
            "reason": self.reason,
            "goal_ids": list(self.goal_ids),
            "token_count": self.token_count,
            "raw_ref": self.raw_ref,
            "protocol_span_id": self.protocol_span_id,
            "evidence_event_ids": list(self.evidence_event_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoalLifecycleRecord":
        return cls(
            event_id=str(data["event_id"]),
            state=GoalLifecycleState(str(data["state"])),
            projection_action=ProjectionAction(str(data["projection_action"])),
            reason=str(data["reason"]),
            goal_ids=tuple(map(str, data.get("goal_ids", ()))),
            token_count=int(data.get("token_count", 0)),
            raw_ref=str(data["raw_ref"]) if data.get("raw_ref") else None,
            protocol_span_id=str(data["protocol_span_id"]),
            evidence_event_ids=tuple(map(str, data.get("evidence_event_ids", ()))),
        )


@dataclass(frozen=True, slots=True)
class GoalProtocolSpan:
    span_id: str
    event_ids: tuple[str, ...]
    message_ordinals: tuple[int, ...]
    action: ProjectionAction
    state_summary: tuple[GoalLifecycleState, ...]
    token_count: int
    reactivation_token_count: int
    archive_verified: bool
    guard_text: str | None = None

    def __post_init__(self) -> None:
        if self.token_count < 0 or self.reactivation_token_count < 0:
            raise ValueError("span token counts must be non-negative")
        object.__setattr__(self, "event_ids", tuple(sorted(set(self.event_ids))))
        object.__setattr__(
            self, "message_ordinals", tuple(sorted(set(self.message_ordinals)))
        )
        object.__setattr__(
            self,
            "state_summary",
            tuple(sorted(set(self.state_summary), key=lambda item: item.value)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "event_ids": list(self.event_ids),
            "message_ordinals": list(self.message_ordinals),
            "action": self.action.value,
            "state_summary": [item.value for item in self.state_summary],
            "token_count": self.token_count,
            "reactivation_token_count": self.reactivation_token_count,
            "archive_verified": self.archive_verified,
            "guard_text": self.guard_text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoalProtocolSpan":
        return cls(
            span_id=str(data["span_id"]),
            event_ids=tuple(map(str, data.get("event_ids", ()))),
            message_ordinals=tuple(map(int, data.get("message_ordinals", ()))),
            action=ProjectionAction(str(data["action"])),
            state_summary=tuple(
                GoalLifecycleState(str(item)) for item in data.get("state_summary", ())
            ),
            token_count=int(data.get("token_count", 0)),
            reactivation_token_count=int(
                data.get("reactivation_token_count", data.get("token_count", 0))
            ),
            archive_verified=bool(data.get("archive_verified", False)),
            guard_text=str(data["guard_text"]) if data.get("guard_text") else None,
        )


@dataclass(frozen=True, slots=True)
class GoalLifecycleView:
    session_id: str
    cutoff_step: int
    prefix_event_hash: str
    goal_context: GoalContext
    records: tuple[GoalLifecycleRecord, ...]
    spans: tuple[GoalProtocolSpan, ...]
    root_event_ids: tuple[str, ...]
    uncertainty_reasons: tuple[str, ...] = ()
    schema_version: str = "phase6_goal_lifecycle_view_v1"

    def __post_init__(self) -> None:
        records = tuple(sorted(self.records, key=lambda item: item.event_id))
        spans = tuple(sorted(self.spans, key=lambda item: item.span_id))
        if len({item.event_id for item in records}) != len(records):
            raise ValueError("duplicate lifecycle event record")
        if len({item.span_id for item in spans}) != len(spans):
            raise ValueError("duplicate lifecycle span")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "spans", spans)
        object.__setattr__(self, "root_event_ids", tuple(sorted(set(self.root_event_ids))))
        object.__setattr__(
            self, "uncertainty_reasons", tuple(sorted(set(self.uncertainty_reasons)))
        )

    @property
    def view_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def record_map(self) -> dict[str, GoalLifecycleRecord]:
        return {item.event_id: item for item in self.records}

    def span_map(self) -> dict[str, GoalProtocolSpan]:
        return {item.span_id: item for item in self.spans}

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "cutoff_step": self.cutoff_step,
            "prefix_event_hash": self.prefix_event_hash,
            "goal_context": self.goal_context.to_dict(),
            "records": [item.to_dict() for item in self.records],
            "spans": [item.to_dict() for item in self.spans],
            "root_event_ids": list(self.root_event_ids),
            "uncertainty_reasons": list(self.uncertainty_reasons),
        }
        if include_hash:
            value["view_hash"] = self.view_hash
        return value

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoalLifecycleView":
        value = cls(
            session_id=str(data["session_id"]),
            cutoff_step=int(data["cutoff_step"]),
            prefix_event_hash=str(data["prefix_event_hash"]),
            goal_context=GoalContext.from_dict(data["goal_context"]),
            records=tuple(
                GoalLifecycleRecord.from_dict(item) for item in data.get("records", ())
            ),
            spans=tuple(GoalProtocolSpan.from_dict(item) for item in data.get("spans", ())),
            root_event_ids=tuple(map(str, data.get("root_event_ids", ()))),
            uncertainty_reasons=tuple(map(str, data.get("uncertainty_reasons", ()))),
            schema_version=str(
                data.get("schema_version", "phase6_goal_lifecycle_view_v1")
            ),
        )
        declared = data.get("view_hash")
        if declared is not None and declared != value.view_hash:
            raise ValueError("GoalLifecycleView hash mismatch")
        return value


_TOOL_TYPES = {
    NodeType.TOOL_CALL,
    NodeType.MCP_CALL,
    NodeType.OBSERVATION,
    NodeType.ERROR,
}
_DEPENDENCY_BACKWARD = {
    EdgeType.SUPPORTS,
    EdgeType.BLOCKS,
    EdgeType.PROVIDES_INPUT,
    EdgeType.LEADS_TO,
}
_PROTECTED_OBLIGATIONS = {"policy", "confirmation", "receipt", "audit"}


def _goal_ids(node: Node) -> tuple[str, ...]:
    values: list[str] = []
    single = node.metadata.get("goal_id")
    if single:
        values.append(str(single))
    many = node.metadata.get("goal_ids", ())
    if isinstance(many, str):
        values.append(many)
    elif isinstance(many, Iterable):
        values.extend(map(str, many))
    subgoal = node.metadata.get("subgoal_id")
    if subgoal:
        values.append(str(subgoal))
    return tuple(sorted(set(values)))


def _span_key(node: Node) -> str:
    if node.node_type in _TOOL_TYPES:
        call_id = node.metadata.get("call_id") or node.metadata.get("provider_group")
        if call_id:
            return f"tool:{call_id}"
    return f"event:{node.node_id}"


def _archive_ok(node: Node, archive_reader: ArchiveReader | None) -> bool:
    if node.node_type not in _TOOL_TYPES:
        return True
    if not node.raw_ref or archive_reader is None:
        return False
    try:
        archive_reader(node.raw_ref)
    except (OSError, KeyError, RuntimeError, ValueError):
        return False
    return True


def _protected(node: Node) -> tuple[bool, str]:
    obligation = str(node.metadata.get("retention_obligation") or "").lower()
    if node.node_type == NodeType.CONSTRAINT or obligation in _PROTECTED_OBLIGATIONS:
        return True, f"protected_obligation:{obligation or 'constraint'}"
    if node.side_effect or bool(node.metadata.get("pinned")):
        return True, "side_effect_or_explicit_pin"
    return False, ""


def _superseded(graph: TraceGraph, event_id: str) -> bool:
    return bool(
        graph.outgoing(event_id, EdgeType.SUPERSEDED_BY)
        or graph.incoming(event_id, EdgeType.SUPERSEDES)
    )


def _resolved(graph: TraceGraph, event_id: str) -> bool:
    return bool(
        graph.outgoing(event_id, EdgeType.RESOLVED_BY)
        or graph.incoming(event_id, EdgeType.RESOLVES)
    )


def _active_closure(
    graph: TraceGraph,
    visible_ids: set[str],
    root_ids: Iterable[str],
) -> set[str]:
    active = set(root_ids).intersection(visible_ids)
    queue = deque(sorted(active))
    while queue:
        current = queue.popleft()
        for edge in graph.incoming(current):
            if edge.edge_type not in _DEPENDENCY_BACKWARD or edge.source not in visible_ids:
                continue
            if edge.source not in active:
                active.add(edge.source)
                queue.append(edge.source)
        for edge in graph.outgoing(current, EdgeType.USES):
            if edge.target in visible_ids and edge.target not in active:
                active.add(edge.target)
                queue.append(edge.target)
    return active


def _default_action(state: GoalLifecycleState, node: Node) -> ProjectionAction:
    if state in {GoalLifecycleState.PINNED, GoalLifecycleState.UNCERTAIN}:
        return ProjectionAction.KEEP_RAW
    if state == GoalLifecycleState.ACTIVE:
        if node.metadata.get("structured_current"):
            return ProjectionAction.KEEP_STRUCTURED
        return ProjectionAction.KEEP_RAW
    if state == GoalLifecycleState.DORMANT and node.metadata.get("guard_text"):
        return ProjectionAction.COMPRESS_TO_GUARD
    if state in {GoalLifecycleState.DORMANT, GoalLifecycleState.SUPERSEDED}:
        return ProjectionAction.ARCHIVE_WITH_HANDLE
    return ProjectionAction.OMIT_FROM_ACTIVE


def _span_action(records: Iterable[GoalLifecycleRecord], nodes: Mapping[str, Node]) -> ProjectionAction:
    values = tuple(records)
    states = {item.state for item in values}
    if states.intersection(
        {GoalLifecycleState.PINNED, GoalLifecycleState.ACTIVE, GoalLifecycleState.UNCERTAIN}
    ):
        if states == {GoalLifecycleState.ACTIVE} and all(
            nodes[item.event_id].metadata.get("structured_current") for item in values
        ):
            return ProjectionAction.KEEP_STRUCTURED
        return ProjectionAction.KEEP_RAW
    if any(nodes[item.event_id].metadata.get("guard_text") for item in values):
        return ProjectionAction.COMPRESS_TO_GUARD
    if states.intersection({GoalLifecycleState.DORMANT, GoalLifecycleState.SUPERSEDED}):
        return ProjectionAction.ARCHIVE_WITH_HANDLE
    return ProjectionAction.OMIT_FROM_ACTIVE


def analyze_goal_lifecycle(
    event_graph: TraceGraph,
    goal_context: GoalContext,
    *,
    cutoff_step: int | None = None,
    archive_reader: ArchiveReader | None = None,
) -> GoalLifecycleView:
    """Build a deterministic prefix-only lifecycle view without mutating the graph."""

    maximum = max((node.step_id for node in event_graph.nodes.values()), default=0)
    cutoff = maximum if cutoff_step is None else int(cutoff_step)
    nodes = tuple(
        sorted(
            (node for node in event_graph.nodes.values() if node.step_id <= cutoff),
            key=lambda item: (item.step_id, item.node_id),
        )
    )
    visible_ids = {node.node_id for node in nodes}
    explicit_roots = set(goal_context.referenced_event_ids).intersection(visible_ids)
    for node in nodes:
        goals = set(_goal_ids(node))
        if (
            node.metadata.get("current_root")
            or node.node_id in explicit_roots
            or (
                node.node_type in {NodeType.GOAL, NodeType.SUBGOAL}
                and goals.intersection(
                    {goal_context.current_goal_id, *goal_context.open_subgoal_ids}
                )
            )
        ):
            explicit_roots.add(node.node_id)
    active_closure = _active_closure(event_graph, visible_ids, explicit_roots)

    uncertainty: list[str] = []
    preliminary: list[GoalLifecycleRecord] = []
    for node in nodes:
        goals = _goal_ids(node)
        protected, protected_reason = _protected(node)
        archive_verified = _archive_ok(node, archive_reader)
        if protected:
            state = GoalLifecycleState.PINNED
            reason = protected_reason
        elif node.node_id in active_closure or node.metadata.get("current_fact"):
            state = GoalLifecycleState.ACTIVE
            reason = "current_goal_dependency"
        elif node.node_type in _TOOL_TYPES and not archive_verified:
            state = GoalLifecycleState.UNCERTAIN
            reason = "archive_unavailable_or_unverified"
            uncertainty.append(f"{node.node_id}:{reason}")
        elif node.metadata.get("uncertain"):
            state = GoalLifecycleState.UNCERTAIN
            reason = "explicit_uncertainty"
            uncertainty.append(f"{node.node_id}:{reason}")
        elif _superseded(event_graph, node.node_id) or node.metadata.get("superseded"):
            state = GoalLifecycleState.SUPERSEDED
            reason = "later_event_supersedes_this_fact"
        elif (
            node.metadata.get("ephemeral_eligible")
            and node.metadata.get("deterministically_recomputable")
            and not node.metadata.get("reactivation_value")
            and node.node_type != NodeType.ERROR
        ):
            state = GoalLifecycleState.EPHEMERAL
            reason = "strict_ephemeral_conditions_satisfied"
        else:
            state = GoalLifecycleState.DORMANT
            if set(goals).intersection(goal_context.paused_or_cancelled_goal_ids):
                reason = "paused_or_cancelled_goal_history"
            elif _resolved(event_graph, node.node_id):
                reason = "resolved_failure_with_reactivation_value"
            else:
                reason = "outside_current_goal_closure_but_archived"
        preliminary.append(
            GoalLifecycleRecord(
                event_id=node.node_id,
                state=state,
                projection_action=_default_action(state, node),
                reason=reason,
                goal_ids=goals,
                token_count=node.token_count or estimate_tokens(node.content),
                raw_ref=node.raw_ref,
                protocol_span_id=_span_key(node),
                evidence_event_ids=(node.node_id,),
            )
        )

    by_span: dict[str, list[GoalLifecycleRecord]] = defaultdict(list)
    for record in preliminary:
        by_span[record.protocol_span_id].append(record)
    record_map = {item.event_id: item for item in preliminary}
    spans: list[GoalProtocolSpan] = []
    for span_id, members in sorted(by_span.items()):
        member_nodes = [event_graph.nodes[item.event_id] for item in members]
        guard = next(
            (
                str(node.metadata["guard_text"])
                for node in member_nodes
                if node.metadata.get("guard_text")
            ),
            None,
        )
        spans.append(
            GoalProtocolSpan(
                span_id=span_id,
                event_ids=tuple(item.event_id for item in members),
                message_ordinals=tuple(
                    int(node.metadata["source_message_ordinal"])
                    for node in member_nodes
                    if isinstance(node.metadata.get("source_message_ordinal"), int)
                ),
                action=_span_action(members, event_graph.nodes),
                state_summary=tuple(item.state for item in members),
                token_count=sum(item.token_count for item in members),
                reactivation_token_count=sum(
                    int(
                        node.metadata.get(
                            "reactivation_token_count",
                            record_map[node.node_id].token_count,
                        )
                    )
                    for node in member_nodes
                ),
                archive_verified=all(_archive_ok(node, archive_reader) for node in member_nodes),
                guard_text=guard,
            )
        )

    # A mixed protocol span inherits the fail-closed span action in every
    # record so downstream managers cannot accidentally evict a partial span.
    span_actions = {item.span_id: item.action for item in spans}
    records = tuple(
        GoalLifecycleRecord(
            event_id=item.event_id,
            state=item.state,
            projection_action=span_actions[item.protocol_span_id],
            reason=item.reason,
            goal_ids=item.goal_ids,
            token_count=item.token_count,
            raw_ref=item.raw_ref,
            protocol_span_id=item.protocol_span_id,
            evidence_event_ids=item.evidence_event_ids,
        )
        for item in record_map.values()
    )
    prefix_payload = {
        "session_id": event_graph.session_id,
        "cutoff_step": cutoff,
        "nodes": [node.to_dict() for node in nodes],
        "edges": [
            edge.to_dict()
            for edge in sorted(event_graph.edges.values(), key=lambda item: item.edge_id)
            if edge.source in visible_ids and edge.target in visible_ids
        ],
    }
    return GoalLifecycleView(
        session_id=event_graph.session_id,
        cutoff_step=cutoff,
        prefix_event_hash=stable_digest(prefix_payload),
        goal_context=goal_context,
        records=records,
        spans=tuple(spans),
        root_event_ids=tuple(explicit_roots),
        uncertainty_reasons=tuple(uncertainty),
    )
