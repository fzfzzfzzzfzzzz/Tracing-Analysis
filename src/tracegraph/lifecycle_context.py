"""Deletion-only provider projection from a shared Phase 5 LiveSubgraph."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .capture import estimate_tokens
from .decision_state import stable_digest
from .graph import TraceGraph
from .liveness import EventSpan, LiveSubgraph
from .provider_cost import (
    PromptCost,
    ProviderProtocol,
    close_protocol_messages,
    provider_prompt_request,
    request_sha256,
    serialized_request_cost,
)


@dataclass(frozen=True, slots=True)
class ProjectionStrategy:
    name: str = "gdsc_prune"
    policy_version: str = "gdsc_prune_v1"
    soft_budget: int | None = None

    def __post_init__(self) -> None:
        if self.name != "gdsc_prune":
            raise ValueError("only deletion-only gdsc_prune is available before F5-G2")
        if self.policy_version != "gdsc_prune_v1":
            raise ValueError("unsupported Phase 5 prune policy version")
        if self.soft_budget is not None and self.soft_budget <= 0:
            raise ValueError("soft budget must be positive or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "policy_version": self.policy_version,
            "soft_budget": self.soft_budget,
        }


@dataclass(frozen=True, slots=True)
class ContextView:
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...]
    live_node_ids: tuple[str, ...]
    evicted_node_ids: tuple[str, ...]
    live_span_ids: tuple[str, ...]
    evicted_span_ids: tuple[str, ...]
    root_provenance: tuple[dict[str, Any], ...]
    closure_provenance: tuple[dict[str, Any], ...]
    lifecycle_reasons: tuple[dict[str, Any], ...]
    projection_strategy: dict[str, Any]
    request_hash: str
    costs: PromptCost
    provider_protocol: dict[str, Any]
    raw_message_ordinals: tuple[int, ...]
    fallback_records: tuple[dict[str, Any], ...] = ()
    uncertainty_records: tuple[dict[str, Any], ...] = ()
    protocol_valid: bool = True
    protocol_errors: tuple[str, ...] = ()
    matched_budget_eligible: bool = True
    budget_infeasible: bool = False
    hard_limit_exceeded: bool = False
    send_eligible: bool = True
    schema_version: str = "lifecycle_context_view_v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(dict(item) for item in self.messages))
        object.__setattr__(self, "tools", tuple(dict(item) for item in self.tools))
        for field in (
            "live_node_ids",
            "evicted_node_ids",
            "live_span_ids",
            "evicted_span_ids",
            "raw_message_ordinals",
            "protocol_errors",
        ):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))
        if set(self.live_node_ids).intersection(self.evicted_node_ids):
            raise ValueError("live and evicted node sets overlap")
        if set(self.live_span_ids).intersection(self.evicted_span_ids):
            raise ValueError("live and evicted span sets overlap")
        if self.request_hash != request_sha256(self.request):
            raise ValueError("ContextView request hash does not match final request")
        if self.protocol_valid != (not self.protocol_errors):
            raise ValueError("protocol validity and errors disagree")
        if self.send_eligible and (
            not self.protocol_valid or self.hard_limit_exceeded
        ):
            raise ValueError("invalid or over-hard-limit view cannot be send eligible")
        if self.budget_infeasible and self.matched_budget_eligible:
            raise ValueError("over-soft-budget view cannot be matched-budget eligible")

    @property
    def request(self) -> dict[str, Any]:
        return provider_prompt_request(
            model=str(self.provider_protocol["model"]),
            messages=self.messages,
            tools=self.tools,
        )

    @property
    def context_view_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def assert_sent_request(self, request: Mapping[str, Any]) -> None:
        sent_hash = request_sha256(request)
        if sent_hash != self.request_hash:
            raise ValueError(
                "sent provider request does not match frozen ContextView request hash"
            )

    def with_provider_actual(
        self,
        *,
        request_hash: str,
        input_tokens: int,
        cost_usd: float | None = None,
    ) -> "ContextView":
        if request_hash != self.request_hash:
            raise ValueError("provider usage request hash does not match ContextView")
        return replace(
            self,
            costs=self.costs.with_actual(input_tokens, cost_usd),
        )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "messages": [dict(item) for item in self.messages],
            "tools": [dict(item) for item in self.tools],
            "live_node_ids": list(self.live_node_ids),
            "evicted_node_ids": list(self.evicted_node_ids),
            "live_span_ids": list(self.live_span_ids),
            "evicted_span_ids": list(self.evicted_span_ids),
            "root_provenance": [dict(item) for item in self.root_provenance],
            "closure_provenance": [dict(item) for item in self.closure_provenance],
            "lifecycle_reasons": [dict(item) for item in self.lifecycle_reasons],
            "projection_strategy": dict(self.projection_strategy),
            "request_hash": self.request_hash,
            "costs": self.costs.to_dict(),
            "provider_protocol": dict(self.provider_protocol),
            "raw_message_ordinals": list(self.raw_message_ordinals),
            "fallback_records": [dict(item) for item in self.fallback_records],
            "uncertainty_records": [dict(item) for item in self.uncertainty_records],
            "protocol_valid": self.protocol_valid,
            "protocol_errors": list(self.protocol_errors),
            "matched_budget_eligible": self.matched_budget_eligible,
            "budget_infeasible": self.budget_infeasible,
            "hard_limit_exceeded": self.hard_limit_exceeded,
            "send_eligible": self.send_eligible,
        }
        if include_hash:
            result["context_view_hash"] = self.context_view_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextView":
        value = cls(
            messages=tuple(dict(item) for item in data.get("messages", ())),
            tools=tuple(dict(item) for item in data.get("tools", ())),
            live_node_ids=tuple(map(str, data.get("live_node_ids", ()))),
            evicted_node_ids=tuple(
                map(str, data.get("evicted_node_ids", ()))
            ),
            live_span_ids=tuple(map(str, data.get("live_span_ids", ()))),
            evicted_span_ids=tuple(
                map(str, data.get("evicted_span_ids", ()))
            ),
            root_provenance=tuple(
                dict(item) for item in data.get("root_provenance", ())
            ),
            closure_provenance=tuple(
                dict(item) for item in data.get("closure_provenance", ())
            ),
            lifecycle_reasons=tuple(
                dict(item) for item in data.get("lifecycle_reasons", ())
            ),
            projection_strategy=dict(data["projection_strategy"]),
            request_hash=str(data["request_hash"]),
            costs=PromptCost.from_dict(data["costs"]),
            provider_protocol=dict(data["provider_protocol"]),
            raw_message_ordinals=tuple(
                map(int, data.get("raw_message_ordinals", ()))
            ),
            fallback_records=tuple(
                dict(item) for item in data.get("fallback_records", ())
            ),
            uncertainty_records=tuple(
                dict(item) for item in data.get("uncertainty_records", ())
            ),
            protocol_valid=bool(data.get("protocol_valid", True)),
            protocol_errors=tuple(
                map(str, data.get("protocol_errors", ()))
            ),
            matched_budget_eligible=bool(
                data.get("matched_budget_eligible", True)
            ),
            budget_infeasible=bool(data.get("budget_infeasible", False)),
            hard_limit_exceeded=bool(
                data.get("hard_limit_exceeded", False)
            ),
            send_eligible=bool(data.get("send_eligible", True)),
            schema_version=str(
                data.get("schema_version", "lifecycle_context_view_v1")
            ),
        )
        declared = data.get("context_view_hash")
        if declared is not None and declared != value.context_view_hash:
            raise ValueError("ContextView hash mismatch")
        return value


def _system_messages(protocol: ProviderProtocol) -> list[dict[str, Any]]:
    if not protocol.system_rules:
        return []
    return [{"role": "system", "content": "\n\n".join(protocol.system_rules)}]


def _role(message: Mapping[str, Any]) -> str:
    return str(message.get("role") or "").lower()


def _tool_call_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    calls = message.get("tool_calls") or message.get("function_calls") or ()
    if isinstance(calls, Mapping):
        calls = (calls,)
    return tuple(
        str(call["id"])
        for call in calls
        if isinstance(call, Mapping) and call.get("id")
    )


def _tool_result_id(message: Mapping[str, Any]) -> str | None:
    if _role(message) != "tool":
        return None
    value = message.get("tool_call_id") or message.get("id")
    return str(value) if value else None


def _strict_protocol_errors(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    calls: dict[str, list[int]] = defaultdict(list)
    results: dict[str, list[int]] = defaultdict(list)
    for ordinal, message in enumerate(messages, start=1):
        for call_id in _tool_call_ids(message):
            calls[call_id].append(ordinal)
        result_id = _tool_result_id(message)
        if result_id:
            results[result_id].append(ordinal)

    errors: list[str] = []
    for call_id, ordinals in sorted(calls.items()):
        if len(ordinals) != 1:
            errors.append(f"duplicate tool call id {call_id!r}")
            continue
        result_ordinals = results.get(call_id, [])
        if len(result_ordinals) != 1:
            errors.append(
                f"tool call {call_id!r} requires exactly one result; "
                f"found {len(result_ordinals)}"
            )
            continue
        call_ordinal = ordinals[0]
        result_ordinal = result_ordinals[0]
        if result_ordinal <= call_ordinal:
            errors.append(f"tool result {call_id!r} appears before its call")
            continue
        intervening = [
            _role(message)
            for message in messages[call_ordinal:result_ordinal - 1]
            if _role(message) != "tool"
        ]
        if intervening:
            errors.append(
                f"non-tool message appears before result for call {call_id!r}"
            )
    for result_id, ordinals in sorted(results.items()):
        if result_id not in calls:
            errors.append(f"tool result {result_id!r} has no matching call")
        if len(ordinals) > 1:
            errors.append(f"duplicate tool results for call {result_id!r}")
    return tuple(sorted(set(errors)))


def _message_has_non_tool_content(message: Mapping[str, Any]) -> bool:
    if _role(message) != "assistant" or not _tool_call_ids(message):
        return False
    content = message.get("content")
    return content not in (None, "", [])


def _coerce_strategy(
    strategy: str | ProjectionStrategy,
) -> ProjectionStrategy:
    if isinstance(strategy, ProjectionStrategy):
        return strategy
    if strategy in {"gdsc_structured", "gdsc_structured_v1"}:
        raise PermissionError("GDSC-Structured is gated until F5-G2 passes")
    if strategy not in {"gdsc_prune", "gdsc_prune_v1"}:
        raise ValueError(f"unsupported lifecycle context strategy: {strategy!r}")
    return ProjectionStrategy()


def _span_ordinals(
    spans: Mapping[str, EventSpan],
    span_ids: set[str],
) -> set[int]:
    return {
        ordinal
        for span_id in span_ids
        for ordinal in spans[span_id].message_ordinals
    }


def project_context(
    event_graph: TraceGraph,
    live_subgraph: LiveSubgraph,
    strategy: str | ProjectionStrategy,
    provider_protocol: ProviderProtocol,
) -> ContextView:
    """Project GDSC-Prune without rewriting any retained raw message."""

    selected_strategy = _coerce_strategy(strategy)
    spans = live_subgraph.span_map()
    effective_evicted = set(live_subgraph.evicted_span_ids)
    effective_live = set(live_subgraph.live_span_ids)
    fallbacks: list[dict[str, Any]] = []
    message_count = len(provider_protocol.base_messages)

    for span_id in sorted(tuple(effective_evicted)):
        span = spans[span_id]
        invalid_ordinals = [
            ordinal
            for ordinal in span.message_ordinals
            if not 1 <= ordinal <= message_count
        ]
        mixed_content = any(
            _message_has_non_tool_content(
                provider_protocol.base_messages[ordinal - 1]
            )
            for ordinal in span.message_ordinals
            if 1 <= ordinal <= message_count
        )
        if invalid_ordinals or mixed_content:
            effective_evicted.remove(span_id)
            effective_live.add(span_id)
            fallbacks.append(
                {
                    "span_id": span_id,
                    "reason": (
                        "message_ordinal_out_of_range"
                        if invalid_ordinals
                        else "assistant_tool_message_contains_non_tool_content"
                    ),
                    "action": "restore_raw_span",
                }
            )

    while True:
        dead_ordinals = _span_ordinals(spans, effective_evicted)
        selected_ordinals = {
            ordinal
            for ordinal in range(1, message_count + 1)
            if ordinal not in dead_ordinals
        }
        closure = close_protocol_messages(
            provider_protocol.base_messages,
            selected_ordinals,
        )
        restored = {
            span_id
            for span_id in effective_evicted
            if set(spans[span_id].message_ordinals).intersection(closure.ordinals)
        }
        if not restored:
            break
        for span_id in sorted(restored):
            effective_evicted.remove(span_id)
            effective_live.add(span_id)
            fallbacks.append(
                {
                    "span_id": span_id,
                    "reason": "provider_protocol_closure_requires_span",
                    "action": "restore_raw_span",
                }
            )

    selected_raw = [
        dict(provider_protocol.base_messages[ordinal - 1])
        for ordinal in sorted(selected_ordinals)
    ]
    closed_raw = [
        dict(provider_protocol.base_messages[ordinal - 1])
        for ordinal in closure.ordinals
    ]
    system_messages = _system_messages(provider_protocol)
    preclosure_messages = tuple(system_messages + selected_raw)
    final_messages = tuple(system_messages + closed_raw)
    strict_errors = _strict_protocol_errors(final_messages)
    protocol_errors = tuple(sorted(set((*closure.errors, *strict_errors))))
    protocol_valid = not protocol_errors

    restored_nodes = {
        node_id
        for span_id in effective_live
        for node_id in spans[span_id].node_ids
    }
    effective_evicted_nodes = {
        node_id
        for span_id in effective_evicted
        for node_id in spans[span_id].node_ids
    }
    visible_nodes = {
        node_id
        for span in live_subgraph.spans
        for node_id in span.node_ids
    }
    effective_live_nodes = visible_nodes.difference(effective_evicted_nodes)
    effective_live_nodes.update(restored_nodes)

    graph_selected = sum(
        event_graph.nodes[node_id].token_count
        or estimate_tokens(event_graph.nodes[node_id].content)
        for node_id in effective_live_nodes
        if node_id in event_graph.nodes
    )
    projected_cost = provider_protocol.count(preclosure_messages)
    protocol_closed_cost = provider_protocol.count(final_messages)
    serialized_cost = serialized_request_cost(provider_protocol, final_messages)
    costs = PromptCost(
        graph_selected=graph_selected,
        compiled=projected_cost,
        protocol_closed=protocol_closed_cost,
        serialized_request=serialized_cost,
    )
    budget_infeasible = bool(
        selected_strategy.soft_budget is not None
        and serialized_cost > selected_strategy.soft_budget
    )
    hard_limit_exceeded = serialized_cost > provider_protocol.hard_context_limit
    matched_budget_eligible = not budget_infeasible and not hard_limit_exceeded
    request = provider_protocol.request(final_messages)
    closure_provenance = tuple(
        [
            *live_subgraph.closure_provenance,
            *(record.to_dict() for record in closure.records),
        ]
    )
    uncertainty = tuple(
        [
            *live_subgraph.uncertainty_records,
            *(
                {
                    "reason": error,
                    "action": "send_ineligible",
                }
                for error in protocol_errors
            ),
        ]
    )
    return ContextView(
        messages=final_messages,
        tools=provider_protocol.tools,
        live_node_ids=tuple(effective_live_nodes),
        evicted_node_ids=tuple(effective_evicted_nodes),
        live_span_ids=tuple(effective_live),
        evicted_span_ids=tuple(effective_evicted),
        root_provenance=live_subgraph.root_provenance,
        closure_provenance=closure_provenance,
        lifecycle_reasons=live_subgraph.lifecycle_reasons,
        projection_strategy=selected_strategy.to_dict(),
        request_hash=request_sha256(request),
        costs=costs,
        provider_protocol=provider_protocol.manifest(),
        raw_message_ordinals=closure.ordinals,
        fallback_records=tuple(fallbacks),
        uncertainty_records=uncertainty,
        protocol_valid=protocol_valid,
        protocol_errors=protocol_errors,
        matched_budget_eligible=matched_budget_eligible,
        budget_infeasible=budget_infeasible,
        hard_limit_exceeded=hard_limit_exceeded,
        send_eligible=protocol_valid and not hard_limit_exceeded,
    )
