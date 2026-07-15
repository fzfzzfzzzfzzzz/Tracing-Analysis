"""Tool wrapper that captures calls and incrementally updates a trace graph."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from .archive import ArchiveStore
from .graph import TraceGraph
from .schema import EdgeType, LifecycleState, Node, NodeType, ToolStatus

T = TypeVar("T")


def estimate_tokens(value: Any) -> int:
    """Deterministic fallback token estimate used when provider usage is absent."""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if not text:
        return 0
    # A byte-aware approximation behaves reasonably for both CJK and ASCII.
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


class ToolExecutor:
    """Execute a callable while recording its complete tool trace.

    The wrapper re-raises tool exceptions after recording them. This preserves
    application semantics while ensuring failures remain available as negative
    evidence in the graph and archive.
    """

    def __init__(self, graph: TraceGraph, archive: ArchiveStore) -> None:
        self.graph = graph
        self.archive = archive

    def _previous_failed_call(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        calls = self.graph.find_nodes(node_types={NodeType.TOOL_CALL, NodeType.MCP_CALL})
        for node in reversed(calls):
            if node.metadata.get("tool_name") != tool_name:
                continue
            if node.metadata.get("canonical_arguments") != canonical:
                continue
            if self.graph.outgoing(node.node_id, EdgeType.FAILED_WITH):
                return node.node_id
        return None

    def _record_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        step_id: int,
        side_effect: bool,
        mcp_server: str | None,
    ) -> Node:
        call_type = NodeType.MCP_CALL if mcp_server else NodeType.TOOL_CALL
        raw_ref = self.archive.put(
            {"tool_name": tool_name, "mcp_server": mcp_server, "arguments": arguments},
            metadata={"kind": "tool_call", "session_id": self.graph.session_id, "step_id": step_id},
        )
        lifecycle = LifecycleState.AUDIT_REQUIRED if side_effect else LifecycleState.ACTIVE
        call = self.graph.create_node(
            call_type,
            {"tool_name": tool_name, "arguments": arguments, "mcp_server": mcp_server},
            step_id,
            lifecycle=lifecycle,
            token_count=estimate_tokens(arguments),
            raw_ref=raw_ref,
            side_effect=side_effect,
            metadata={
                "tool_name": tool_name,
                "mcp_server": mcp_server,
                "canonical_arguments": json.dumps(
                    arguments, ensure_ascii=False, sort_keys=True, default=str
                ),
            },
        )
        previous = self._previous_failed_call(tool_name, arguments)
        if previous is not None and previous != call.node_id:
            self.graph.connect(call.node_id, previous, EdgeType.RETRIES)
        return call

    def execute(
        self,
        tool: Callable[..., T],
        /,
        *args: Any,
        step_id: int,
        tool_name: str | None = None,
        side_effect: bool = False,
        mcp_server: str | None = None,
        token_count: int | None = None,
        **kwargs: Any,
    ) -> T:
        name = tool_name or getattr(tool, "__name__", tool.__class__.__name__)
        arguments = {"args": list(args), "kwargs": kwargs}
        call = self._record_call(
            tool_name=name,
            arguments=arguments,
            step_id=step_id,
            side_effect=side_effect,
            mcp_server=mcp_server,
        )
        try:
            result = tool(*args, **kwargs)
        except Exception as exc:
            error_payload = {
                "type": type(exc).__name__,
                "message": str(exc),
                "tool_name": name,
            }
            raw_ref = self.archive.put(
                error_payload,
                metadata={
                    "kind": "tool_error",
                    "session_id": self.graph.session_id,
                    "step_id": step_id,
                },
            )
            error = self.graph.create_node(
                NodeType.ERROR,
                error_payload,
                step_id,
                lifecycle=LifecycleState.UNRESOLVED_FAILURE,
                token_count=token_count if token_count is not None else estimate_tokens(error_payload),
                raw_ref=raw_ref,
                active=True,
                metadata={"status": ToolStatus.FAILED.value, "tool_name": name},
            )
            self.graph.connect(call.node_id, error.node_id, EdgeType.FAILED_WITH)
            raise
        raw_ref = self.archive.put(
            result,
            metadata={"kind": "tool_observation", "session_id": self.graph.session_id, "step_id": step_id},
        )
        observation = self.graph.create_node(
            NodeType.OBSERVATION,
            result,
            step_id,
            lifecycle=LifecycleState.ACTIVE,
            token_count=token_count if token_count is not None else estimate_tokens(result),
            raw_ref=raw_ref,
            active=True,
            metadata={"status": ToolStatus.SUCCESS.value, "tool_name": name},
        )
        self.graph.connect(call.node_id, observation.node_id, EdgeType.PRODUCES)
        return result

    def record_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        step_id: int,
        status: ToolStatus,
        payload: Any,
        side_effect: bool = False,
        mcp_server: str | None = None,
        token_count: int | None = None,
    ) -> tuple[Node, Node]:
        """Record a result produced by an external harness without executing it."""

        call = self._record_call(
            tool_name=tool_name,
            arguments=arguments,
            step_id=step_id,
            side_effect=side_effect,
            mcp_server=mcp_server,
        )
        is_failure = status in {ToolStatus.FAILED, ToolStatus.TIMEOUT}
        result_type = NodeType.ERROR if is_failure else NodeType.OBSERVATION
        state = LifecycleState.UNRESOLVED_FAILURE if is_failure else LifecycleState.ACTIVE
        raw_ref = self.archive.put(
            payload,
            metadata={
                "kind": "tool_error" if is_failure else "tool_observation",
                "session_id": self.graph.session_id,
                "step_id": step_id,
            },
        )
        result = self.graph.create_node(
            result_type,
            payload,
            step_id,
            lifecycle=state,
            token_count=token_count if token_count is not None else estimate_tokens(payload),
            raw_ref=raw_ref,
            active=True,
            metadata={"status": status.value, "tool_name": tool_name},
        )
        relation = EdgeType.FAILED_WITH if is_failure else EdgeType.PRODUCES
        self.graph.connect(call.node_id, result.node_id, relation)
        return call, result

