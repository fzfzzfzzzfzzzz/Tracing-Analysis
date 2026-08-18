"""Live τ³-bench half-duplex agent using TraceGraph context selection.

This module is imported only inside an upstream τ³-bench environment. It tracks
the current message prefix, builds the graph online, selects an active context,
and then calls the same upstream LLM utility used by the standard ``LLMAgent``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
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
    UserMessage,
)
from tau2.utils.llm_utils import DEFAULT_MAX_RETRIES, generate, to_litellm_messages

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
from tracegraph.integrations.gdsc_manager import GDSCCompilation, GDSCManager
from tracegraph.message_protocol import project_context_items_to_messages
from tracegraph.provider_cost import (
    canonical_request_json,
    provider_prompt_request,
    request_sha256,
)
from tracegraph.schema import NodeType


class TraceGraphTauAgent(LLMAgent):
    """Drop-in τ³ agent whose only changed component is context management."""

    def __init__(self, tools, domain_policy, llm, llm_args=None, task=None):
        super().__init__(tools=tools, domain_policy=domain_policy, llm=llm, llm_args=llm_args)
        self.task = task
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
        self.gdsc_manager = None
        if self.manager_name in {
            "acon_official",
            "acon_official_with_failure_cards",
            "acon_official_with_gdsc_state",
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
            if self.manager_name == "acon_official_with_gdsc_state":
                self.gdsc_manager = GDSCManager(
                    model=self.llm,
                    hard_context_limit=int(
                        os.environ.get("TRACEGRAPH_PROVIDER_CONTEXT_LIMIT", "200000")
                    ),
                )
        elif self.manager_name == "decision_state_compiler":
            self.context_manager = None
            self.gdsc_manager = GDSCManager(
                model=self.llm,
                hard_context_limit=int(
                    os.environ.get("TRACEGRAPH_PROVIDER_CONTEXT_LIMIT", "200000")
                ),
            )
        elif self.manager_name not in managers:
            raise ValueError(
                f"unknown TRACEGRAPH_MANAGER={self.manager_name!r}; "
                "choices="
                f"{sorted([*managers, 'acon_official', 'acon_official_with_failure_cards', 'acon_official_with_gdsc_state', 'decision_state_compiler'])}"
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

    def _task_payload(self) -> dict:
        if self.task is None:
            return {}
        dumper = getattr(self.task, "model_dump", None)
        if callable(dumper):
            value = dumper(mode="json")
            return value if isinstance(value, dict) else {}
        return dict(self.task) if isinstance(self.task, dict) else {}

    def _tool_schemas(self) -> list[dict]:
        schemas: list[dict] = []
        for tool in self.tools:
            value = getattr(tool, "openai_schema", None)
            if callable(value):
                value = value()
            if isinstance(value, dict):
                schemas.append(value)
        return schemas

    @property
    def system_prompt(self) -> str:
        # The policy is represented as a graph Constraint so constraint-retention
        # ablations are real. The invariant agent instruction remains fixed.
        return f"<instructions>\n{AGENT_INSTRUCTION}\n</instructions>"

    @staticmethod
    def _dump_message(message: Message) -> dict:
        return message.model_dump(mode="json")

    @staticmethod
    def _load_message(payload: dict) -> Message:
        role = str(payload.get("role") or "").lower()
        message_types = {
            "system": SystemMessage,
            "user": UserMessage,
            "assistant": AssistantMessage,
            "tool": ToolMessage,
        }
        message_type = message_types.get(role)
        if message_type is None:
            raise ValueError(f"unsupported compiled message role: {role!r}")
        if role == "assistant":
            tool_calls: list[dict] = []
            for raw_call in payload.get("tool_calls") or ():
                if not isinstance(raw_call, Mapping):
                    raise ValueError("compiled assistant tool call must be a mapping")
                function = raw_call.get("function")
                function = function if isinstance(function, Mapping) else {}
                name = function.get("name") or raw_call.get("name")
                arguments = function.get("arguments", raw_call.get("arguments", {}))
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            "compiled assistant tool arguments must be valid JSON"
                        ) from exc
                if not isinstance(arguments, Mapping):
                    raise ValueError("compiled assistant tool arguments must be an object")
                tool_calls.append(
                    {
                        "id": str(raw_call.get("id") or ""),
                        "name": str(name or ""),
                        "arguments": dict(arguments),
                    }
                )
            payload = {
                "role": "assistant",
                "content": payload.get("content"),
                "tool_calls": tool_calls or None,
            }
        elif role == "tool":
            payload = {
                "role": "tool",
                "id": str(payload.get("tool_call_id") or payload.get("id") or ""),
                "content": payload.get("content"),
            }
        else:
            payload = {"role": role, "content": payload.get("content")}
        return message_type.model_validate(payload)

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

    def _gdsc_view(self, graph, compilation: GDSCCompilation) -> ContextView:
        items: list[ContextItem] = []
        covered: set[str] = set()
        for representation in compilation.bundle.representation_manifest:
            if representation.get("representation_type") == "omit":
                continue
            source_ids = tuple(map(str, representation.get("source_ids") or ()))
            covered.update(source_ids)
            source = next(
                (graph.nodes[node_id] for node_id in source_ids if node_id in graph.nodes),
                None,
            )
            items.append(
                ContextItem(
                    node_id=(
                        source.node_id
                        if source is not None
                        else str(representation["representation_id"])
                    ),
                    node_type=source.node_type if source is not None else NodeType.SUMMARY,
                    content=representation.get("payload"),
                    token_count=int(representation.get("estimated_cost") or 0),
                    reason=(
                        "gdsc_representation:"
                        + str(representation.get("representation_type") or "unknown")
                    ),
                    source_node_ids=source_ids,
                    raw_ref=source.raw_ref if source is not None else None,
                    preserves_sources=True,
                )
            )
        costs = compilation.bundle.costs
        return ContextView(
            manager=self.manager_name,
            items=items,
            original_tokens=sum(
                node.token_count or estimate_tokens(node.content)
                for node in graph.nodes.values()
            ),
            budget=self.context_budget,
            excluded_node_ids=[node_id for node_id in graph.nodes if node_id not in covered],
            metadata={
                "context_policy_version": "gdsc_core_v1",
                "decision_state_hash": compilation.state.state_hash,
                "decision_query_hash": compilation.query.query_hash,
                "graph_selected_representation_tokens": costs.graph_selected,
                "compiled_representation_tokens": costs.compiled,
                "protocol_closed_message_tokens": costs.protocol_closed,
                "serialized_request_estimated_tokens": costs.serialized_request,
                "matched_budget_eligible": compilation.bundle.matched_budget_eligible,
                "budget_infeasible": compilation.bundle.budget_infeasible,
                "hard_limit_exceeded": compilation.bundle.hard_limit_exceeded,
                "compiler_version": compilation.bundle.compiler_version,
            },
        )

    def _persist_gdsc_compilation(self, compilation: GDSCCompilation) -> None:
        self.session_root.mkdir(parents=True, exist_ok=True)
        with (self.session_root / "gdsc_compilations.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(
                json.dumps(compilation.to_dict(), ensure_ascii=False, default=str)
                + "\n"
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

    def _persist_provider_request(
        self,
        context_messages: list[Message],
        view: ContextView,
        *,
        expected_prompt_hash: str | None = None,
    ) -> str:
        messages = to_litellm_messages(context_messages)
        tools = self._tool_schemas()
        request_kwargs = dict(self.llm_args or {})
        request_kwargs.setdefault("num_retries", DEFAULT_MAX_RETRIES)
        prompt_request = provider_prompt_request(
            model=self.llm,
            messages=messages,
            tools=tools,
        )
        invocation_request = {
            **prompt_request,
            "tool_choice": "auto" if tools else None,
            **request_kwargs,
        }
        prompt_serialized = canonical_request_json(prompt_request)
        invocation_serialized = canonical_request_json(invocation_request)
        prompt_sha256 = request_sha256(prompt_request)
        invocation_sha256 = request_sha256(invocation_request)
        if expected_prompt_hash is not None and prompt_sha256 != expected_prompt_hash:
            raise ValueError(
                "GDSC PromptBundle hash does not match the exact LiteLLM prompt object"
            )
        serialized_tokens = estimate_tokens(prompt_serialized)
        graph_selected_tokens = int(
            view.metadata.get("graph_selected_representation_tokens", view.selected_tokens)
        )
        compiled_tokens = int(
            view.metadata.get("compiled_representation_tokens", graph_selected_tokens)
        )
        protocol_closed_tokens = int(
            view.metadata.get("protocol_closed_message_tokens", compiled_tokens)
        )
        view.metadata.update(
            {
                "serialized_request_sha256": prompt_sha256,
                "prompt_request_sha256": prompt_sha256,
                "invocation_request_sha256": invocation_sha256,
                "serialized_request_estimated_tokens": serialized_tokens,
                "invocation_request_estimated_tokens": estimate_tokens(
                    invocation_serialized
                ),
                "tool_schema_estimated_tokens": estimate_tokens(tools),
                "serializer_version": "tau3_litellm_prompt_v2",
                "prompt_cost_layers": {
                    "graph_selected": graph_selected_tokens,
                    "compiled": compiled_tokens,
                    "protocol_closed": protocol_closed_tokens,
                    "serialized_request": serialized_tokens,
                    "provider_actual": None,
                },
            }
        )
        self.session_root.mkdir(parents=True, exist_ok=True)
        with (self.session_root / "provider_requests.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(
                json.dumps(
                    {
                        "request_sha256": prompt_sha256,
                        "prompt_request_sha256": prompt_sha256,
                        "invocation_request_sha256": invocation_sha256,
                        "request": prompt_request,
                        "prompt_request": prompt_request,
                        "invocation_request": invocation_request,
                        "manager": self.manager_name,
                        "serializer_version": "tau3_litellm_prompt_v2",
                        "estimated_tokens": serialized_tokens,
                        "cost_layers": view.metadata["prompt_cost_layers"],
                    },
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )
        return prompt_sha256

    def _persist_provider_usage(self, request_sha256: str, response) -> None:
        usage = getattr(response, "usage", None) or {}
        actual_input_tokens = next(
            (
                int(usage[key])
                for key in ("prompt_tokens", "input_tokens", "input_token_count")
                if usage.get(key) is not None
            ),
            None,
        )
        payload = {
            "request_sha256": request_sha256,
            "provider_usage": usage,
            "provider_actual_input_tokens": actual_input_tokens,
            "provider_cost_usd": float(getattr(response, "cost", None) or 0.0),
            "provider_usage_present": bool(usage),
        }
        with (self.session_root / "provider_usage.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

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
        response = generate(
            model=self.llm,
            tools=self.tools,
            messages=context_messages,
            call_name=f"tracegraph_{self.manager_name}",
            **self.llm_args,
        )
        self._persist_provider_usage(request_sha256, response)
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
        task=kwargs.get("task"),
    )


def register_tau3_agent(name: str = "tracegraph_agent") -> None:
    from tau2.registry import registry

    registry.register_agent_factory(create_tracegraph_agent, name)
