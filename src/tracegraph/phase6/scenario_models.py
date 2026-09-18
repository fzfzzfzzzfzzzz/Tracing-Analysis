"""Definitions moved from ``tracegraph.phase6_scenarios``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from ..archive import ArchiveStore
from ..capture import estimate_tokens
from ..decision_state import stable_digest
from ..goal_lifecycle import GoalContext, GoalLifecycleState
from ..graph import TraceGraph
from ..schema import Edge, EdgeType, Node, NodeType

from .scenario_constants import (
    FORK_TYPES as FORK_TYPES,
    SCENARIO_SCHEMA_VERSION as SCENARIO_SCHEMA_VERSION,
    _CREATED_AT as _CREATED_AT,
)



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
