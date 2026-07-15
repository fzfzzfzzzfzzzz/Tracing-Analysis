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
    ToolMessage,
)
from tau2.utils.llm_utils import generate

from tracegraph.adapters import TauTraceImporter
from tracegraph.archive import ArchiveStore
from tracegraph.context import ContextView, build_context_managers
from tracegraph.schema import NodeType


class TraceGraphTauAgent(LLMAgent):
    """Drop-in τ³ agent whose only changed component is context management."""

    def __init__(self, tools, domain_policy, llm, llm_args=None):
        super().__init__(tools=tools, domain_policy=domain_policy, llm=llm, llm_args=llm_args)
        manager_name = os.environ.get("TRACEGRAPH_MANAGER", "full_ours")
        managers = build_context_managers(
            last_k=int(os.environ.get("TRACEGRAPH_LAST_K", "8"))
        )
        if manager_name not in managers:
            raise ValueError(
                f"unknown TRACEGRAPH_MANAGER={manager_name!r}; choices={sorted(managers)}"
            )
        self.context_manager = managers[manager_name]
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

    @staticmethod
    def _tool_pair_closure(messages: list[Message], ordinals: set[int]) -> set[int]:
        call_to_ordinal: dict[str, int] = {}
        result_to_ordinal: dict[str, int] = {}
        for ordinal, message in enumerate(messages, start=1):
            tool_calls = getattr(message, "tool_calls", None) or []
            for call in tool_calls:
                if call.id:
                    call_to_ordinal[call.id] = ordinal
            if isinstance(message, ToolMessage):
                result_to_ordinal[message.id] = ordinal
        changed = True
        while changed:
            changed = False
            for call_id, call_ordinal in call_to_ordinal.items():
                result_ordinal = result_to_ordinal.get(call_id)
                if call_ordinal in ordinals and result_ordinal and result_ordinal not in ordinals:
                    ordinals.add(result_ordinal)
                    changed = True
                if result_ordinal in ordinals and call_ordinal not in ordinals:
                    ordinals.add(call_ordinal)
                    changed = True
        return ordinals

    def _select_messages(
        self,
        state: LLMAgentState,
        view: ContextView,
        graph,
    ) -> tuple[list[Message], list[str]]:
        ordinals: set[int] = {len(state.messages)}
        compressed_fragments: list[str] = []
        for item in view.items:
            node = graph.nodes.get(item.node_id)
            ordinal = node.metadata.get("source_message_ordinal") if node else None
            if item.node_type in {NodeType.SUMMARY, NodeType.ARCHIVE_HANDLE}:
                compressed_fragments.append(
                    json.dumps(item.content, ensure_ascii=False, default=str)
                )
            elif isinstance(ordinal, int):
                ordinals.add(ordinal)
            else:
                compressed_fragments.append(
                    json.dumps(item.content, ensure_ascii=False, default=str)
                )
        ordinals = self._tool_pair_closure(state.messages, ordinals)
        selected = [
            message
            for ordinal, message in enumerate(state.messages, start=1)
            if ordinal in ordinals
        ]
        return selected, compressed_fragments

    def _persist(self, graph, view: ContextView) -> None:
        self.session_root.mkdir(parents=True, exist_ok=True)
        graph.save(self.session_root / "trace.json")
        with (self.session_root / "context_views.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(json.dumps(view.to_dict(), ensure_ascii=False) + "\n")

    def _generate_next_message(
        self,
        message: ValidAgentInputMessage,
        state: LLMAgentState,
    ) -> AssistantMessage:
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
        view = self.context_manager.select(graph, budget=self.context_budget)
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
        self._persist(graph, view)
        return generate(
            model=self.llm,
            tools=self.tools,
            messages=context_messages,
            call_name=f"tracegraph_{self.context_manager.name}",
            **self.llm_args,
        )


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
