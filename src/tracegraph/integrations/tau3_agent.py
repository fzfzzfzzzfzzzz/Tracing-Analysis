"""Live τ³-bench half-duplex agent using TraceGraph context selection.

This module is imported only inside an upstream τ³-bench environment. It tracks
the current message prefix, builds the graph online, selects an active context,
and then calls the same upstream LLM utility used by the standard ``LLMAgent``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from tau2.agent.base_agent import ValidAgentInputMessage
from tau2.agent.llm_agent import AGENT_INSTRUCTION, LLMAgent, LLMAgentState
from tau2.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
)
from tau2.utils.llm_utils import generate

from tracegraph.adapters import TauTraceImporter
from tracegraph.archive import ArchiveStore
from tracegraph.capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens
from tracegraph.context import (
    ContextItem,
    ContextView,
    GraphLifecycleManager,
    build_context_managers,
)
from tracegraph.integrations.acon import (
    AconContextPlan,
    canonical_message_json,
    load_official_acon_adapter,
)
from tracegraph.message_protocol import project_context_items_to_messages
from tracegraph.schema import NodeType


class TraceGraphTauAgent(LLMAgent):
    """Drop-in τ³ agent whose only changed component is context management."""

    def __init__(self, tools, domain_policy, llm, llm_args=None):
        super().__init__(tools=tools, domain_policy=domain_policy, llm=llm, llm_args=llm_args)
        self.manager_name = os.environ.get("TRACEGRAPH_MANAGER", "full_ours")
        expected_token_accounting = os.environ.get("TRACEGRAPH_TOKEN_ACCOUNTING")
        if (
            expected_token_accounting
            and expected_token_accounting != TOKEN_ACCOUNTING_VERSION
        ):
            raise ValueError(
                "TRACEGRAPH_TOKEN_ACCOUNTING does not match runtime: "
                f"{expected_token_accounting!r} != {TOKEN_ACCOUNTING_VERSION!r}"
            )
        managers = build_context_managers(
            last_k=int(os.environ.get("TRACEGRAPH_LAST_K", "8"))
        )
        self.acon_adapter = None
        if self.manager_name in {
            "acon_official",
            "acon_official_with_failure_cards",
        }:
            project_root = Path(__file__).resolve().parents[3]
            config_path = Path(
                os.environ.get(
                    "TRACEGRAPH_ACON_CONFIG",
                    str(project_root / "configs" / "acon_tau3.json"),
                )
            )
            source_root = Path(
                os.environ.get(
                    "TRACEGRAPH_ACON_ROOT",
                    str(project_root / "vendor" / "acon-main"),
                )
            )
            self.acon_adapter = load_official_acon_adapter(
                config_path=config_path,
                source_root=source_root,
                compressor_model_override=os.environ.get(
                    "TRACEGRAPH_ACON_COMPRESSOR_MODEL"
                ),
            )
            self.context_manager = None
        elif self.manager_name not in managers:
            raise ValueError(
                f"unknown TRACEGRAPH_MANAGER={self.manager_name!r}; "
                "choices="
                f"{sorted([*managers, 'acon_official', 'acon_official_with_failure_cards'])}"
            )
        else:
            self.context_manager = managers[self.manager_name]
        budget_text = os.environ.get("TRACEGRAPH_BUDGET", "2048")
        self.context_budget = None if budget_text.lower() == "none" else int(budget_text)
        self.trace_session_id = uuid4().hex
        self.trace_root = Path(os.environ.get("TRACEGRAPH_OUTPUT_DIR", "outputs/tau3_live"))
        self.session_root = self.trace_root / self.trace_session_id
        self.archive = ArchiveStore(self.session_root / "archive")
        self.importer = TauTraceImporter(self.archive)

    @property
    def system_prompt(self) -> str:
        # The policy is represented as a graph Constraint so constraint-retention
        # ablations are real. The invariant agent instruction remains fixed.
        return f"<instructions>\n{AGENT_INSTRUCTION}\n</instructions>"

    @staticmethod
    def _dump_message(message: Message) -> dict:
        return message.model_dump(mode="json")

    def _select_messages(
        self,
        state: LLMAgentState,
        view: ContextView,
        graph,
    ) -> tuple[list[Message], list[str]]:
        dumped_messages = [self._dump_message(message) for message in state.messages]
        ordinals, compressed_fragments = project_context_items_to_messages(
            dumped_messages,
            view.items,
            graph.nodes,
        )
        selected = [
            message
            for ordinal, message in enumerate(state.messages, start=1)
            if ordinal in ordinals
        ]
        view.metadata["selected_message_ordinals"] = sorted(ordinals)
        view.metadata["selected_message_roles"] = [
            str(dumped_messages[ordinal - 1].get("role") or "")
            for ordinal in sorted(ordinals)
        ]
        view.metadata["graph_selected_representation_tokens"] = view.selected_tokens
        view.metadata["protocol_closed_message_tokens"] = sum(
            estimate_tokens(dumped_messages[ordinal - 1]) for ordinal in ordinals
        )
        return selected, compressed_fragments

    def _acon_view(self, graph, plan: AconContextPlan, messages: list[Message]) -> ContextView:
        nodes_by_ordinal: dict[int, list] = {}
        for node in graph.nodes.values():
            ordinal = node.metadata.get("source_message_ordinal")
            if isinstance(ordinal, int):
                nodes_by_ordinal.setdefault(ordinal, []).append(node)

        items: list[ContextItem] = []
        covered: set[str] = set()
        for node in graph.find_nodes(node_types={NodeType.CONSTRAINT}):
            items.append(ContextItem.from_node(node, "uncompressed_policy"))
            covered.add(node.node_id)
        for index in plan.included_indices:
            ordinal = index + 1
            source_nodes = nodes_by_ordinal.get(ordinal, [])
            source_ids = tuple(node.node_id for node in source_nodes)
            changed = index in plan.content_overrides
            content = json.loads(canonical_message_json(self._dump_message(messages[index])))
            if changed:
                content["content"] = plan.content_overrides[index]
            item_id = source_ids[0] if len(source_ids) == 1 else f"acon_message_{ordinal}"
            node_type = source_nodes[0].node_type if len(source_nodes) == 1 else NodeType.SUMMARY
            items.append(
                ContextItem(
                    node_id=item_id,
                    node_type=node_type,
                    content=content,
                    token_count=estimate_tokens(content),
                    reason="official_acon_runtime_context",
                    source_node_ids=source_ids,
                    preserves_sources=not changed,
                )
            )
            if not changed:
                covered.update(source_ids)

        original_tokens = sum(
            node.token_count or estimate_tokens(node.content) for node in graph.nodes.values()
        )
        metadata = plan.metadata()
        metadata.update(
            {
                "policy_compressed": False,
                "task_compressed": False,
                "budget_ignored": self.context_budget is not None,
                "token_accounting": TOKEN_ACCOUNTING_VERSION,
            }
        )
        return ContextView(
            manager=self.manager_name,
            items=items,
            original_tokens=original_tokens,
            budget=self.context_budget,
            excluded_node_ids=[
                node.node_id for node in graph.nodes.values() if node.node_id not in covered
            ],
            metadata=metadata,
        )

    def _persist(
        self,
        graph,
        view: ContextView,
        acon_plan: AconContextPlan | None = None,
    ) -> None:
        self.session_root.mkdir(parents=True, exist_ok=True)
        graph.save(self.session_root / "trace.json")
        with (self.session_root / "context_views.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(json.dumps(view.to_dict(), ensure_ascii=False) + "\n")
        if acon_plan is not None and acon_plan.call_records:
            with (self.session_root / "acon_calls.jsonl").open(
                "a", encoding="utf-8", newline="\n"
            ) as handle:
                for record in acon_plan.call_records:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def _generate_next_message(
        self,
        message: ValidAgentInputMessage,
        state: LLMAgentState,
    ) -> AssistantMessage:
        first_new_index = len(state.messages)
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)
        simulation = {
            "id": self.trace_session_id,
            "task_id": self.trace_session_id,
            "messages": [self._dump_message(item) for item in state.messages],
        }
        graph = self.importer.import_simulation(simulation, policy=self.domain_policy)
        graph.metadata["context_manager"] = self.manager_name
        graph.metadata["token_accounting"] = TOKEN_ACCOUNTING_VERSION
        acon_plan = None
        if self.acon_adapter is not None:
            dumped_messages = [self._dump_message(item) for item in state.messages]
            acon_plan = self.acon_adapter.prepare(
                dumped_messages,
                new_indices=range(first_new_index, len(state.messages)),
            )
            selected_messages = []
            for index in acon_plan.included_indices:
                selected = state.messages[index]
                if index in acon_plan.content_overrides:
                    selected = selected.model_copy(
                        update={"content": acon_plan.content_overrides[index]}
                    )
                selected_messages.append(selected)
            context_messages = list(state.system_messages)
            context_messages.append(
                SystemMessage(
                    role="system",
                    content=(
                        "<policy>\n"
                        + self.domain_policy
                        + "\n</policy>"
                    ),
                )
            )
            context_messages.extend(selected_messages)
            view = self._acon_view(graph, acon_plan, state.messages)
            if self.manager_name == "acon_official_with_failure_cards":
                card_view = GraphLifecycleManager().select(
                    graph,
                    budget=self.context_budget,
                )
                card_items = [
                    item
                    for item in card_view.items
                    if item.node_type == NodeType.SUMMARY
                    and item.reason.startswith("failure_card")
                ]
                if card_items:
                    context_messages.insert(
                        len(state.system_messages) + 1,
                        SystemMessage(
                            role="system",
                            content=(
                                "<active_trace_context>\n"
                                + "\n".join(
                                    json.dumps(
                                        item.content,
                                        ensure_ascii=False,
                                        default=str,
                                    )
                                    for item in card_items
                                )
                                + "\n</active_trace_context>"
                            ),
                        ),
                    )
                    view.items.extend(card_items)
                    covered_by_cards = {
                        node_id
                        for item in card_items
                        for node_id in item.source_node_ids
                    }
                    view.excluded_node_ids = [
                        node_id
                        for node_id in view.excluded_node_ids
                        if node_id not in covered_by_cards
                    ]
                view.metadata.update(
                    {
                        "failure_card_overlay": True,
                        "failure_card_budget_fraction": card_view.metadata.get(
                            "failure_card_budget_fraction"
                        ),
                        "failure_card_budget": card_view.metadata.get(
                            "failure_card_budget"
                        ),
                        "failure_card_count": len(card_items),
                        "failure_card_tokens": sum(
                            item.token_count for item in card_items
                        ),
                        "raw_failure_messages_selected_by_overlay": 0,
                    }
                )
            view.metadata["graph_selected_representation_tokens"] = view.selected_tokens
            view.metadata["protocol_closed_message_tokens"] = sum(
                estimate_tokens(self._dump_message(message))
                for message in selected_messages
            )
        else:
            view = self.context_manager.select(graph, budget=self.context_budget)
            view.metadata["token_accounting"] = TOKEN_ACCOUNTING_VERSION
            selected_messages, fragments = self._select_messages(state, view, graph)
            context_messages = list(state.system_messages)
            if fragments:
                context_messages.append(
                    SystemMessage(
                        role="system",
                        content=(
                            "<active_trace_context>\n"
                            + "\n".join(fragments)
                            + "\n</active_trace_context>"
                        ),
                    )
                )
            context_messages.extend(selected_messages)
        self._persist(graph, view, acon_plan)
        response = generate(
            model=self.llm,
            tools=self.tools,
            messages=context_messages,
            call_name=f"tracegraph_{self.manager_name}",
            **self.llm_args,
        )
        if acon_plan is not None:
            acon_metadata = acon_plan.metadata()
            compressor_cost = float(acon_metadata["compressor_cost_usd"])
            agent_cost = float(response.cost or 0.0)
            response.cost = agent_cost + compressor_cost
            raw_data = dict(response.raw_data or {})
            raw_data["tracegraph_context_management"] = {
                "manager": self.manager_name,
                "agent_generation_cost_usd": agent_cost,
                "compressor_cost_usd": compressor_cost,
                "total_turn_cost_usd": response.cost,
                "compressor_provider_input_tokens": acon_metadata[
                    "compressor_provider_input_tokens"
                ],
                "compressor_provider_output_tokens": acon_metadata[
                    "compressor_provider_output_tokens"
                ],
                "runtime_main_result_eligible": acon_plan.runtime_main_result_eligible,
            }
            response.raw_data = raw_data
        return response


def create_tracegraph_agent(tools, domain_policy, **kwargs):
    return TraceGraphTauAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=kwargs.get("llm"),
        llm_args=kwargs.get("llm_args"),
    )


def register_tau3_agent(name: str = "tracegraph_agent") -> None:
    from tau2.registry import registry

    registry.register_agent_factory(create_tracegraph_agent, name)
