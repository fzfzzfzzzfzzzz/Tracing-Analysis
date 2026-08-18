"""Protocol closure for compressed OpenAI-style chat histories."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .schema import NodeType


@dataclass(frozen=True, slots=True)
class ClosureAddition:
    """One message introduced by protocol closure and its trigger."""

    ordinal: int
    reason: str
    trigger_ordinal: int | None = None
    call_id: str | None = None


@dataclass(slots=True)
class ProtocolClosureResult:
    """Typed, auditable result of projecting a compressed chat history."""

    ordinals: set[int]
    fragments: list[str] = field(default_factory=list)
    additions: list[ClosureAddition] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def _role(message: dict[str, Any]) -> str:
    return str(message.get("role") or "").lower()


def _tool_call_ids(message: dict[str, Any]) -> list[str]:
    calls = message.get("tool_calls") or message.get("function_calls") or []
    if isinstance(calls, dict):
        calls = [calls]
    result = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        call_id = call.get("id")
        if call_id:
            result.append(str(call_id))
    return result


def _tool_result_id(message: dict[str, Any]) -> str | None:
    if _role(message) != "tool":
        return None
    value = message.get("tool_call_id") or message.get("id")
    return str(value) if value else None


def close_message_protocol(
    messages: Sequence[dict[str, Any]],
    ordinals: set[int],
) -> ProtocolClosureResult:
    """Close tool-call pairs, retain a user anchor, and explain additions.

    Ordinals are one-based to match TraceGraph ``source_message_ordinal``.
    Compressed histories otherwise risk starting with an assistant/tool turn,
    which some OpenAI-compatible providers reject as an invalid ``messages``
    sequence.
    """

    closed = {
        int(ordinal)
        for ordinal in ordinals
        if 1 <= int(ordinal) <= len(messages)
    }
    call_to_ordinal: dict[str, int] = {}
    result_to_ordinal: dict[str, int] = {}
    additions: list[ClosureAddition] = []
    errors: list[str] = []
    for ordinal, message in enumerate(messages, start=1):
        for call_id in _tool_call_ids(message):
            call_to_ordinal[call_id] = ordinal
        result_id = _tool_result_id(message)
        if result_id:
            result_to_ordinal[result_id] = ordinal

    changed = True
    while changed:
        changed = False
        for call_id, call_ordinal in call_to_ordinal.items():
            result_ordinal = result_to_ordinal.get(call_id)
            if (
                call_ordinal in closed
                and result_ordinal is not None
                and result_ordinal not in closed
            ):
                closed.add(result_ordinal)
                additions.append(
                    ClosureAddition(
                        ordinal=result_ordinal,
                        reason="tool_result_for_selected_call",
                        trigger_ordinal=call_ordinal,
                        call_id=call_id,
                    )
                )
                changed = True
            if (
                result_ordinal in closed
                and call_ordinal not in closed
            ):
                closed.add(call_ordinal)
                additions.append(
                    ClosureAddition(
                        ordinal=call_ordinal,
                        reason="tool_call_for_selected_result",
                        trigger_ordinal=result_ordinal,
                        call_id=call_id,
                    )
                )
                changed = True

    if closed:
        first = min(closed)
        first_role = _role(messages[first - 1])
        if first_role != "user":
            prior_users = [
                ordinal
                for ordinal, message in enumerate(messages[: first - 1], start=1)
                if _role(message) == "user"
            ]
            if prior_users:
                anchor = prior_users[-1]
                if anchor not in closed:
                    closed.add(anchor)
                    additions.append(
                        ClosureAddition(
                            ordinal=anchor,
                            reason="leading_user_anchor",
                            trigger_ordinal=first,
                        )
                    )

    for call_id, call_ordinal in call_to_ordinal.items():
        if call_ordinal in closed and call_id not in result_to_ordinal:
            errors.append(f"selected tool call {call_id!r} has no result message")
    for call_id, result_ordinal in result_to_ordinal.items():
        if result_ordinal in closed and call_id not in call_to_ordinal:
            errors.append(f"selected tool result {call_id!r} has no call message")
    return ProtocolClosureResult(
        ordinals=closed,
        additions=additions,
        errors=errors,
    )


def close_message_ordinals(
    messages: Sequence[dict[str, Any]],
    ordinals: set[int],
) -> set[int]:
    """Compatibility wrapper returning only the closed ordinal set."""

    return close_message_protocol(messages, ordinals).ordinals


def project_context_items_to_protocol(
    messages: Sequence[dict[str, Any]],
    items: Sequence[Any],
    nodes: Any,
) -> ProtocolClosureResult:
    """Project graph context into raw-message ordinals plus compact fragments.

    Summary and archive-handle items are representations, not requests to
    replay their source messages. In particular, a Failure Card may point to a
    historical error node for provenance without re-injecting that tool result
    and forcing protocol closure to restore its original tool call.
    """

    ordinals: set[int] = {len(messages)} if messages else set()
    fragments: list[str] = []
    for item in items:
        if item.node_type in {NodeType.SUMMARY, NodeType.ARCHIVE_HANDLE}:
            fragments.append(
                json.dumps(item.content, ensure_ascii=False, default=str)
            )
            continue
        node = nodes.get(item.node_id)
        ordinal = node.metadata.get("source_message_ordinal") if node else None
        if isinstance(ordinal, int):
            ordinals.add(ordinal)
        else:
            fragments.append(
                json.dumps(item.content, ensure_ascii=False, default=str)
            )
    result = close_message_protocol(messages, ordinals)
    result.fragments = fragments
    return result


def project_context_items_to_messages(
    messages: Sequence[dict[str, Any]],
    items: Sequence[Any],
    nodes: Any,
) -> tuple[set[int], list[str]]:
    """Compatibility wrapper for legacy context managers."""

    result = project_context_items_to_protocol(messages, items, nodes)
    return result.ordinals, result.fragments
