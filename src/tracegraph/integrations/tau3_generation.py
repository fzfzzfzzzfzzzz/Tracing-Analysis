"""Provider-message generation for the τ³ bridge."""

from __future__ import annotations

# ruff: noqa: F401, F811

import json
import os
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4
from tau2.agent.base_agent import ValidAgentInputMessage
from tau2.agent.llm_agent import AGENT_INSTRUCTION, LLMAgent, LLMAgentState
from tau2.data_model.message import AssistantMessage, Message, MultiToolMessage, SystemMessage, ToolMessage, UserMessage
from tau2.utils.llm_utils import DEFAULT_MAX_RETRIES, generate, to_litellm_messages
from tracegraph.adapters import TauTraceImporter
from tracegraph.archive import ArchiveStore
from tracegraph.capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens
from tracegraph.context_engine.context import ContextItem, ContextView, GraphLifecycleManager, build_context_managers
from tracegraph.integrations.acon import AconContextPlan, canonical_message_json, load_official_acon_adapter
from tracegraph.integrations.gdsc_manager import GDSCCompilation, GDSCManager
from tracegraph.message_protocol import project_context_items_to_messages
from tracegraph.provider_cost import canonical_request_json, provider_prompt_request, request_sha256
from tracegraph.schema import NodeType


def _merge_leading_system_messages(messages: list[Message]) -> tuple[list[Message], int]:
    """Merge leading system fragments for providers that allow one system turn.

    TraceGraph keeps the invariant instruction, domain policy, and compressed
    context as separate internal fragments.  Qwen's chat template accepts a
    system message only at index zero, so two consecutive leading system
    messages must be serialized as one without changing their order or text.
    """

    leading_count = 0
    for message in messages:
        if not isinstance(message, SystemMessage):
            break
        leading_count += 1
    if any(isinstance(message, SystemMessage) for message in messages[leading_count:]):
        raise ValueError("provider context contains a non-leading system message")
    if leading_count <= 1:
        return list(messages), leading_count
    merged = messages[0].model_copy(
        update={
            "content": "\n\n".join(
                str(message.content) for message in messages[:leading_count]
            )
        }
    )
    return [merged, *messages[leading_count:]], leading_count


def generate_next_message(
    self,
    message: ValidAgentInputMessage,
    state: LLMAgentState,
) -> AssistantMessage:
    first_new_index = len(state.messages)
    if isinstance(message, MultiToolMessage):
        state.messages.extend(message.tool_messages)
    else:
        state.messages.append(message)
    task_payload = self._task_payload()
    task_id = task_payload.get("id") or self.trace_session_id
    simulation = {
        "id": self.trace_session_id,
        "task_id": task_id,
        "messages": [self._dump_message(item) for item in state.messages],
    }
    graph = self.importer.import_simulation(
        simulation,
        task=task_payload,
        policy=self.domain_policy,
    )
    graph.metadata["context_manager"] = self.manager_name
    graph.metadata["token_accounting"] = TOKEN_ACCOUNTING_VERSION
    acon_plan = None
    gdsc_compilation = None
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
        elif self.manager_name == "acon_official_with_gdsc_state":
            gdsc_compilation = self.gdsc_manager.compile(
                graph,
                messages=dumped_messages,
                system_rules=(),
                tool_schemas=self._tool_schemas(),
                budget=self.context_budget,
            )
            if gdsc_compilation.bundle.hard_limit_exceeded:
                raise ValueError("GDSC provider hard context limit exceeded")
            excluded_types = {
                "active_goal",
                "open_subgoal",
                "global_policy_rule",
                "applicable_policy_rule",
            }
            overlay_atoms = [
                atom.to_dict()
                for atom in gdsc_compilation.state.atoms
                if atom.atom_type.value not in excluded_types
            ]
            if overlay_atoms:
                context_messages.insert(
                    len(state.system_messages) + 1,
                    SystemMessage(
                        role="system",
                        content=(
                            "<gdsc_verified_state>\n"
                            + json.dumps(
                                overlay_atoms,
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            )
                            + "\n</gdsc_verified_state>"
                        ),
                    ),
                )
            view.metadata.update(
                {
                    "gdsc_state_overlay": True,
                    "decision_state_hash": gdsc_compilation.state.state_hash,
                    "decision_query_hash": gdsc_compilation.query.query_hash,
                    "gdsc_overlay_atom_count": len(overlay_atoms),
                    "context_policy_version": "acon_official_plus_gdsc_core_v1",
                }
            )
        view.metadata["graph_selected_representation_tokens"] = view.selected_tokens
        view.metadata["protocol_closed_message_tokens"] = sum(
            estimate_tokens(self._dump_message(message))
            for message in selected_messages
        )
    elif self.gdsc_manager is not None:
        provider_messages = to_litellm_messages(list(state.messages))
        system_rules = [
            str(self._dump_message(item).get("content") or "")
            for item in state.system_messages
        ]
        gdsc_compilation = self.gdsc_manager.compile(
            graph,
            messages=provider_messages,
            system_rules=system_rules,
            tool_schemas=self._tool_schemas(),
            budget=self.context_budget,
        )
        if gdsc_compilation.bundle.hard_limit_exceeded:
            raise ValueError("GDSC provider hard context limit exceeded")
        context_messages = [
            self._load_message(dict(payload))
            for payload in gdsc_compilation.bundle.messages
        ]
        view = self._gdsc_view(graph, gdsc_compilation)
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
    context_messages, leading_system_message_count = _merge_leading_system_messages(
        context_messages
    )
    view.metadata.update(
        {
            "provider_leading_system_message_count": leading_system_message_count,
            "provider_system_messages_merged": leading_system_message_count > 1,
        }
    )
    if gdsc_compilation is not None:
        self._persist_gdsc_compilation(gdsc_compilation)
    expected_prompt_hash = (
        gdsc_compilation.bundle.request_hash
        if self.manager_name == "decision_state_compiler"
        and gdsc_compilation is not None
        else None
    )
    request_sha256 = self._persist_provider_request(
        context_messages,
        view,
        expected_prompt_hash=expected_prompt_hash,
    )
    self._persist(graph, view, acon_plan)
    responses = []
    response = None
    for attempt_index in range(2):
        call_args = dict(self.llm_args or {})
        if attempt_index:
            original_seed = call_args.get("seed")
            if isinstance(original_seed, int):
                call_args["seed"] = original_seed + attempt_index
            call_args["temperature"] = max(
                0.2,
                float(call_args.get("temperature") or 0.0),
            )
        response = generate(
            model=self.llm,
            tools=self.tools,
            messages=context_messages,
            call_name=(
                f"tracegraph_{self.manager_name}"
                if not attempt_index
                else f"tracegraph_{self.manager_name}_empty_retry_{attempt_index}"
            ),
            **call_args,
        )
        responses.append(response)
        if response.has_content() or response.is_tool_call():
            break
    assert response is not None
    retry_metadata = {
        "attempt_count": len(responses),
        "empty_response_retries": len(responses) - 1,
        "retry_cost_included": True,
        "retry_usage_included": True,
        "attempts": [
            {
                "content_empty": not item.has_content(),
                "has_tool_call": item.is_tool_call(),
                "usage": dict(item.usage or {}),
                "cost_usd": float(item.cost or 0.0),
            }
            for item in responses
        ],
    }
    raw_data = dict(response.raw_data or {})
    raw_data["tracegraph_agent_empty_response_retry"] = retry_metadata
    response = response.model_copy(
        update={
            "cost": sum(float(item.cost or 0.0) for item in responses),
            "usage": {
                key: sum(int((item.usage or {}).get(key, 0)) for item in responses)
                for key in ("prompt_tokens", "completion_tokens")
            },
            "raw_data": raw_data,
        }
    )
    self._persist_provider_usage(request_sha256, response)
    if not (response.has_content() or response.is_tool_call()):
        raise ValueError("agent returned an empty response after 2 attempts")
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
