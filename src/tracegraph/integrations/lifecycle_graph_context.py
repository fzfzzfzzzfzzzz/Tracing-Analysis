"""Phase 5 runtime-neutral manager for LiveSubgraph and GDSC-Prune."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tracegraph.decision_query import DecisionQuery, build_decision_query
from tracegraph.graph import TraceGraph
from tracegraph.lifecycle_context import (
    ContextView,
    ProjectionStrategy,
    project_context,
)
from tracegraph.liveness import (
    ArchiveReader,
    DecisionLifecycleGraph,
    LivenessRoots,
    LiveSubgraph,
    analyze_liveness,
    build_state,
    derive_roots,
)
from tracegraph.policy_rules import PolicyRule, compile_policy_rule
from tracegraph.provider_cost import ProviderProtocol
from tracegraph.schema import NodeType


@dataclass(frozen=True, slots=True)
class LifecycleContextCompilation:
    state: DecisionLifecycleGraph
    query: DecisionQuery
    policy_rules: tuple[PolicyRule, ...]
    roots: LivenessRoots
    live_subgraph: LiveSubgraph
    context_view: ContextView

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "query": self.query.to_dict(),
            "policy_rules": [item.to_dict() for item in self.policy_rules],
            "roots": self.roots.to_dict(),
            "live_subgraph": self.live_subgraph.to_dict(),
            "context_view": self.context_view.to_dict(),
        }


class LifecycleGraphContextManager:
    """Compose the four frozen Phase 5 interfaces without hiding their outputs."""

    name = "lifecycle_graph_context"
    implementation_version = "lifecycle_graph_context_v1"
    context_policy_version = "gdsc_prune_v1"
    structured_policy_version = "gdsc_structured_v1"

    def __init__(
        self,
        *,
        model: str,
        hard_context_limit: int,
        soft_budget: int | None = None,
    ) -> None:
        self.model = str(model)
        self.hard_context_limit = int(hard_context_limit)
        self.strategy = ProjectionStrategy(soft_budget=soft_budget)

    @staticmethod
    def _policy_rules(event_graph: TraceGraph) -> tuple[PolicyRule, ...]:
        rules: list[PolicyRule] = []
        for node in event_graph.find_nodes(node_types={NodeType.CONSTRAINT}):
            value: str | Mapping[str, Any]
            value = (
                node.content
                if isinstance(node.content, (str, Mapping))
                else str(node.content)
            )
            rules.append(
                compile_policy_rule(value, source_event_ids=(node.node_id,))
            )
        return tuple(sorted(rules, key=lambda item: item.rule_id))

    def compile(
        self,
        event_graph: TraceGraph,
        *,
        messages: Sequence[Mapping[str, Any]],
        system_rules: Sequence[str],
        tool_schemas: Sequence[Mapping[str, Any]],
        cutoff: int | None = None,
        query: DecisionQuery | None = None,
        archive_reader: ArchiveReader | None = None,
    ) -> LifecycleContextCompilation:
        rules = self._policy_rules(event_graph)
        state = build_state(
            event_graph,
            cutoff,
            tool_schemas=tool_schemas,
            policy=rules,
        )
        active_query = query or build_decision_query(
            state.decision_state,
            tool_schemas=tool_schemas,
            policy_rules=rules,
        )
        roots = derive_roots(
            state,
            active_query,
            tool_schemas,
            rules,
        )
        live_subgraph = analyze_liveness(
            event_graph,
            state,
            roots,
            archive_reader=archive_reader,
        )
        protocol = ProviderProtocol(
            model=self.model,
            system_rules=tuple(system_rules),
            base_messages=tuple(dict(item) for item in messages),
            tools=tuple(dict(item) for item in tool_schemas),
            hard_context_limit=self.hard_context_limit,
        )
        context_view = project_context(
            event_graph,
            live_subgraph,
            self.strategy,
            protocol,
        )
        return LifecycleContextCompilation(
            state=state,
            query=active_query,
            policy_rules=rules,
            roots=roots,
            live_subgraph=live_subgraph,
            context_view=context_view,
        )
