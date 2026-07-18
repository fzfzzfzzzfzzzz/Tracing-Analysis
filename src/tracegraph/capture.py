"""Tool wrapper that captures calls and incrementally updates a trace graph."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from .archive import ArchiveStore
from .graph import TraceGraph
from .schema import (
    EdgeType,
    LifecycleState,
    Node,
    NodeType,
    SemanticOutcome,
    ToolStatus,
)
from .semantics import infer_semantic_outcome, operation_key

T = TypeVar("T")

TOKEN_ACCOUNTING_VERSION = "content_estimate_v2"


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

    def _call_has_unresolved_negative_result(self, node_id: str) -> bool:
        result_edges = self.graph.outgoing(node_id, EdgeType.FAILED_WITH) + self.graph.outgoing(
            node_id, EdgeType.PRODUCES
        )
        for edge in result_edges:
            result = self.graph.nodes[edge.target]
            outcome = result.metadata.get("semantic_outcome")
            if (
                (
                    result.node_type == NodeType.ERROR
                    or outcome
                    in {
                        SemanticOutcome.NEGATIVE.value,
                        SemanticOutcome.POLICY_DENIED.value,
                        SemanticOutcome.TEST_FAILED.value,
                    }
                )
                and not self.graph.resolving_edges(result.node_id)
                and not self.graph.superseding_edges(result.node_id)
            ):
                return True
        return False

    def _previous_failed_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[str, str, float] | None:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        structural_key = operation_key(tool_name, arguments)
        calls = self.graph.find_nodes(node_types={NodeType.TOOL_CALL, NodeType.MCP_CALL})
        structural_match: tuple[str, str, float] | None = None
        for node in reversed(calls):
            if node.metadata.get("tool_name") != tool_name:
                continue
            if not self._call_has_unresolved_negative_result(node.node_id):
                continue
            if node.metadata.get("canonical_arguments") == canonical:
                return node.node_id, "exact_signature", 1.0
            if structural_match is None and node.metadata.get("operation_key") == structural_key:
                structural_match = (node.node_id, "structural_operation", 0.8)
        return structural_match

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
        structural_key = operation_key(tool_name, arguments)
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
                "operation_key": structural_key,
            },
        )
        previous = self._previous_failed_call(tool_name, arguments)
        if previous is not None and previous[0] != call.node_id:
            self.graph.connect(
                previous[0],
                call.node_id,
                EdgeType.RETRIED_BY,
                confidence=previous[2],
                metadata={"match_type": previous[1], "inferred": True},
            )
        return call

    def _resolve_retried_results(self, call: Node, result: Node) -> None:
        result_outcome = result.metadata.get("semantic_outcome")
        result_is_negative = result.node_type == NodeType.ERROR or result_outcome in {
            SemanticOutcome.NEGATIVE.value,
            SemanticOutcome.POLICY_DENIED.value,
            SemanticOutcome.TEST_FAILED.value,
        }
        for previous_call_id in self.graph.retry_predecessors(call.node_id):
            result_edges = self.graph.outgoing(
                previous_call_id, EdgeType.FAILED_WITH
            ) + self.graph.outgoing(previous_call_id, EdgeType.PRODUCES)
            for edge in result_edges:
                previous_result = self.graph.nodes[edge.target]
                previous_outcome = previous_result.metadata.get("semantic_outcome")
                if previous_result.node_type != NodeType.ERROR and previous_outcome not in {
                    SemanticOutcome.NEGATIVE.value,
                    SemanticOutcome.POLICY_DENIED.value,
                    SemanticOutcome.TEST_FAILED.value,
                }:
                    continue
                if result_is_negative:
                    if not self.graph.resolving_edges(
                        previous_result.node_id
                    ) and not self.graph.superseding_edges(previous_result.node_id):
                        self.graph.connect(
                            previous_result.node_id,
                            result.node_id,
                            EdgeType.SUPERSEDED_BY,
                            metadata={"inferred_from_failed_retry": True},
                        )
                    continue
                if (
                    previous_result.node_type == NodeType.OBSERVATION
                    and result_outcome != SemanticOutcome.POSITIVE.value
                ):
                    continue
                if not self.graph.resolving_edges(previous_result.node_id):
                    self.graph.connect(
                        previous_result.node_id,
                        result.node_id,
                        EdgeType.RESOLVED_BY,
                        metadata={"inferred_from_retry": True},
                    )

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
                token_count=token_count
                if token_count is not None
                else estimate_tokens(error_payload),
                raw_ref=raw_ref,
                active=True,
                metadata={
                    "status": ToolStatus.FAILED.value,
                    "tool_name": name,
                    "semantic_outcome": SemanticOutcome.NEGATIVE.value,
                },
            )
            self.graph.connect(call.node_id, error.node_id, EdgeType.FAILED_WITH)
            raise
        raw_ref = self.archive.put(
            result,
            metadata={
                "kind": "tool_observation",
                "session_id": self.graph.session_id,
                "step_id": step_id,
            },
        )
        semantic_outcome = infer_semantic_outcome(result, ToolStatus.SUCCESS)
        observation = self.graph.create_node(
            NodeType.OBSERVATION,
            result,
            step_id,
            lifecycle=LifecycleState.ACTIVE,
            token_count=token_count if token_count is not None else estimate_tokens(result),
            raw_ref=raw_ref,
            active=True,
            metadata={
                "status": ToolStatus.SUCCESS.value,
                "tool_name": name,
                "semantic_outcome": semantic_outcome.value,
            },
        )
        self.graph.connect(call.node_id, observation.node_id, EdgeType.PRODUCES)
        self._resolve_retried_results(call, observation)
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
        semantic_outcome = infer_semantic_outcome(payload, status)
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
            metadata={
                "status": status.value,
                "tool_name": tool_name,
                "semantic_outcome": semantic_outcome.value,
            },
        )
        relation = EdgeType.FAILED_WITH if is_failure else EdgeType.PRODUCES
        self.graph.connect(call.node_id, result.node_id, relation)
        self._resolve_retried_results(call, result)
        return call, result
