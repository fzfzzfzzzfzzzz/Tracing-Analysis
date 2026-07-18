"""Protocol closure for compressed OpenAI-style chat histories."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .schema import NodeType


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


def close_message_ordinals(
    messages: Sequence[dict[str, Any]],
    ordinals: set[int],
) -> set[int]:
    """Close tool-call pairs and add a leading user anchor.

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
                changed = True
            if (
                result_ordinal in closed
                and call_ordinal not in closed
            ):
                closed.add(call_ordinal)
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
                closed.add(prior_users[-1])
    return closed


def project_context_items_to_messages(
    messages: Sequence[dict[str, Any]],
    items: Sequence[Any],
    nodes: Any,
) -> tuple[set[int], list[str]]:
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
    return close_message_ordinals(messages, ordinals), fragments
