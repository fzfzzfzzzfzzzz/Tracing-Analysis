"""Benchmark-independent online tool-calling agent scaffold.

All experimental conditions use this same control loop, model backend, tools,
and tasks. Only the ``ContextManager`` changes, which isolates the independent
variable described in the research report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .archive import ArchiveStore
from .capture import ToolExecutor, estimate_tokens
from .context import ContextManager, ContextView
from .graph import TraceGraph
from .lifecycle import LifecycleEngine
from .schema import EdgeType, LifecycleState, NodeType, ToolStatus


@dataclass(frozen=True, slots=True)
class ToolRequest:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass(slots=True)
class ModelTurn:
    content: str | None = None
    tool_calls: list[ToolRequest] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: Any = None

    def __post_init__(self) -> None:
        if self.content and self.tool_calls:
            raise ValueError("a model turn must contain text or tool calls, not both")
        if not self.content and not self.tool_calls:
            raise ValueError("a model turn cannot be empty")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    function: Any
    description: str = ""
    side_effect: bool = False


class ModelBackend(Protocol):
    """Provider-neutral model interface used by the fixed agent scaffold."""

    def generate(self, context: ContextView, tools: list[ToolSpec]) -> ModelTurn: ...


@dataclass(slots=True)
class AgentRunResult:
    graph: TraceGraph
    final_text: str | None
    termination_reason: str
    context_views: list[ContextView]


class ContextManagedAgent:
    """A fixed tool-calling loop with a replaceable context manager."""

    def __init__(
        self,
        *,
        backend: ModelBackend,
        tools: list[ToolSpec],
        context_manager: ContextManager,
        archive: ArchiveStore,
        budget: int | None = None,
        max_steps: int = 32,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        self.backend = backend
        self.tools = tools
        self.context_manager = context_manager
        self.archive = archive
        self.budget = budget
        self.max_steps = max_steps

    def run(
        self,
        goal: Any,
        *,
        constraints: list[str] | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        graph = TraceGraph(session_id=session_id, metadata=metadata)
        graph.metadata.update(
            {
                "context_manager": self.context_manager.name,
                "budget": self.budget,
                "max_steps": self.max_steps,
            }
        )
        graph.create_node(
            NodeType.GOAL,
            goal,
            0,
            lifecycle=LifecycleState.ACTIVE,
            token_count=estimate_tokens(goal),
        )
        for constraint in constraints or []:
            graph.create_node(
                NodeType.CONSTRAINT,
                constraint,
                0,
                lifecycle=LifecycleState.ACTIVE,
                token_count=estimate_tokens(constraint),
            )

        executor = ToolExecutor(graph, self.archive)
        tool_map = {tool.name: tool for tool in self.tools}
        views: list[ContextView] = []
        decisions: list[str] = []
        final_text: str | None = None

        for step_id in range(1, self.max_steps + 1):
            view = self.context_manager.select(graph, budget=self.budget)
            views.append(view)
            turn = self.backend.generate(view, self.tools)
            decision_content: Any = turn.content
            if turn.tool_calls:
                decision_content = {
                    "tool_calls": [
                        {"name": request.name, "arguments": request.arguments}
                        for request in turn.tool_calls
                    ]
                }
            decision = graph.create_node(
                NodeType.DECISION,
                decision_content,
                step_id,
                lifecycle=LifecycleState.ACTIVE,
                token_count=turn.output_tokens or estimate_tokens(decision_content),
                metadata={
                    "final": not turn.tool_calls,
                    "input_tokens": turn.input_tokens or view.selected_tokens,
                    "raw_model_output": turn.raw,
                },
            )
            decisions.append(decision.node_id)
            for item in view.items:
                if item.node_id not in graph.nodes:
                    continue
                if graph.nodes[item.node_id].node_type in {
                    NodeType.OBSERVATION,
                    NodeType.ERROR,
                    NodeType.SUMMARY,
                    NodeType.ARCHIVE_HANDLE,
                }:
                    graph.connect(decision.node_id, item.node_id, EdgeType.USES, confidence=0.8)

            if not turn.tool_calls:
                final_text = turn.content
                LifecycleEngine().apply(graph)
                graph.metadata["termination_reason"] = "model_final"
                graph.metadata["input_tokens_by_step"] = [view.selected_tokens for view in views]
                return AgentRunResult(graph, final_text, "model_final", views)

            for index, request in enumerate(turn.tool_calls):
                spec = tool_map.get(request.name)
                if spec is None:
                    call, result = executor.record_result(
                        tool_name=request.name,
                        arguments=request.arguments,
                        step_id=step_id,
                        status=ToolStatus.FAILED,
                        payload={"type": "UnknownTool", "message": f"unknown tool: {request.name}"},
                    )
                else:
                    try:
                        payload = spec.function(**request.arguments)
                        status = ToolStatus.SUCCESS
                    except TimeoutError as exc:
                        payload = {"type": type(exc).__name__, "message": str(exc)}
                        status = ToolStatus.TIMEOUT
                    except Exception as exc:  # Tool failures are observations, not loop failures.
                        payload = {"type": type(exc).__name__, "message": str(exc)}
                        status = ToolStatus.FAILED
                    call, result = executor.record_result(
                        tool_name=request.name,
                        arguments=request.arguments,
                        step_id=step_id,
                        status=status,
                        payload=payload,
                        side_effect=spec.side_effect,
                    )
                call.metadata["provider_call_id"] = request.call_id or f"step_{step_id}_{index}"
                graph.connect(decision.node_id, call.node_id, EdgeType.LEADS_TO)
                if result.node_type == NodeType.OBSERVATION:
                    for retry in graph.outgoing(call.node_id, EdgeType.RETRIES):
                        failed_edges = graph.outgoing(retry.target, EdgeType.FAILED_WITH)
                        for failed_edge in failed_edges:
                            graph.connect(result.node_id, failed_edge.target, EdgeType.RESOLVES)

        if decisions:
            graph.nodes[decisions[-1]].metadata["final"] = True
        LifecycleEngine().apply(graph)
        graph.metadata["termination_reason"] = "max_steps"
        graph.metadata["input_tokens_by_step"] = [view.selected_tokens for view in views]
        return AgentRunResult(graph, final_text, "max_steps", views)


class ScriptedBackend:
    """Deterministic backend for tests and labeled synthetic smoke experiments."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.position = 0

    def generate(self, context: ContextView, tools: list[ToolSpec]) -> ModelTurn:
        if self.position >= len(self.turns):
            raise RuntimeError("scripted backend exhausted")
        turn = self.turns[self.position]
        self.position += 1
        if turn.input_tokens is None:
            turn.input_tokens = context.selected_tokens
        return turn
