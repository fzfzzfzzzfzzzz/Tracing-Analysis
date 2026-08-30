"""Deterministic TraceLifecycle-Fork v1 scenario and gold generator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .archive import ArchiveStore
from .capture import estimate_tokens
from .decision_state import stable_digest
from .goal_lifecycle import GoalContext, GoalLifecycleState
from .graph import TraceGraph
from .schema import Edge, EdgeType, Node, NodeType


SCENARIO_SCHEMA_VERSION = "trace_lifecycle_fork_v1"
DEFAULT_BASE_SEED = 20260830
_CREATED_AT = "2026-08-30T00:00:00+00:00"

SCENARIO_FAMILIES = (
    "F1_shell_switch",
    "F2_failed_approach",
    "F3_goal_resume",
    "F4_explainable_process",
    "F5_state_supersession",
    "F6_side_effect_audit",
)
VARIANTS = (
    ("small_cheap", 256, "cheap"),
    ("small_costly", 256, "costly"),
    ("large_cheap", 2048, "cheap"),
    ("large_costly", 2048, "costly"),
)
FORK_TYPES = ("CONTINUE", "REACTIVATE", "DISTRACTOR")


@dataclass(frozen=True, slots=True)
class ScenarioFork:
    fork_id: str
    prefix_id: str
    request: Mapping[str, Any]
    required_anchor_ids: tuple[str, ...]
    required_subgraph_event_ids: tuple[str, ...]
    forbidden_current_fact_ids: tuple[str, ...]
    expected_answer_facts: tuple[str, ...]
    expected_final_state: Mapping[str, Any]
    fork_type: str
    reacquisition_calls: int
    reacquisition_observation_tokens: int

    def __post_init__(self) -> None:
        if self.fork_type not in FORK_TYPES:
            raise ValueError(f"unsupported fork_type: {self.fork_type}")
        for field in (
            "required_anchor_ids",
            "required_subgraph_event_ids",
            "forbidden_current_fact_ids",
            "expected_answer_facts",
        ):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))

    def request_payload(self) -> dict[str, Any]:
        """Return exactly what a manager may observe; gold fields stay private."""

        return json.loads(json.dumps(dict(self.request), ensure_ascii=False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCENARIO_SCHEMA_VERSION,
            "fork_id": self.fork_id,
            "prefix_id": self.prefix_id,
            "fork_type": self.fork_type,
            "request": dict(self.request),
            "required_anchor_ids": list(self.required_anchor_ids),
            "required_subgraph_event_ids": list(self.required_subgraph_event_ids),
            "forbidden_current_fact_ids": list(self.forbidden_current_fact_ids),
            "expected_answer_facts": list(self.expected_answer_facts),
            "expected_final_state": dict(self.expected_final_state),
            "reacquisition_calls": self.reacquisition_calls,
            "reacquisition_observation_tokens": self.reacquisition_observation_tokens,
        }


@dataclass(frozen=True, slots=True)
class ScenarioPrefix:
    prefix_id: str
    scenario_family: str
    variant_id: str
    payload_target_tokens: int
    reacquisition_mode: str
    graph: TraceGraph
    messages: tuple[dict[str, Any], ...]
    tool_schemas: tuple[dict[str, Any], ...]
    goal_context: GoalContext
    lifecycle_gold_by_event: Mapping[str, GoalLifecycleState]
    oracle_removable_span_ids: tuple[str, ...]
    forks: tuple[ScenarioFork, ...]
    base_seed: int

    @property
    def prefix_hash(self) -> str:
        return stable_digest(self.to_prefix_dict(include_hash=False))

    def to_prefix_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": SCENARIO_SCHEMA_VERSION,
            "prefix_id": self.prefix_id,
            "scenario_family": self.scenario_family,
            "variant_id": self.variant_id,
            "payload_target_tokens": self.payload_target_tokens,
            "reacquisition_mode": self.reacquisition_mode,
            "base_seed": self.base_seed,
            "visible_prefix_event_ids": sorted(self.graph.nodes),
            "graph": self.graph.to_dict(),
            "messages": [dict(item) for item in self.messages],
            "tool_schemas": [dict(item) for item in self.tool_schemas],
            "goal_context": self.goal_context.to_dict(),
        }
        if include_hash:
            value["prefix_hash"] = self.prefix_hash
        return value

    def to_gold_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCENARIO_SCHEMA_VERSION,
            "prefix_id": self.prefix_id,
            "scenario_family": self.scenario_family,
            "lifecycle_gold_by_event": {
                key: value.value
                for key, value in sorted(self.lifecycle_gold_by_event.items())
            },
            "pinned_event_ids": sorted(
                key
                for key, value in self.lifecycle_gold_by_event.items()
                if value == GoalLifecycleState.PINNED
            ),
            "active_event_ids": sorted(
                key
                for key, value in self.lifecycle_gold_by_event.items()
                if value == GoalLifecycleState.ACTIVE
            ),
            "dormant_event_ids": sorted(
                key
                for key, value in self.lifecycle_gold_by_event.items()
                if value == GoalLifecycleState.DORMANT
            ),
            "superseded_event_ids": sorted(
                key
                for key, value in self.lifecycle_gold_by_event.items()
                if value == GoalLifecycleState.SUPERSEDED
            ),
            "ephemeral_event_ids": sorted(
                key
                for key, value in self.lifecycle_gold_by_event.items()
                if value == GoalLifecycleState.EPHEMERAL
            ),
            "uncertain_event_ids": sorted(
                key
                for key, value in self.lifecycle_gold_by_event.items()
                if value == GoalLifecycleState.UNCERTAIN
            ),
            "oracle_removable_spans": list(self.oracle_removable_span_ids),
            "forks": [item.to_dict() for item in self.forks],
            "gold_source": "deterministic_scenario_state_machine_v1",
            "llm_generated": False,
        }


class _ScenarioAssembly:
    def __init__(
        self,
        *,
        prefix_id: str,
        archive: ArchiveStore,
    ) -> None:
        self.graph = TraceGraph(
            session_id=prefix_id,
            metadata={"phase": 6, "synthetic": True, "prefix_id": prefix_id},
        )
        self.archive = archive
        self.messages: list[dict[str, Any]] = []
        self.gold: dict[str, GoalLifecycleState] = {}
        self.step = 0
        self.edge_counter = 0

    def _ordinal(self, message: Mapping[str, Any] | None) -> int | None:
        if message is None:
            return None
        self.messages.append(dict(message))
        return len(self.messages)

    def node(
        self,
        suffix: str,
        node_type: NodeType,
        content: Any,
        state: GoalLifecycleState,
        *,
        goal_id: str | None = None,
        chain_id: str | None = None,
        message: Mapping[str, Any] | None = None,
        archived: bool = False,
        side_effect: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> Node:
        self.step += 1
        values = dict(metadata or {})
        if goal_id:
            values["goal_id"] = goal_id
        if chain_id:
            values["causal_chain_id"] = chain_id
        ordinal = self._ordinal(message)
        if ordinal is not None:
            values["source_message_ordinal"] = ordinal
        raw_ref = self.archive.put(content, metadata={"event_id": suffix}) if archived else None
        node = Node(
            node_type=node_type,
            content=content,
            step_id=self.step,
            token_count=estimate_tokens(content),
            raw_ref=raw_ref,
            side_effect=side_effect,
            metadata=values,
            node_id=f"{self.graph.session_id}:{suffix}",
            created_at=_CREATED_AT,
        )
        self.graph.add_node(node)
        self.gold[node.node_id] = state
        return node

    def edge(self, source: Node, target: Node, edge_type: EdgeType) -> None:
        self.edge_counter += 1
        self.graph.add_edge(
            Edge(
                source=source.node_id,
                target=target.node_id,
                edge_type=edge_type,
                edge_id=f"{self.graph.session_id}:edge:{self.edge_counter:03d}",
                created_at=_CREATED_AT,
            )
        )

    def goal(self, suffix: str, text: str, state: GoalLifecycleState, goal_id: str) -> Node:
        return self.node(
            suffix,
            NodeType.GOAL,
            text,
            state,
            goal_id=goal_id,
            chain_id=f"chain:{goal_id}",
            message={"role": "user", "content": text},
            metadata={"retrieval_terms": [goal_id, *text.lower().split()]},
        )

    def decision(
        self,
        suffix: str,
        text: str,
        state: GoalLifecycleState,
        *,
        goal_id: str,
        chain_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Node:
        return self.node(
            suffix,
            NodeType.DECISION,
            text,
            state,
            goal_id=goal_id,
            chain_id=chain_id,
            message={"role": "assistant", "content": text},
            metadata=metadata,
        )

    def exchange(
        self,
        suffix: str,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Any,
        state: GoalLifecycleState,
        goal_id: str,
        chain_id: str,
        failed: bool = False,
        side_effect: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[Node, Node]:
        call_id = f"{self.graph.session_id}:{suffix}:call"
        common = dict(metadata or {})
        retrieval_terms = list(common.get("retrieval_terms", ()))
        call_content = {
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "call_id": call_id,
        }
        call = self.node(
            f"{suffix}:call",
            NodeType.TOOL_CALL,
            call_content,
            state,
            goal_id=goal_id,
            chain_id=chain_id,
            message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(
                                dict(arguments), ensure_ascii=False, sort_keys=True
                            ),
                        },
                    }
                ],
            },
            archived=True,
            side_effect=side_effect,
            metadata={
                **common,
                "tool_name": tool_name,
                "call_id": call_id,
                "operation": str(arguments.get("operation") or tool_name),
                "retrieval_terms": retrieval_terms,
            },
        )
        result_node = self.node(
            f"{suffix}:result",
            NodeType.ERROR if failed else NodeType.OBSERVATION,
            result,
            state,
            goal_id=goal_id,
            chain_id=chain_id,
            message={
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
            },
            archived=True,
            side_effect=side_effect,
            metadata={
                **common,
                "call_id": call_id,
                "tool_name": tool_name,
                "retrieval_terms": retrieval_terms,
            },
        )
        self.edge(
            call,
            result_node,
            EdgeType.FAILED_WITH if failed else EdgeType.PRODUCES,
        )
        return call, result_node


def _filler(label: str, target_tokens: int) -> str:
    prefix = f"{label}:"
    return prefix + ("x" * max(0, target_tokens * 4 - len(prefix)))


def _tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Deterministic controlled tool {name}",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "entity": {"type": "string"},
                },
                "required": ["operation"],
            },
        },
    }


def _forks(
    *,
    prefix_id: str,
    family: str,
    current_goal_id: str,
    old_goal_id: str,
    anchor_ids: tuple[str, ...],
    required_ids: tuple[str, ...],
    forbidden_ids: tuple[str, ...],
    answer_fact: str,
    entity: str,
    error_signature: str | None,
    reacquisition_mode: str,
) -> tuple[ScenarioFork, ...]:
    if family == "F3_goal_resume":
        reactivate_request = {
            "text": f"Resume the earlier goal {old_goal_id} for {entity}.",
            "current_goal_id": current_goal_id,
            "resume_goal_id": old_goal_id,
            "referenced_entities": [entity],
        }
    elif error_signature:
        reactivate_request = {
            "text": f"The previous {error_signature} happened again for {entity}; explain why.",
            "current_goal_id": current_goal_id,
            "error_signature": error_signature,
            "referenced_entities": [entity],
            "historical_intent": True,
        }
    else:
        reactivate_request = {
            "text": f"Audit why the previous operation for {entity} was performed.",
            "current_goal_id": current_goal_id,
            "historical_intent": True,
            "historical_reference": True,
            "referenced_entities": [entity],
            "referenced_event_ids": list(anchor_ids[:1]),
        }
    calls = 1 if reacquisition_mode == "cheap" else 3
    observation_tokens = 128 if reacquisition_mode == "cheap" else 512
    rows = (
        ScenarioFork(
            fork_id=f"{prefix_id}:continue",
            prefix_id=prefix_id,
            fork_type="CONTINUE",
            request={
                "text": f"Continue the current task for {entity} using the current state.",
                "current_goal_id": current_goal_id,
                "referenced_entities": [entity],
            },
            required_anchor_ids=(),
            required_subgraph_event_ids=(),
            forbidden_current_fact_ids=forbidden_ids,
            expected_answer_facts=(f"current:{entity}",),
            expected_final_state={"goal_id": current_goal_id, "status": "continued"},
            reacquisition_calls=0,
            reacquisition_observation_tokens=0,
        ),
        ScenarioFork(
            fork_id=f"{prefix_id}:reactivate",
            prefix_id=prefix_id,
            fork_type="REACTIVATE",
            request=reactivate_request,
            required_anchor_ids=anchor_ids,
            required_subgraph_event_ids=required_ids,
            forbidden_current_fact_ids=forbidden_ids,
            expected_answer_facts=(answer_fact,),
            expected_final_state={"goal_id": old_goal_id, "status": "historically_explained"},
            reacquisition_calls=calls,
            reacquisition_observation_tokens=observation_tokens,
        ),
        ScenarioFork(
            fork_id=f"{prefix_id}:distractor",
            prefix_id=prefix_id,
            fork_type="DISTRACTOR",
            request={
                "text": f"Use {entity} for a new current operation; do not revisit history.",
                "current_goal_id": current_goal_id,
                "referenced_entities": [entity],
            },
            required_anchor_ids=(),
            required_subgraph_event_ids=(),
            forbidden_current_fact_ids=forbidden_ids,
            expected_answer_facts=(f"current:{entity}",),
            expected_final_state={"goal_id": current_goal_id, "status": "new_operation"},
            reacquisition_calls=0,
            reacquisition_observation_tokens=0,
        ),
    )
    return rows


def _build_prefix(
    family: str,
    variant_id: str,
    payload_tokens: int,
    reacquisition_mode: str,
    *,
    archive: ArchiveStore,
    base_seed: int,
) -> ScenarioPrefix:
    prefix_id = f"{family}:{variant_id}"
    old_goal_id = f"{prefix_id}:goal-old"
    current_goal_id = f"{prefix_id}:goal-current"
    chain_id = f"{prefix_id}:causal-history"
    entity = {
        "F1_shell_switch": "shell-script.ps1",
        "F2_failed_approach": "module-alpha",
        "F3_goal_resume": "goal-a-worktree",
        "F4_explainable_process": "calculation-42",
        "F5_state_supersession": "service-config",
        "F6_side_effect_audit": "order-9001",
    }[family]
    a = _ScenarioAssembly(prefix_id=prefix_id, archive=archive)
    old_goal = a.goal(
        "old-goal",
        f"Investigate historical work for {entity}",
        GoalLifecycleState.DORMANT,
        old_goal_id,
    )
    pinned = a.node(
        "constraint",
        NodeType.CONSTRAINT,
        f"Never repeat irreversible operations for {entity} without confirmation.",
        GoalLifecycleState.PINNED,
        goal_id=current_goal_id,
        metadata={"retention_obligation": "policy", "pinned": True},
    )
    del pinned

    error_signature: str | None = None
    forbidden_ids: tuple[str, ...] = ()
    if family in {"F1_shell_switch", "F2_failed_approach"}:
        if family == "F1_shell_switch":
            first_tool, second_tool = "powershell", "bash"
            error_signature = "shell_syntax_error"
            answer_fact = "PowerShell syntax failed; Bash-compatible syntax resolved it."
        else:
            first_tool, second_tool = "approach_a", "approach_b"
            error_signature = "approach_a_failure"
            answer_fact = "Approach A failed on diagnostic evidence; Approach B succeeded."
        failed_call, failed_result = a.exchange(
            "failed-attempt",
            tool_name=first_tool,
            arguments={"operation": "attempt", "entity": entity},
            result={
                "error": error_signature,
                "detail": _filler(error_signature, payload_tokens),
            },
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            failed=True,
            metadata={
                "error_signature": error_signature,
                "retrieval_terms": [entity, error_signature, first_tool, "previous"],
                "guard_text": f"Avoid {first_tool}: {error_signature}",
                "reactivation_token_count": 96,
                "reactivation_value": True,
            },
        )
        decision = a.decision(
            "switch-decision",
            f"Switch from {first_tool} to {second_tool} because {error_signature}.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, first_tool, second_tool],
                "reactivation_value": True,
            },
        )
        retry_call, retry_result = a.exchange(
            "successful-alternative",
            tool_name=second_tool,
            arguments={"operation": "retry", "entity": entity},
            result={"status": "success", "entity": entity, "method": second_tool},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, second_tool, "resolved"],
                "reactivation_value": True,
                "reactivation_token_count": 64,
            },
        )
        a.edge(failed_result, decision, EdgeType.BLOCKS)
        a.edge(decision, retry_call, EdgeType.LEADS_TO)
        a.edge(failed_call, retry_call, EdgeType.RETRIED_BY)
        a.edge(failed_result, retry_result, EdgeType.RESOLVED_BY)
        anchors = (failed_result.node_id,)
        required = tuple(
            item.node_id
            for item in (failed_call, failed_result, decision, retry_call, retry_result)
        )
    elif family == "F3_goal_resume":
        answer_fact = "Goal A resumes from its saved partial progress and next step."
        progress_call, progress_result = a.exchange(
            "goal-a-progress",
            tool_name="inspect_goal",
            arguments={"operation": "inspect", "entity": entity},
            result={"progress": "60%", "next_step": "apply patch", "detail": _filler("goal-a", payload_tokens)},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, old_goal_id, "resume"],
                "reactivation_value": True,
                "reactivation_token_count": 96,
            },
        )
        pause = a.decision(
            "goal-a-paused",
            "Goal A was paused after partial progress; preserve the next step.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={"retrieval_terms": [entity, old_goal_id, "paused", "resume"]},
        )
        a.edge(progress_result, pause, EdgeType.SUPPORTS)
        anchors = (progress_result.node_id, pause.node_id)
        required = (old_goal.node_id, progress_call.node_id, progress_result.node_id, pause.node_id)
    elif family == "F4_explainable_process":
        answer_fact = "The archived intermediate calculation deterministically produced result 42."
        process_call, process_result = a.exchange(
            "calculation-process",
            tool_name="calculator",
            arguments={"operation": "calculate", "entity": entity},
            result={"intermediate": _filler("calculation trace", payload_tokens), "result": 42},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, "calculation", "42", "intermediate"],
                "reactivation_value": True,
                "reactivation_token_count": 128,
            },
        )
        explanation = a.decision(
            "calculation-conclusion",
            "Intermediate steps support the final result 42.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={"retrieval_terms": [entity, "result", "42"]},
        )
        a.edge(process_result, explanation, EdgeType.SUPPORTS)
        anchors = (process_result.node_id,)
        required = (process_call.node_id, process_result.node_id, explanation.node_id)
    elif family == "F5_state_supersession":
        answer_fact = "The old config value was v1; it was intentionally superseded by v2."
        old_call, old_result = a.exchange(
            "read-old-config",
            tool_name="read_config",
            arguments={"operation": "read", "entity": entity},
            result={"version": "v1", "detail": _filler("old config", payload_tokens)},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, "old", "v1", "previous"],
                "reactivation_value": True,
                "reactivation_token_count": 96,
            },
        )
        reason = a.decision(
            "update-reason",
            "Update was required to replace v1 with v2.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={"retrieval_terms": [entity, "v1", "v2", "why"]},
        )
        new_call, new_result = a.exchange(
            "read-new-config",
            tool_name="read_config",
            arguments={"operation": "read", "entity": entity},
            result={"version": "v2", "status": "current"},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, "new", "v2"],
                "reactivation_value": True,
                "reactivation_token_count": 64,
            },
        )
        a.edge(old_result, reason, EdgeType.SUPPORTS)
        a.edge(old_result, new_result, EdgeType.SUPERSEDED_BY)
        # The oracle records only the old result as epistemically superseded;
        # the protocol call remains dormant and is evicted as one full span.
        a.gold[old_result.node_id] = GoalLifecycleState.SUPERSEDED
        anchors = (old_result.node_id,)
        required = (old_call.node_id, old_result.node_id, reason.node_id, new_call.node_id, new_result.node_id)
        forbidden_ids = (old_result.node_id,)
    else:
        answer_fact = "The archived authorization rationale and pinned receipt prove the side effect was approved once."
        auth_call, auth_result = a.exchange(
            "authorization-check",
            tool_name="check_authorization",
            arguments={"operation": "authorize", "entity": entity},
            result={"authorized": True, "reason": _filler("user confirmation", payload_tokens)},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, "authorization", "confirmation", "audit"],
                "reactivation_value": True,
                "reactivation_token_count": 96,
            },
        )
        rationale = a.decision(
            "authorization-rationale",
            "User confirmation authorized exactly one side effect.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={"retrieval_terms": [entity, "why", "authorization", "audit"]},
        )
        a.edge(auth_result, rationale, EdgeType.SUPPORTS)
        anchors = (auth_result.node_id,)
        required = (auth_call.node_id, auth_result.node_id, rationale.node_id)

    # A separate deterministically recomputable span exercises EPHEMERAL
    # without becoming part of the historical causal chain.
    a.exchange(
        "ephemeral-check",
        tool_name="checksum",
        arguments={"operation": "checksum", "entity": entity},
        result={"checksum": "deterministic"},
        state=GoalLifecycleState.EPHEMERAL,
        goal_id=old_goal_id,
        chain_id=f"{prefix_id}:ephemeral",
        metadata={
            "ephemeral_eligible": True,
            "deterministically_recomputable": True,
            "reactivation_value": False,
            "reactivation_token_count": 16,
        },
    )

    current_goal = a.goal(
        "current-goal",
        f"Complete the current task for {entity}",
        GoalLifecycleState.ACTIVE,
        current_goal_id,
    )
    current_goal.metadata["structured_current"] = True
    current_payload_tokens = max(96, payload_tokens // 3)
    current_call, current_result = a.exchange(
        "current-state",
        tool_name="current_state",
        arguments={"operation": "read_current", "entity": entity},
        result={"entity": entity, "status": "current", "detail": _filler("current", current_payload_tokens)},
        state=GoalLifecycleState.ACTIVE,
        goal_id=current_goal_id,
        chain_id=f"{prefix_id}:current",
        metadata={
            "current_fact": True,
            "current_root": True,
            "retrieval_terms": [entity, "current"],
        },
    )
    del current_goal, current_call, current_result

    if family == "F6_side_effect_audit":
        effect_call, effect_result = a.exchange(
            "side-effect-receipt",
            tool_name="execute_once",
            arguments={"operation": "execute", "entity": entity},
            result={"receipt_id": "receipt-9001", "executed": True},
            state=GoalLifecycleState.PINNED,
            goal_id=current_goal_id,
            chain_id=f"{prefix_id}:receipt",
            side_effect=True,
            metadata={
                "retention_obligation": "receipt",
                "pinned": True,
                "retrieval_terms": [entity, "receipt", "audit"],
            },
        )
        del effect_call, effect_result

    if family == "F4_explainable_process":
        a.node(
            "uncertain-note",
            NodeType.DECISION,
            "An unrelated partial diagnostic remains uncertain.",
            GoalLifecycleState.UNCERTAIN,
            goal_id=current_goal_id,
            metadata={"uncertain": True},
        )

    if a.graph.validate():
        raise ValueError(f"generated invalid graph {prefix_id}: {a.graph.validate()}")
    goal_context = GoalContext(
        current_goal_id=current_goal_id,
        paused_or_cancelled_goal_ids=(old_goal_id,),
        referenced_entities=(entity,),
    )
    forks = _forks(
        prefix_id=prefix_id,
        family=family,
        current_goal_id=current_goal_id,
        old_goal_id=old_goal_id,
        anchor_ids=anchors,
        required_ids=required,
        forbidden_ids=forbidden_ids,
        answer_fact=answer_fact,
        entity=entity,
        error_signature=error_signature,
        reacquisition_mode=reacquisition_mode,
    )
    removable_states = {
        GoalLifecycleState.DORMANT,
        GoalLifecycleState.SUPERSEDED,
        GoalLifecycleState.EPHEMERAL,
    }
    removable_span_ids = sorted(
        {
            (
                f"tool:{a.graph.nodes[event_id].metadata['call_id']}"
                if a.graph.nodes[event_id].metadata.get("call_id")
                else f"event:{event_id}"
            )
            for event_id, state in a.gold.items()
            if state in removable_states
        }
    )
    schemas = tuple(
        _tool_schema(name)
        for name in sorted(
            {
                str(node.metadata["tool_name"])
                for node in a.graph.nodes.values()
                if node.metadata.get("tool_name")
            }
        )
    )
    return ScenarioPrefix(
        prefix_id=prefix_id,
        scenario_family=family,
        variant_id=variant_id,
        payload_target_tokens=payload_tokens,
        reacquisition_mode=reacquisition_mode,
        graph=a.graph,
        messages=tuple(a.messages),
        tool_schemas=schemas,
        goal_context=goal_context,
        lifecycle_gold_by_event=dict(a.gold),
        oracle_removable_span_ids=tuple(removable_span_ids),
        forks=forks,
        base_seed=base_seed,
    )


def generate_trace_lifecycle_suite(
    archive_root: str | Path,
    *,
    base_seed: int = DEFAULT_BASE_SEED,
) -> tuple[ScenarioPrefix, ...]:
    """Generate the fixed 24-prefix/72-fork population in stable order."""

    archive = ArchiveStore(archive_root)
    return tuple(
        _build_prefix(
            family,
            variant_id,
            payload_tokens,
            reacquisition_mode,
            archive=archive,
            base_seed=base_seed,
        )
        for family in SCENARIO_FAMILIES
        for variant_id, payload_tokens, reacquisition_mode in VARIANTS
    )
