"""Definitions moved from ``tracegraph.phase5_offline``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from ..archive import ArchiveStore
from ..graph import TraceGraph
from ..schema import EdgeType, Node, NodeType
from ..trajectory_artifacts import sha256_json

from .phase5_constants import (
    TOOL_SCHEMA_ARTIFACT_VERSION as TOOL_SCHEMA_ARTIFACT_VERSION,
)



def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ordinal(node: Node) -> int | None:
    value = node.metadata.get("source_message_ordinal")
    return int(value) if isinstance(value, int) and value > 0 else None


def strict_predecision_nodes(
    graph: TraceGraph,
    *,
    cutoff_step: int,
    source_message_ordinal: int | None,
) -> tuple[Node, ...]:
    """Select only events strictly before the frozen decision message."""

    selected: list[Node] = []
    for node in graph.nodes.values():
        ordinal = _ordinal(node)
        if source_message_ordinal is not None and ordinal is not None:
            if ordinal >= source_message_ordinal:
                continue
        elif node.step_id >= cutoff_step:
            continue
        selected.append(node)
    return tuple(
        sorted(
            selected,
            key=lambda item: (item.step_id, _ordinal(item) or 0, item.node_id),
        )
    )


def build_strict_prefix(
    graph: TraceGraph,
    *,
    cutoff_step: int,
    source_message_ordinal: int | None,
    prefix_id: str,
) -> TraceGraph:
    nodes = strict_predecision_nodes(
        graph,
        cutoff_step=cutoff_step,
        source_message_ordinal=source_message_ordinal,
    )
    visible = {node.node_id for node in nodes}
    prefix = TraceGraph(
        session_id=graph.session_id,
        metadata={
            "source_session_id": graph.session_id,
            "phase5_prefix_id": prefix_id,
            "prefix_only": True,
            "cutoff_step": cutoff_step,
            "source_message_ordinal": source_message_ordinal,
        },
    )
    for node in nodes:
        prefix.add_node(Node.from_dict(node.to_dict()))
    for edge in sorted(graph.edges.values(), key=lambda item: item.edge_id):
        if edge.source in visible and edge.target in visible:
            prefix.add_edge(
                type(edge).from_dict(edge.to_dict()),
                validate_signature=False,
            )
    return prefix


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def prefix_messages(prefix: TraceGraph) -> tuple[dict[str, Any], ...]:
    """Rebuild the same canonical raw-message view used by Phase 4 R2.1."""

    grouped: defaultdict[int, list[Node]] = defaultdict(list)
    for node in prefix.nodes.values():
        ordinal = _ordinal(node)
        if ordinal is not None:
            grouped[ordinal].append(node)
    messages: list[dict[str, Any]] = []
    for _, nodes in sorted(grouped.items()):
        ordered = sorted(nodes, key=lambda item: (item.node_type.value, item.node_id))
        results = [
            node
            for node in ordered
            if node.node_type in {NodeType.OBSERVATION, NodeType.ERROR}
        ]
        if results:
            for node in results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(
                            node.metadata.get("call_id") or node.node_id
                        ),
                        "content": _text(node.content),
                    }
                )
            continue
        user_nodes = [
            node
            for node in ordered
            if node.metadata.get("source") == "user_message"
            or node.node_type in {NodeType.GOAL, NodeType.SUBGOAL}
        ]
        calls = [
            node
            for node in ordered
            if node.node_type in {NodeType.TOOL_CALL, NodeType.MCP_CALL}
        ]
        decisions = [
            node for node in ordered if node.node_type == NodeType.DECISION
        ]
        if user_nodes and not calls and not decisions:
            messages.append(
                {"role": "user", "content": _text(user_nodes[-1].content)}
            )
            continue
        message: dict[str, Any] = {
            "role": "assistant",
            "content": _text(decisions[-1].content) if decisions else "",
        }
        if calls:
            message["tool_calls"] = [
                {
                    "id": str(call.metadata.get("call_id") or call.node_id),
                    "type": "function",
                    "function": {
                        "name": str(
                            call.metadata.get("tool_name")
                            or (
                                call.content.get("tool_name")
                                if isinstance(call.content, Mapping)
                                else ""
                            )
                        ),
                        "arguments": _text(
                            call.content.get("arguments", {})
                            if isinstance(call.content, Mapping)
                            else {}
                        ),
                    },
                }
                for call in calls
            ]
        messages.append(message)
    return tuple(messages)


def policy_text(prefix: TraceGraph) -> tuple[str, ...]:
    return tuple(
        str(node.content)
        for node in prefix.find_nodes(node_types={NodeType.CONSTRAINT})
    )


def tool_schema_artifact(
    schemas_by_domain: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": TOOL_SCHEMA_ARTIFACT_VERSION,
        "source": "native_tau3_environment_openai_schema",
        "domains": {
            str(domain): [dict(item) for item in schemas]
            for domain, schemas in sorted(schemas_by_domain.items())
        },
    }
    value["artifact_sha256"] = sha256_json(value)
    return value


def _archive_tree(root: Path) -> dict[str, Any]:
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return {
        "root": root.as_posix(),
        "file_count": len(files),
        "tree_sha256": sha256_json(files),
    }


def _complete_tool_spans(prefix: TraceGraph) -> list[tuple[Node, tuple[Node, ...]]]:
    spans: list[tuple[Node, tuple[Node, ...]]] = []
    for call in prefix.find_nodes(
        node_types={NodeType.TOOL_CALL, NodeType.MCP_CALL}
    ):
        results = tuple(
            sorted(
                (
                    prefix.nodes[edge.target]
                    for edge in prefix.outgoing(call.node_id)
                    if edge.edge_type
                    in {EdgeType.PRODUCES, EdgeType.FAILED_WITH}
                ),
                key=lambda item: (item.step_id, item.node_id),
            )
        )
        if results:
            spans.append((call, results))
    return spans


def _explicit_terminal_relation_count(prefix: TraceGraph) -> int:
    terminal = {
        EdgeType.RESOLVED_BY,
        EdgeType.SUPERSEDED_BY,
        EdgeType.RESOLVES,
        EdgeType.SUPERSEDES,
        EdgeType.PROVIDES_INPUT,
        EdgeType.USES,
    }
    return sum(
        1
        for edge in prefix.edges.values()
        if edge.edge_type in terminal and edge.confidence == 1.0
    )


def structural_features(
    prefix: TraceGraph,
    *,
    archive: ArchiveStore,
) -> dict[str, Any]:
    messages = prefix_messages(prefix)
    spans = _complete_tool_spans(prefix)
    archived_spans = [
        (call, results)
        for call, results in spans
        if call.raw_ref
        and archive.exists(call.raw_ref)
        and all(result.raw_ref and archive.exists(result.raw_ref) for result in results)
    ]
    return {
        "prefix_node_count": len(prefix.nodes),
        "prefix_edge_count": len(prefix.edges),
        "message_count": len(messages),
        "tool_call_count": len(
            prefix.find_nodes(
                node_types={NodeType.TOOL_CALL, NodeType.MCP_CALL}
            )
        ),
        "complete_tool_span_count": len(spans),
        "archived_complete_tool_span_count": len(archived_spans),
        "explicit_terminal_relation_count": _explicit_terminal_relation_count(
            prefix
        ),
        "side_effect_node_count": sum(
            int(node.side_effect) for node in prefix.nodes.values()
        ),
        "constraint_node_count": len(
            prefix.find_nodes(node_types={NodeType.CONSTRAINT})
        ),
        "cost_analysis_eligible": bool(archived_spans and messages),
        "reactivation_candidate": bool(
            archived_spans and _explicit_terminal_relation_count(prefix)
        ),
    }


def _source_map(dataset: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["session_id"]): item
        for item in dataset.get("sources", ())
        if isinstance(item, Mapping)
    }
