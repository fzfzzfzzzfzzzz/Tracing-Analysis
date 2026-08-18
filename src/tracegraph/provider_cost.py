"""Provider-protocol closure and final serialized prompt accounting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .capture import estimate_tokens


TokenCounter = Callable[[Any], int]


def canonical_request_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def request_sha256(request: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_request_json(request).encode("utf-8")).hexdigest()


def provider_prompt_request(
    *,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the exact provider prompt object used for token accounting.

    Generation controls such as retries and ``tool_choice`` belong to the
    invocation envelope.  They are deliberately excluded from this object so
    prompt hashes and provider input-token accounting share one stable scope.
    """

    request: dict[str, Any] = {
        "model": str(model),
        "messages": [dict(message) for message in messages],
    }
    if tools:
        request["tools"] = [dict(tool) for tool in tools]
    return request


def _role(message: Mapping[str, Any]) -> str:
    return str(message.get("role") or "").lower()


def _call_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    calls = message.get("tool_calls") or message.get("function_calls") or ()
    if isinstance(calls, Mapping):
        calls = (calls,)
    result: list[str] = []
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        call_id = call.get("id")
        if call_id:
            result.append(str(call_id))
    return tuple(result)


def _result_id(message: Mapping[str, Any]) -> str | None:
    if _role(message) != "tool":
        return None
    call_id = message.get("tool_call_id") or message.get("id")
    return str(call_id) if call_id else None


@dataclass(frozen=True, slots=True)
class ClosureRecord:
    added_ordinal: int
    reason: str
    trigger_ordinal: int
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "added_ordinal": self.added_ordinal,
            "reason": self.reason,
            "trigger_ordinal": self.trigger_ordinal,
            "tool_call_id": self.tool_call_id,
        }


@dataclass(frozen=True, slots=True)
class ProtocolClosure:
    ordinals: tuple[int, ...]
    records: tuple[ClosureRecord, ...]
    valid: bool
    errors: tuple[str, ...] = ()


def close_protocol_messages(
    messages: Sequence[Mapping[str, Any]],
    selected_ordinals: Sequence[int] | set[int],
) -> ProtocolClosure:
    """Close OpenAI-style tool exchanges with auditable expansion reasons."""

    closed = {
        int(ordinal)
        for ordinal in selected_ordinals
        if 1 <= int(ordinal) <= len(messages)
    }
    records: list[ClosureRecord] = []
    calls: dict[str, int] = {}
    results: dict[str, list[int]] = {}
    for ordinal, message in enumerate(messages, start=1):
        for call_id in _call_ids(message):
            calls[call_id] = ordinal
        result = _result_id(message)
        if result:
            results.setdefault(result, []).append(ordinal)

    changed = True
    while changed:
        changed = False
        for call_id, call_ordinal in sorted(calls.items()):
            result_ordinals = results.get(call_id, [])
            if call_ordinal in closed:
                for result_ordinal in result_ordinals:
                    if result_ordinal not in closed:
                        closed.add(result_ordinal)
                        records.append(
                            ClosureRecord(
                                result_ordinal,
                                "tool_result_required_by_selected_call",
                                call_ordinal,
                                call_id,
                            )
                        )
                        changed = True
            if any(ordinal in closed for ordinal in result_ordinals) and call_ordinal not in closed:
                trigger = min(ordinal for ordinal in result_ordinals if ordinal in closed)
                closed.add(call_ordinal)
                records.append(
                    ClosureRecord(
                        call_ordinal,
                        "tool_call_required_by_selected_result",
                        trigger,
                        call_id,
                    )
                )
                changed = True

    if closed:
        first = min(closed)
        if _role(messages[first - 1]) not in {"user", "system"}:
            users = [
                ordinal
                for ordinal, message in enumerate(messages[: first - 1], start=1)
                if _role(message) == "user"
            ]
            if users and users[-1] not in closed:
                closed.add(users[-1])
                records.append(
                    ClosureRecord(users[-1], "leading_user_anchor", first)
                )

    errors: list[str] = []
    for ordinal in sorted(closed):
        message = messages[ordinal - 1]
        for call_id in _call_ids(message):
            if not results.get(call_id):
                errors.append(f"selected tool call {call_id!r} has no result in source history")
        result = _result_id(message)
        if result and result not in calls:
            errors.append(f"selected tool result {result!r} has no matching call in source history")
    return ProtocolClosure(
        ordinals=tuple(sorted(closed)),
        records=tuple(
            sorted(records, key=lambda item: (item.added_ordinal, item.reason, item.trigger_ordinal))
        ),
        valid=not errors,
        errors=tuple(sorted(set(errors))),
    )


@dataclass(frozen=True, slots=True)
class ProviderProtocol:
    """Serialization inputs plus provider limits; runtime callables are excluded."""

    name: str = "openai_chat"
    version: str = "openai_chat_v1"
    model: str = "zai/glm-4.7-flash"
    system_rules: tuple[str, ...] = ()
    base_messages: tuple[dict[str, Any], ...] = ()
    tools: tuple[dict[str, Any], ...] = ()
    hard_context_limit: int = 128_000
    serializer_overhead_tokens: int = 0
    token_counter: TokenCounter = estimate_tokens

    def __post_init__(self) -> None:
        if self.hard_context_limit <= 0:
            raise ValueError("hard_context_limit must be positive")
        if self.serializer_overhead_tokens < 0:
            raise ValueError("serializer_overhead_tokens must be non-negative")
        object.__setattr__(self, "system_rules", tuple(map(str, self.system_rules)))
        object.__setattr__(self, "base_messages", tuple(dict(item) for item in self.base_messages))
        object.__setattr__(self, "tools", tuple(dict(item) for item in self.tools))

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "model": self.model,
            "hard_context_limit": self.hard_context_limit,
            "serializer_overhead_tokens": self.serializer_overhead_tokens,
            "tokenizer": getattr(self.token_counter, "__name__", self.token_counter.__class__.__name__),
        }

    def request(self, messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return provider_prompt_request(
            model=self.model,
            messages=messages,
            tools=self.tools,
        )

    def count(self, value: Any) -> int:
        return int(self.token_counter(value))


@dataclass(frozen=True, slots=True)
class PromptCost:
    graph_selected: int
    compiled: int
    protocol_closed: int
    serialized_request: int
    provider_actual: int | None = None
    provider_cost_usd: float | None = None
    accounting_version: str = "provider_prompt_cost_v1"

    def __post_init__(self) -> None:
        for value in (
            self.graph_selected,
            self.compiled,
            self.protocol_closed,
            self.serialized_request,
        ):
            if value < 0:
                raise ValueError("prompt costs must be non-negative")
        if self.provider_actual is not None and self.provider_actual < 0:
            raise ValueError("provider_actual must be non-negative")
        if self.provider_cost_usd is not None and self.provider_cost_usd < 0:
            raise ValueError("provider_cost_usd must be non-negative")

    def with_actual(self, tokens: int, cost_usd: float | None = None) -> "PromptCost":
        return replace(self, provider_actual=int(tokens), provider_cost_usd=cost_usd)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accounting_version": self.accounting_version,
            "graph_selected": self.graph_selected,
            "compiled": self.compiled,
            "protocol_closed": self.protocol_closed,
            "serialized_request": self.serialized_request,
            "provider_actual": self.provider_actual,
            "provider_cost_usd": self.provider_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromptCost":
        return cls(**dict(data))


@dataclass(frozen=True, slots=True)
class PromptBudget:
    """Soft experimental budget separated from the provider hard limit."""

    soft_limit: int | None
    hard_limit: int | None = None

    def __post_init__(self) -> None:
        if self.soft_limit is not None and self.soft_limit <= 0:
            raise ValueError("soft_limit must be positive or None")
        if self.hard_limit is not None and self.hard_limit <= 0:
            raise ValueError("hard_limit must be positive or None")


def coerce_budget(value: int | PromptBudget | None, protocol: ProviderProtocol) -> PromptBudget:
    if isinstance(value, PromptBudget):
        hard_limit = (
            protocol.hard_context_limit
            if value.hard_limit is None
            else min(value.hard_limit, protocol.hard_context_limit)
        )
        return PromptBudget(
            value.soft_limit,
            hard_limit,
        )
    return PromptBudget(value, protocol.hard_context_limit)


def serialized_request_cost(
    protocol: ProviderProtocol,
    messages: Sequence[Mapping[str, Any]],
) -> int:
    """Count the exact canonical request including system/messages/tool schemas."""

    request = protocol.request(messages)
    return protocol.count(canonical_request_json(request)) + protocol.serializer_overhead_tokens
