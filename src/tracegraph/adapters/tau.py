"""Import τ-bench and current τ³-bench result files into TraceGraph.

The adapter has no dependency on the upstream package. It consumes the JSON
contract so offline analysis can run under Python 3.11 while live τ³ runs stay
in the upstream Python 3.12/``uv`` environment.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..archive import ArchiveStore
from ..capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens
from ..graph import TraceGraph
from ..lifecycle import LifecycleEngine
from ..schema import (
    EdgeType,
    LifecycleState,
    Node,
    NodeType,
    SemanticOutcome,
    ToolStatus,
)
from ..semantics import infer_semantic_outcome, operation_key


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


class TauTraceImporter:
    """Convert saved upstream simulations into dependency trace graphs."""

    DEFAULT_SIDE_EFFECT_PREFIXES = (
        "book",
        "cancel",
        "change",
        "create",
        "delete",
        "exchange",
        "modify",
        "refund",
        "return",
        "send",
        "transfer",
        "update",
        "write",
    )

    def __init__(
        self,
        archive: ArchiveStore,
        *,
        side_effect_prefixes: tuple[str, ...] | None = None,
    ) -> None:
        self.archive = archive
        self.side_effect_prefixes = side_effect_prefixes or self.DEFAULT_SIDE_EFFECT_PREFIXES

    def _is_side_effect(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered.startswith(self.side_effect_prefixes)

    @staticmethod
    def _messages(simulation: dict[str, Any]) -> list[dict[str, Any]]:
        messages = simulation.get("messages")
        if messages is None:
            messages = simulation.get("trajectory", simulation.get("traj", []))
        if not isinstance(messages, list):
            raise ValueError("simulation messages/trajectory/traj must be a list")
        flattened: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            tool_messages = item.get("tool_messages")
            if isinstance(tool_messages, list):
                flattened.extend(entry for entry in tool_messages if isinstance(entry, dict))
            else:
                flattened.append(item)
        return flattened

    @staticmethod
    def _task_lookup(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
        tasks = container.get("tasks", [])
        return {
            str(task.get("id")): task
            for task in tasks
            if isinstance(task, dict) and task.get("id") is not None
        }

    @staticmethod
    def iter_payloads(path: str | Path) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
        """Yield ``(simulation, task)`` from current and legacy result layouts."""

        source = Path(path)
        if source.is_dir():
            metadata_path = source / "results.json"
            if not metadata_path.is_file():
                raise FileNotFoundError(f"missing {metadata_path}")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            tasks = TauTraceImporter._task_lookup(metadata)
            inline = metadata.get("simulations", [])
            if inline:
                for simulation in inline:
                    if isinstance(simulation, dict):
                        yield simulation, tasks.get(str(simulation.get("task_id")))
                return
            simulations_dir = source / "simulations"
            for simulation_path in sorted(simulations_dir.glob("*.json")):
                simulation = json.loads(simulation_path.read_text(encoding="utf-8"))
                yield simulation, tasks.get(str(simulation.get("task_id")))
            return

        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for simulation in payload:
                if isinstance(simulation, dict):
                    yield simulation, None
            return
        if not isinstance(payload, dict):
            raise ValueError("τ-bench input must be a JSON object, list, or results directory")
        tasks = TauTraceImporter._task_lookup(payload)
        simulations = payload.get("simulations")
        if isinstance(simulations, list):
            for simulation in simulations:
                if isinstance(simulation, dict):
                    yield simulation, tasks.get(str(simulation.get("task_id")))
            return
        if payload.get("task_id") is not None or any(
            key in payload for key in ("messages", "trajectory", "traj")
        ):
            yield payload, tasks.get(str(payload.get("task_id")))
            return
        raise ValueError("no simulations found in τ-bench input")

    def import_path(
        self,
        path: str | Path,
        *,
        policy: str | None = None,
    ) -> list[TraceGraph]:
        return [
            self.import_simulation(simulation, task=task, policy=policy)
            for simulation, task in self.iter_payloads(path)
        ]

    def _archive(
        self,
        payload: Any,
        *,
        kind: str,
        graph: TraceGraph,
        step_id: int,
    ) -> str:
        return self.archive.put(
            payload,
            metadata={"kind": kind, "session_id": graph.session_id, "step_id": step_id},
        )

    def import_simulation(
        self,
        simulation: dict[str, Any],
        *,
        task: dict[str, Any] | None = None,
        policy: str | None = None,
    ) -> TraceGraph:
        explicit_id = simulation.get("id")
        if explicit_id is not None:
            simulation_id = str(explicit_id)
        else:
            task_part = simulation.get("task_id", "unknown_task")
            trial_part = simulation.get("trial", 0)
            simulation_id = f"task_{task_part}_trial_{trial_part}"
        reward_info = _as_dict(simulation.get("reward_info"))
        reward = reward_info.get("reward", simulation.get("reward"))
        graph = TraceGraph(
            session_id=f"tau_{simulation_id}",
            metadata={
                "source": "tau_bench_json",
                "token_accounting": TOKEN_ACCOUNTING_VERSION,
                "simulation_id": simulation_id,
                "task_id": simulation.get("task_id"),
                "trial": simulation.get("trial"),
                "reward": reward,
                "task_success": reward,
                "termination_reason": simulation.get("termination_reason"),
                "agent_cost": simulation.get("agent_cost"),
                "duration": simulation.get("duration"),
                "upstream_info": simulation.get("info"),
            },
        )
        task = task or {}
        goal_content: Any = (
            task.get("user_scenario")
            or task.get("instruction")
            or task.get("description")
            or {"task_id": simulation.get("task_id")}
        )
        graph.create_node(
            NodeType.GOAL,
            goal_content,
            0,
            lifecycle=LifecycleState.ACTIVE,
            token_count=estimate_tokens(goal_content),
            metadata={"source": "task"},
        )
        effective_policy = policy or simulation.get("policy")
        constraint_nodes: list[Node] = []
        if effective_policy:
            constraint_nodes.append(
                graph.create_node(
                    NodeType.CONSTRAINT,
                    effective_policy,
                    0,
                    lifecycle=LifecycleState.ACTIVE,
                    token_count=estimate_tokens(effective_policy),
                    metadata={"source": "domain_policy"},
                )
            )

        pending_calls: dict[str, Node] = {}
        call_signatures: dict[str, tuple[str, str]] = {}
        call_operation_keys: dict[str, str] = {}
        failed_by_signature: dict[tuple[str, str], tuple[str, str]] = {}
        failed_by_operation: dict[str, tuple[str, str]] = {}
        latest_success: dict[str, str] = {}
        recent_results: list[str] = []
        decisions: list[Node] = []
        user_turns = 0

        for ordinal, message in enumerate(self._messages(simulation), start=1):
            step_id = int(message.get("turn_idx", ordinal) or ordinal)
            role = str(message.get("role", message.get("speaker", ""))).lower()
            content = message.get("content")
            if role == "system":
                if content:
                    constraint_nodes.append(
                        graph.create_node(
                            NodeType.CONSTRAINT,
                            content,
                            step_id,
                            lifecycle=LifecycleState.ACTIVE,
                            token_count=estimate_tokens(content),
                            metadata={
                                "source": "system_message",
                                "source_message_ordinal": ordinal,
                            },
                        )
                    )
                continue
            if role == "user":
                user_turns += 1
                if content:
                    node_type = NodeType.SUBGOAL if user_turns > 1 else NodeType.GOAL
                    graph.create_node(
                        node_type,
                        content,
                        step_id,
                        lifecycle=LifecycleState.ACTIVE,
                        token_count=estimate_tokens(content),
                        metadata={
                            "source": "user_message",
                            "source_message_ordinal": ordinal,
                            "provider_usage": _as_dict(message.get("usage")),
                        },
                    )
                # Current τ³ also permits user-side tools. They are captured below.

            if role in {"assistant", "agent"} and content:
                decision = graph.create_node(
                    NodeType.DECISION,
                    content,
                    step_id,
                    lifecycle=LifecycleState.ACTIVE,
                    token_count=estimate_tokens(content),
                    metadata={
                        "source": "assistant_message",
                        "source_message_ordinal": ordinal,
                        "final": False,
                        "provider_usage": _as_dict(message.get("usage")),
                    },
                )
                decisions.append(decision)
                for result_id in recent_results:
                    semantic_outcome = graph.nodes[result_id].metadata.get("semantic_outcome")
                    relation = (
                        EdgeType.BLOCKS
                        if graph.nodes[result_id].node_type == NodeType.ERROR
                        or semantic_outcome
                        in {
                            SemanticOutcome.NEGATIVE.value,
                            SemanticOutcome.POLICY_DENIED.value,
                            SemanticOutcome.TEST_FAILED.value,
                        }
                        else EdgeType.SUPPORTS
                    )
                    graph.connect(result_id, decision.node_id, relation, confidence=0.5)
                recent_results = []

            tool_calls = message.get("tool_calls") or message.get("function_calls") or []
            if isinstance(tool_calls, dict):
                tool_calls = [tool_calls]
            for index, call_payload in enumerate(tool_calls):
                if not isinstance(call_payload, dict):
                    continue
                function = _as_dict(call_payload.get("function"))
                tool_name = str(call_payload.get("name") or function.get("name") or "unknown_tool")
                arguments = call_payload.get("arguments", function.get("arguments", {}))
                arguments = _parse_content(arguments)
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
                call_id = str(call_payload.get("id") or f"turn_{step_id}_call_{index}")
                side_effect = self._is_side_effect(tool_name)
                canonical_arguments = json.dumps(
                    arguments, ensure_ascii=False, sort_keys=True, default=str
                )
                signature = (tool_name, canonical_arguments)
                structural_key = operation_key(tool_name, arguments)
                raw_ref = self._archive(
                    call_payload,
                    kind="tau_tool_call",
                    graph=graph,
                    step_id=step_id,
                )
                call = graph.create_node(
                    NodeType.TOOL_CALL,
                    {"tool_name": tool_name, "arguments": arguments, "call_id": call_id},
                    step_id,
                    lifecycle=(
                        LifecycleState.AUDIT_REQUIRED if side_effect else LifecycleState.ACTIVE
                    ),
                    token_count=estimate_tokens(arguments),
                    raw_ref=raw_ref,
                    side_effect=side_effect,
                    metadata={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "requestor": role,
                        "source_message_ordinal": ordinal,
                        "canonical_arguments": canonical_arguments,
                        "operation_key": structural_key,
                    },
                )
                pending_calls[call_id] = call
                call_signatures[call_id] = signature
                call_operation_keys[call_id] = structural_key
                retry_match = failed_by_signature.get(signature)
                match_type = "exact_signature"
                confidence = 1.0
                if retry_match is None:
                    retry_match = failed_by_operation.get(structural_key)
                    match_type = "structural_operation"
                    confidence = 0.8
                if retry_match is not None:
                    graph.connect(
                        retry_match[0],
                        call.node_id,
                        EdgeType.RETRIED_BY,
                        confidence=confidence,
                        metadata={"match_type": match_type, "inferred": True},
                    )
                if decisions:
                    graph.connect(decisions[-1].node_id, call.node_id, EdgeType.LEADS_TO)
                for constraint in constraint_nodes:
                    if side_effect:
                        graph.connect(
                            constraint.node_id,
                            call.node_id,
                            EdgeType.BLOCKS,
                            confidence=0.5,
                            metadata={"inferred": True},
                        )

            if role != "tool":
                continue
            call_id = str(message.get("id", message.get("tool_call_id", "")))
            call = pending_calls.get(call_id)
            if call is None:
                raw_call_ref = self._archive(
                    {"call_id": call_id, "inferred": True},
                    kind="tau_orphan_tool_call",
                    graph=graph,
                    step_id=step_id,
                )
                call = graph.create_node(
                    NodeType.TOOL_CALL,
                    {"tool_name": "unknown_tool", "arguments": {}, "call_id": call_id},
                    step_id,
                    token_count=1,
                    raw_ref=raw_call_ref,
                    metadata={
                        "tool_name": "unknown_tool",
                        "call_id": call_id,
                        "orphan": True,
                        "source_message_ordinal": ordinal,
                    },
                )
                pending_calls[call_id] = call
                call_signatures[call_id] = ("unknown_tool", "{}")
                call_operation_keys[call_id] = operation_key("unknown_tool", {})
            payload = _parse_content(content)
            legacy_error = isinstance(content, str) and content.lstrip().lower().startswith(
                ("error:", "error ")
            )
            structured_error = isinstance(payload, dict) and bool(payload.get("error", False))
            is_error = bool(message.get("error", False)) or legacy_error or structured_error
            status = ToolStatus.FAILED if is_error else ToolStatus.SUCCESS
            semantic_outcome = infer_semantic_outcome(payload, status)
            raw_ref = self._archive(
                message,
                kind="tau_tool_error" if is_error else "tau_tool_observation",
                graph=graph,
                step_id=step_id,
            )
            result = graph.create_node(
                NodeType.ERROR if is_error else NodeType.OBSERVATION,
                payload,
                step_id,
                lifecycle=(
                    LifecycleState.UNRESOLVED_FAILURE if is_error else LifecycleState.ACTIVE
                ),
                token_count=estimate_tokens(payload),
                raw_ref=raw_ref,
                metadata={
                    "status": status.value,
                    "tool_name": call.metadata.get("tool_name"),
                    "call_id": call_id,
                    "source_message_ordinal": ordinal,
                    "semantic_outcome": semantic_outcome.value,
                },
            )
            graph.connect(
                call.node_id,
                result.node_id,
                EdgeType.FAILED_WITH if is_error else EdgeType.PRODUCES,
            )
            signature = call_signatures.get(call_id, ("unknown_tool", "{}"))
            structural_key = call_operation_keys.get(call_id, operation_key(signature[0], {}))
            semantic_negative = semantic_outcome in {
                SemanticOutcome.NEGATIVE,
                SemanticOutcome.POLICY_DENIED,
                SemanticOutcome.TEST_FAILED,
            }
            retry_call_ids = set(graph.retry_predecessors(call.node_id))
            if semantic_negative:
                for prior_call_id in retry_call_ids:
                    prior_results = graph.outgoing(
                        prior_call_id, EdgeType.FAILED_WITH
                    ) + graph.outgoing(prior_call_id, EdgeType.PRODUCES)
                    for prior_edge in prior_results:
                        prior_result = graph.nodes[prior_edge.target]
                        prior_outcome = prior_result.metadata.get("semantic_outcome")
                        if prior_result.node_type == NodeType.ERROR or prior_outcome in {
                            SemanticOutcome.NEGATIVE.value,
                            SemanticOutcome.POLICY_DENIED.value,
                            SemanticOutcome.TEST_FAILED.value,
                        }:
                            if not graph.resolving_edges(
                                prior_result.node_id
                            ) and not graph.superseding_edges(prior_result.node_id):
                                graph.connect(
                                    prior_result.node_id,
                                    result.node_id,
                                    EdgeType.SUPERSEDED_BY,
                                    metadata={"inferred_from_failed_retry": True},
                                )
                if retry_call_ids:
                    failed_by_signature = {
                        key: value
                        for key, value in failed_by_signature.items()
                        if value[0] not in retry_call_ids
                    }
                    failed_by_operation = {
                        key: value
                        for key, value in failed_by_operation.items()
                        if value[0] not in retry_call_ids
                    }
                failed_by_signature[signature] = (call.node_id, result.node_id)
                failed_by_operation[structural_key] = (call.node_id, result.node_id)
            else:
                resolved_call_ids = retry_call_ids
                for prior_call_id in resolved_call_ids:
                    prior_results = graph.outgoing(
                        prior_call_id, EdgeType.FAILED_WITH
                    ) + graph.outgoing(prior_call_id, EdgeType.PRODUCES)
                    for prior_edge in prior_results:
                        prior_result = graph.nodes[prior_edge.target]
                        prior_outcome = prior_result.metadata.get("semantic_outcome")
                        if prior_result.node_type == NodeType.ERROR or prior_outcome in {
                            SemanticOutcome.NEGATIVE.value,
                            SemanticOutcome.POLICY_DENIED.value,
                            SemanticOutcome.TEST_FAILED.value,
                        }:
                            if (
                                prior_result.node_type == NodeType.OBSERVATION
                                and semantic_outcome != SemanticOutcome.POSITIVE
                            ):
                                continue
                            graph.connect(
                                prior_result.node_id,
                                result.node_id,
                                EdgeType.RESOLVED_BY,
                                metadata={"inferred_from_retry": True},
                            )
                if resolved_call_ids:
                    failed_by_signature = {
                        key: value
                        for key, value in failed_by_signature.items()
                        if value[0] not in resolved_call_ids
                    }
                    failed_by_operation = {
                        key: value
                        for key, value in failed_by_operation.items()
                        if value[0] not in resolved_call_ids
                    }
                previous_observation = latest_success.get(structural_key)
                if previous_observation is not None:
                    graph.connect(
                        previous_observation,
                        result.node_id,
                        EdgeType.SUPERSEDED_BY,
                    )
                latest_success[structural_key] = result.node_id
            recent_results.append(result.node_id)

        if decisions:
            decisions[-1].metadata["final"] = True
        transitions = LifecycleEngine().apply(graph)
        graph.metadata["lifecycle_transitions"] = {
            node_id: [before.value, after.value] for node_id, (before, after) in transitions.items()
        }
        graph.metadata["graph_validation_errors"] = graph.validate()
        return graph
