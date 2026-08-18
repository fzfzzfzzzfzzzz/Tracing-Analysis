"""Runtime adapter from an EventGraph prefix to a GDSC PromptBundle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tracegraph.compiler import CompilerConfig, compile as compile_decision_state
from tracegraph.decision_query import DecisionQuery, build_decision_query
from tracegraph.decision_state import DecisionStateGraph
from tracegraph.graph import TraceGraph
from tracegraph.omission_risk import OmissionRiskModel
from tracegraph.policy_rules import PolicyRule, compile_policy_rule
from tracegraph.prompt_bundle import PromptBundle
from tracegraph.provider_cost import ProviderProtocol
from tracegraph.schema import NodeType
from tracegraph.state_reducer import reduce_event_graph


@dataclass(frozen=True, slots=True)
class GDSCCompilation:
    state: DecisionStateGraph
    query: DecisionQuery
    policy_rules: tuple[PolicyRule, ...]
    bundle: PromptBundle

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "query": self.query.to_dict(),
            "policy_rules": [rule.to_dict() for rule in self.policy_rules],
            "bundle": self.bundle.to_dict(),
        }


class GDSCManager:
    """Compile a deterministic GDSC-Core request without mutating EventGraph."""

    name = "decision_state_compiler"
    context_policy_version = "gdsc_core_v1"

    def __init__(
        self,
        *,
        model: str,
        hard_context_limit: int,
        compiler_config: CompilerConfig | None = None,
    ) -> None:
        self.model = model
        self.hard_context_limit = int(hard_context_limit)
        self.compiler_config = compiler_config or CompilerConfig()

    @staticmethod
    def _policy_rules(event_graph: TraceGraph) -> tuple[PolicyRule, ...]:
        rules: list[PolicyRule] = []
        for node in event_graph.find_nodes(node_types={NodeType.CONSTRAINT}):
            value: str | Mapping[str, Any]
            value = node.content if isinstance(node.content, (str, Mapping)) else str(node.content)
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
        budget: int | None,
        risk_model: OmissionRiskModel | None = None,
    ) -> GDSCCompilation:
        rules = self._policy_rules(event_graph)
        state = reduce_event_graph(
            event_graph,
            tool_schemas=tool_schemas,
            policy_rules=rules,
        )
        query = build_decision_query(
            state,
            tool_schemas=tool_schemas,
            policy_rules=rules,
        )
        protocol = ProviderProtocol(
            model=self.model,
            system_rules=tuple(system_rules),
            base_messages=tuple(dict(message) for message in messages),
            tools=tuple(dict(schema) for schema in tool_schemas),
            hard_context_limit=self.hard_context_limit,
        )
        bundle = compile_decision_state(
            event_graph,
            state,
            query,
            protocol,
            budget,
            risk_model,
            config=self.compiler_config,
        )
        return GDSCCompilation(state, query, rules, bundle)
