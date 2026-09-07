"""Provider-message synthesis and closure validation for policy v4."""

from __future__ import annotations

# ruff: noqa: E402, F401

import json
import re
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable
from ..capture import estimate_tokens
from ..graph import TraceGraph
from ..schema import EdgeType, Node, NodeType, RelevanceState, RetentionObligation, SemanticOutcome, ToolStatus, ValidityState
from .span_analysis import _group_spans
from .types import POLICY_API_VERSION, ContextPlan, LifecycleRecord, MemorySnapshot, MemorySpan, stable_value_hash

_WORDS = re.compile(r"[\w.-]+", re.UNICODE)

_CALL_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}

_RESULT_TYPES = {NodeType.OBSERVATION, NodeType.ERROR}

_EVIDENCE_TYPES = {NodeType.OBSERVATION, NodeType.ERROR, NodeType.DECISION}

_CAUSAL_EDGES = {
    EdgeType.PRODUCES,
    EdgeType.FAILED_WITH,
    EdgeType.USES,
    EdgeType.SUPPORTS,
    EdgeType.BLOCKS,
    EdgeType.RESOLVES,
    EdgeType.SUPERSEDES,
    EdgeType.RETRIES,
    EdgeType.LEADS_TO,
    EdgeType.PROVIDES_INPUT,
    EdgeType.RETRIED_BY,
    EdgeType.RESOLVED_BY,
    EdgeType.SUPERSEDED_BY,
}

_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.INCONCLUSIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}

from .lifecycle_v4 import _call_id, _mapping, _tool_name

def _synthetic_messages(nodes: Sequence[Node]) -> tuple[dict[str, Any], ...]:
    calls = sorted(
        (node for node in nodes if node.node_type in _CALL_TYPES),
        key=lambda node: (node.step_id, node.node_id),
    )
    results = sorted(
        (node for node in nodes if node.node_type in _RESULT_TYPES),
        key=lambda node: (node.step_id, node.node_id),
    )
    messages: list[dict[str, Any]] = []
    if calls:
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": _call_id(call) or call.node_id,
                    "type": "function",
                    "function": {
                        "name": _tool_name(call) or "unknown_tool",
                        "arguments": json.dumps(
                            _mapping(call.content).get("arguments", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                }
                for call in calls
            ],
        })
        messages.extend(
            {
                "role": "tool",
                "tool_call_id": _call_id(result),
                "content": json.dumps(result.content, ensure_ascii=False, default=str),
            }
            for result in results
            if _call_id(result)
        )
        return tuple(messages)
    node = nodes[-1]
    role = "user" if node.node_type in {NodeType.GOAL, NodeType.SUBGOAL} else "system"
    return ({
        "role": role,
        "content": json.dumps(node.content, ensure_ascii=False, default=str),
    },)


def _protocol_errors(messages: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    calls: dict[str, int] = {}
    results: dict[str, int] = {}
    errors: list[str] = []
    for index, message in enumerate(messages):
        for call in message.get("tool_calls", ()) or ():
            if isinstance(call, Mapping) and call.get("id"):
                call_id = str(call["id"])
                if call_id in calls:
                    errors.append(f"duplicate_tool_call:{call_id}")
                calls[call_id] = index
        if str(message.get("role", "")).casefold() == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if not call_id:
                errors.append("tool_result_missing_call_id")
            elif call_id in results:
                errors.append(f"duplicate_tool_result:{call_id}")
            else:
                results[call_id] = index
    for call_id, index in calls.items():
        if call_id not in results:
            errors.append(f"tool_call_missing_result:{call_id}")
        elif results[call_id] <= index:
            errors.append(f"tool_result_out_of_order:{call_id}")
    for call_id in results.keys() - calls.keys():
        errors.append(f"tool_result_missing_call:{call_id}")
    return tuple(sorted(set(errors)))
