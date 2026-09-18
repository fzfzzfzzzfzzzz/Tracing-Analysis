"""TraceGraph projection for ACON's AppWorld chat-memory protocol.

AppWorld exposes tool execution as alternating ``assistant`` Python snippets and
``user`` observations rather than OpenAI ``tool_calls`` / ``tool`` messages.
This module therefore keeps the graph selection generic, but applies an
AppWorld-specific protocol closure before returning messages to ACON.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..capture import estimate_tokens
from ..context_engine.graph_policy import GraphLifecycleManager
from ..graph import TraceGraph
from ..schema import EdgeType, LifecycleState, Node, NodeType, SemanticOutcome


_API_CALL = re.compile(r"apis\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_SIDE_EFFECT_PREFIXES = (
    "add",
    "approve",
    "book",
    "cancel",
    "change",
    "complete",
    "create",
    "delete",
    "deny",
    "exchange",
    "modify",
    "refund",
    "remove",
    "reset",
    "return",
    "send",
    "transfer",
    "update",
    "withdraw",
)
_ERROR_PREFIXES = (
    "error:",
    "execution failed",
    "traceback",
    "syntaxerror",
    "syntax error",
    "exception:",
)
_NEGATIVE_STATUS = {
    "denied",
    "error",
    "failed",
    "failure",
    "invalid",
    "not_found",
    "rejected",
    "timed_out",
    "timeout",
    "unavailable",
}


def canonical_messages_json(messages: Sequence[Mapping[str, Any]]) -> str:
    """Return the canonical representation used for request/projection hashes."""

    return json.dumps(
        [dict(message) for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def messages_sha256(messages: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_messages_json(messages).encode("utf-8")).hexdigest()


def _stable_node_id(session_id: str, kind: str, ordinal: int) -> str:
    payload = f"{session_id}\x1f{kind}\x1f{ordinal}".encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:24]}"


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _action_family(content: Any) -> str:
    text = str(content or "")
    calls = [f"{app}.{method}" for app, method in _API_CALL.findall(text)]
    return calls[-1] if calls else "python_repl"


def _side_effect(action_family: str) -> bool:
    method = action_family.rsplit(".", 1)[-1].lower()
    return method.startswith(_SIDE_EFFECT_PREFIXES)


def _is_error_observation(content: Any) -> bool:
    text = str(content or "").strip().lower()
    if text.startswith(_ERROR_PREFIXES) or "\ntraceback" in text:
        return True
    payload = _parse_json(content)
    if not isinstance(payload, dict):
        return False
    if payload.get("error") not in (None, False, "", 0, []):
        return True
    if payload.get("success") is False or payload.get("ok") is False:
        return True
    status = payload.get("status", payload.get("state", payload.get("outcome")))
    if status is not None:
        normalized = str(status).strip().lower().replace(" ", "_")
        return normalized in _NEGATIVE_STATUS
    if set(payload) == {"message"}:
        message = str(payload["message"]).lower()
        return any(
            marker in message
            for marker in ("not found", "failed", "invalid", "denied", "error")
        )
    return False


@dataclass(frozen=True, slots=True)
class AppWorldClosureAddition:
    ordinal: int
    reason: str
    trigger_ordinal: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "reason": self.reason,
            "trigger_ordinal": self.trigger_ordinal,
        }


@dataclass(slots=True)
class AppWorldProjection:
    messages: list[dict[str, Any]]
    selected_ordinals: set[int]
    fragments: list[str]
    additions: list[AppWorldClosureAddition]
    budget_pruned_ordinals: list[int]
    projection_budget_infeasible: bool
    graph: TraceGraph
    context_view: Any
    source_sha256: str
    projected_sha256: str

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": "appworld_tracegraph_projection_v1",
            "source_message_count": len(self.graph.metadata.get("source_roles", [])),
            "projected_message_count": len(self.messages),
            "selected_ordinals": sorted(self.selected_ordinals),
            "fragments": list(self.fragments),
            "closure_additions": [item.to_dict() for item in self.additions],
            "budget_pruned_ordinals": list(self.budget_pruned_ordinals),
            "projection_budget_infeasible": self.projection_budget_infeasible,
            "source_sha256": self.source_sha256,
            "projected_sha256": self.projected_sha256,
            "projected_estimated_tokens": sum(
                estimate_tokens(message.get("content", "")) for message in self.messages
            ),
            "context_view": self.context_view.to_dict(),
            "graph": self.graph.to_dict(),
        }


def build_appworld_graph(
    messages: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> TraceGraph:
    """Convert one ACON session (without its system message) to a trace graph."""

    graph = TraceGraph(
        session_id=session_id,
        metadata={
            "source": "acon_appworld_memory",
            "source_roles": [str(message.get("role", "")) for message in messages],
        },
    )
    first_user_seen = False
    latest_result: Node | None = None
    pending_call: Node | None = None
    failed_by_operation: dict[str, tuple[Node, Node]] = {}
    retried_failure_by_call: dict[str, Node] = {}

    for ordinal, raw_message in enumerate(messages, start=1):
        role = str(raw_message.get("role", "")).lower()
        content = raw_message.get("content", "")
        metadata = {
            "source_message_ordinal": ordinal,
            "source_role": role,
        }

        if role == "user" and not first_user_seen:
            first_user_seen = True
            graph.create_node(
                NodeType.GOAL,
                content,
                ordinal,
                node_id=_stable_node_id(session_id, "goal", ordinal),
                lifecycle=LifecycleState.ACTIVE,
                token_count=estimate_tokens(content),
                metadata=metadata,
            )
            continue

        if role == "assistant":
            family = _action_family(content)
            decision = graph.create_node(
                NodeType.DECISION,
                {"action_family": family},
                ordinal,
                node_id=_stable_node_id(session_id, "decision", ordinal),
                lifecycle=LifecycleState.ACTIVE,
                token_count=estimate_tokens({"action_family": family}),
                metadata={**metadata, "final": False, "action_family": family},
            )
            if latest_result is not None:
                graph.connect(
                    latest_result.node_id,
                    decision.node_id,
                    EdgeType.PROVIDES_INPUT,
                )
            pending_call = graph.create_node(
                NodeType.TOOL_CALL,
                content,
                ordinal,
                node_id=_stable_node_id(session_id, "tool_call", ordinal),
                lifecycle=(
                    LifecycleState.AUDIT_REQUIRED
                    if _side_effect(family)
                    else LifecycleState.ACTIVE
                ),
                token_count=estimate_tokens(content),
                raw_ref=f"memory://{session_id}/{ordinal}",
                side_effect=_side_effect(family),
                metadata={
                    **metadata,
                    "tool_name": family,
                    "operation_key": family,
                    "call_id": f"appworld_turn_{ordinal}",
                },
            )
            graph.connect(decision.node_id, pending_call.node_id, EdgeType.LEADS_TO)
            failed = failed_by_operation.get(family)
            if failed is not None:
                graph.connect(
                    failed[0].node_id,
                    pending_call.node_id,
                    EdgeType.RETRIED_BY,
                    confidence=0.8,
                    metadata={"match_type": "action_family", "inferred": True},
                )
                retried_failure_by_call[pending_call.node_id] = failed[1]
            continue

        if role != "user":
            continue

        is_error = _is_error_observation(content)
        payload = _parse_json(content)
        outcome = SemanticOutcome.NEGATIVE if is_error else SemanticOutcome.INCONCLUSIVE
        result = graph.create_node(
            NodeType.ERROR if is_error else NodeType.OBSERVATION,
            payload,
            ordinal,
            node_id=_stable_node_id(
                session_id, "error" if is_error else "observation", ordinal
            ),
            lifecycle=(
                LifecycleState.UNRESOLVED_FAILURE
                if is_error
                else LifecycleState.ACTIVE
            ),
            token_count=estimate_tokens(payload),
            raw_ref=f"memory://{session_id}/{ordinal}",
            metadata={
                **metadata,
                "semantic_outcome": outcome.value,
                "tool_name": (
                    pending_call.metadata.get("tool_name") if pending_call else None
                ),
            },
        )
        if pending_call is not None:
            graph.connect(
                pending_call.node_id,
                result.node_id,
                EdgeType.FAILED_WITH if is_error else EdgeType.PRODUCES,
            )
            operation = str(pending_call.metadata.get("operation_key", "python_repl"))
            if is_error:
                failed_by_operation[operation] = (pending_call, result)
            else:
                retried_error = retried_failure_by_call.get(pending_call.node_id)
                if retried_error is not None:
                    graph.connect(
                        retried_error.node_id,
                        result.node_id,
                        EdgeType.RESOLVED_BY,
                        confidence=0.8,
                        metadata={"inferred_from_retry": True},
                    )
                    failed_by_operation.pop(operation, None)
        latest_result = result
        pending_call = None

    graph.metadata["graph_validation_errors"] = graph.validate()
    return graph


def close_appworld_protocol(
    messages: Sequence[Mapping[str, Any]],
    ordinals: set[int],
) -> tuple[set[int], list[AppWorldClosureAddition]]:
    """Close ACON assistant/action and user/observation pairs."""

    closed = {ordinal for ordinal in ordinals if 1 <= ordinal <= len(messages)}
    additions: list[AppWorldClosureAddition] = []
    if messages:
        first_user = next(
            (
                ordinal
                for ordinal, message in enumerate(messages, start=1)
                if str(message.get("role", "")).lower() == "user"
            ),
            None,
        )
        if first_user is not None and first_user not in closed:
            closed.add(first_user)
            additions.append(AppWorldClosureAddition(first_user, "task_anchor"))

    changed = True
    while changed:
        changed = False
        for ordinal in sorted(tuple(closed)):
            role = str(messages[ordinal - 1].get("role", "")).lower()
            if role == "assistant" and ordinal < len(messages):
                if str(messages[ordinal].get("role", "")).lower() == "user":
                    paired = ordinal + 1
                    if paired not in closed:
                        closed.add(paired)
                        additions.append(
                            AppWorldClosureAddition(
                                paired, "observation_for_selected_action", ordinal
                            )
                        )
                        changed = True
            if role == "user" and ordinal > 1:
                if str(messages[ordinal - 2].get("role", "")).lower() == "assistant":
                    paired = ordinal - 1
                    if paired not in closed:
                        closed.add(paired)
                        additions.append(
                            AppWorldClosureAddition(
                                paired, "action_for_selected_observation", ordinal
                            )
                        )
                        changed = True
    return closed, additions


def project_appworld_history(
    messages: Sequence[Mapping[str, Any]],
    *,
    budget: int,
    session_id: str,
    manager: GraphLifecycleManager | None = None,
) -> AppWorldProjection:
    """Select graph context and project it back into ACON chat messages."""

    source = [dict(message) for message in messages]
    graph = build_appworld_graph(source, session_id=session_id)
    active_manager = manager or GraphLifecycleManager()
    view = active_manager.select(graph, budget=budget)
    selected = {len(source)} if source else set()
    protected: set[int] = {len(source)} if source else set()
    fragments: list[str] = []
    for item in view.items:
        if item.node_type in {NodeType.SUMMARY, NodeType.ARCHIVE_HANDLE}:
            fragments.append(json.dumps(item.content, ensure_ascii=False, default=str))
            continue
        # Decision nodes are structural companions for AppWorld's executable
        # assistant action. Replaying them would select the same raw assistant
        # message at a tiny synthetic token cost and defeat the budget. The
        # paired TOOL_CALL node owns the raw action and its real token count.
        if item.node_type == NodeType.DECISION:
            continue
        node = graph.nodes.get(item.node_id)
        ordinal = node.metadata.get("source_message_ordinal") if node else None
        if isinstance(ordinal, int):
            selected.add(ordinal)
            if any(
                marker in item.reason
                for marker in (
                    "current_goal",
                    "current_subgoal",
                    "active_constraint",
                    "unique_unrecoverable",
                )
            ):
                protected.add(ordinal)
        else:
            fragments.append(json.dumps(item.content, ensure_ascii=False, default=str))
    selected, additions = close_appworld_protocol(source, selected)
    protected, _ = close_appworld_protocol(source, protected)
    memory_block = ""
    if fragments:
        memory = "\n".join(f"- {fragment}" for fragment in fragments)
        memory_block = (
            "\n\n<TRACEGRAPH_MEMORY>\n"
            + memory
            + "\n</TRACEGRAPH_MEMORY>"
        )

    def projected_tokens(ordinals: set[int]) -> int:
        total = sum(
            estimate_tokens(source[ordinal - 1].get("content", ""))
            for ordinal in ordinals
        )
        return total + (estimate_tokens(memory_block) if memory_block else 0)

    budget_pruned: list[int] = []
    for ordinal in sorted(tuple(selected)):
        if projected_tokens(selected) <= budget:
            break
        if ordinal in protected:
            continue
        if str(source[ordinal - 1].get("role", "")).lower() != "assistant":
            continue
        pair = {ordinal}
        if (
            ordinal < len(source)
            and str(source[ordinal].get("role", "")).lower() == "user"
        ):
            pair.add(ordinal + 1)
        if pair & protected or not pair.issubset(selected):
            continue
        selected.difference_update(pair)
        budget_pruned.extend(sorted(pair))

    projected = [dict(source[ordinal - 1]) for ordinal in sorted(selected)]
    if fragments and projected:
        first_user_index = next(
            (
                index
                for index, message in enumerate(projected)
                if str(message.get("role", "")).lower() == "user"
            ),
            None,
        )
        if first_user_index is not None:
            projected[first_user_index]["content"] = (
                str(projected[first_user_index].get("content", ""))
                + memory_block
            )
    return AppWorldProjection(
        messages=projected,
        selected_ordinals=selected,
        fragments=fragments,
        additions=additions,
        budget_pruned_ordinals=budget_pruned,
        projection_budget_infeasible=projected_tokens(selected) > budget,
        graph=graph,
        context_view=view,
        source_sha256=messages_sha256(source),
        projected_sha256=messages_sha256(projected),
    )


class AppWorldTraceGraphMemory:
    """Stateful, auditable projection hook used by the server runner."""

    def __init__(self, *, budget: int, trace_path: str | Path, session_id: str) -> None:
        self.budget = int(budget)
        self.trace_path = Path(trace_path)
        self.session_id = str(session_id)
        self.call_index = 0
        self.manager = GraphLifecycleManager()

    def project(self, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        self.call_index += 1
        projection = project_appworld_history(
            messages,
            budget=self.budget,
            session_id=self.session_id,
            manager=self.manager,
        )
        record = projection.to_record()
        record["call_index"] = self.call_index
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return projection.messages
