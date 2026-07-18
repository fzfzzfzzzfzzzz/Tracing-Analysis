"""Repair trace node sizes using the current content-only token accounting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens
from .graph import TraceGraph


def retokenize_trace(path: Path) -> dict[str, Any]:
    """Rewrite one trace with deterministic content-only node token counts."""

    graph = TraceGraph.load(path)
    old_total = sum(node.token_count for node in graph.nodes.values())
    changed_nodes = 0
    for node in graph.nodes.values():
        token_count = estimate_tokens(node.content)
        if node.token_count != token_count:
            changed_nodes += 1
            node.token_count = token_count
    new_total = sum(node.token_count for node in graph.nodes.values())
    previous_version = graph.metadata.get("token_accounting")
    graph.metadata["token_accounting"] = TOKEN_ACCOUNTING_VERSION
    graph.metadata["token_accounting_repair"] = {
        "previous_version": previous_version,
        "method": "deterministic byte-aware estimate over node content",
        "provider_prompt_usage_excluded": True,
    }
    graph.metadata["graph_validation_errors"] = graph.validate()
    graph.save(path)
    return {
        "trace_file": str(path),
        "session_id": graph.session_id,
        "node_count": len(graph.nodes),
        "changed_nodes": changed_nodes,
        "old_total_tokens": old_total,
        "new_total_tokens": new_total,
        "token_accounting": TOKEN_ACCOUNTING_VERSION,
        "validation_errors": graph.metadata["graph_validation_errors"],
    }


def retokenize_tree(root: Path) -> list[dict[str, Any]]:
    """Repair every recursively discovered ``trace.json`` below a directory."""

    if not root.is_dir():
        raise NotADirectoryError(root)
    return [retokenize_trace(path) for path in sorted(root.rglob("trace.json"))]
