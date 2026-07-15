"""Deterministic synthetic trace used only for integration smoke tests."""

from __future__ import annotations

from .archive import ArchiveStore
from .capture import estimate_tokens
from .graph import TraceGraph
from .lifecycle import LifecycleEngine
from .schema import EdgeType, LifecycleState, NodeType


def build_synthetic_trace(archive: ArchiveStore) -> TraceGraph:
    graph = TraceGraph(
        "synthetic_lifecycle_smoke",
        metadata={
            "source": "synthetic_fixture",
            "synthetic": True,
            "task_success": 1.0,
            "policy_violation": 0.0,
            "purpose": "pipeline_validation_not_benchmark_evidence",
        },
    )
    graph.create_node(
        NodeType.GOAL,
        "Safely update the order after confirming the user and preserve evidence.",
        0,
        lifecycle=LifecycleState.ACTIVE,
        token_count=18,
    )
    constraint = graph.create_node(
        NodeType.CONSTRAINT,
        "A side-effecting update requires explicit confirmation.",
        0,
        lifecycle=LifecycleState.ACTIVE,
        token_count=12,
    )

    old_call = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": "lookup_order", "arguments": {"id": "A-1"}},
        1,
        lifecycle=LifecycleState.CONSUMED,
        token_count=14,
        raw_ref=archive.put({"tool": "lookup_order", "id": "A-1"}),
        active=False,
        metadata={"tool_name": "lookup_order"},
    )
    old_observation = graph.create_node(
        NodeType.OBSERVATION,
        {"status": "pending", "version": 1, "catalogue": "x" * 1200},
        1,
        lifecycle=LifecycleState.CONSUMED,
        token_count=320,
        raw_ref=archive.put({"status": "pending", "version": 1, "catalogue": "x" * 1200}),
        active=False,
        metadata={"tool_name": "lookup_order"},
    )
    graph.connect(old_call.node_id, old_observation.node_id, EdgeType.PRODUCES)

    diagnostic_call = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": "check_inventory", "arguments": {"sku": "missing"}},
        1,
        lifecycle=LifecycleState.CONSUMED,
        token_count=10,
        raw_ref=archive.put({"tool": "check_inventory", "sku": "missing"}),
        active=False,
        metadata={"tool_name": "check_inventory"},
    )
    unresolved = graph.create_node(
        NodeType.ERROR,
        {"error": "inventory service unavailable"},
        1,
        lifecycle=LifecycleState.UNRESOLVED_FAILURE,
        token_count=11,
        raw_ref=archive.put({"error": "inventory service unavailable"}),
        metadata={"tool_name": "check_inventory"},
    )
    graph.connect(diagnostic_call.node_id, unresolved.node_id, EdgeType.FAILED_WITH)

    failed_call = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": "update_order", "arguments": {"id": "A-1"}},
        2,
        lifecycle=LifecycleState.AUDIT_REQUIRED,
        token_count=12,
        raw_ref=archive.put({"tool": "update_order", "id": "A-1", "attempt": 1}),
        side_effect=True,
        metadata={"tool_name": "update_order"},
    )
    failure = graph.create_node(
        NodeType.ERROR,
        {"error": "confirmation_required"},
        2,
        lifecycle=LifecycleState.UNRESOLVED_FAILURE,
        token_count=9,
        raw_ref=archive.put({"error": "confirmation_required"}),
        metadata={"tool_name": "update_order"},
    )
    graph.connect(failed_call.node_id, failure.node_id, EdgeType.FAILED_WITH)
    graph.connect(constraint.node_id, failed_call.node_id, EdgeType.BLOCKS)

    confirm_call = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": "read_confirmation", "arguments": {}},
        3,
        lifecycle=LifecycleState.CONSUMED,
        token_count=6,
        raw_ref=archive.put({"tool": "read_confirmation"}),
        active=False,
        metadata={"tool_name": "read_confirmation"},
    )
    confirmation = graph.create_node(
        NodeType.OBSERVATION,
        {"confirmed": True},
        3,
        lifecycle=LifecycleState.ACTIVE,
        token_count=6,
        raw_ref=archive.put({"confirmed": True}),
        metadata={"tool_name": "read_confirmation"},
    )
    graph.connect(confirm_call.node_id, confirmation.node_id, EdgeType.PRODUCES)

    retry_call = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": "update_order", "arguments": {"id": "A-1"}},
        4,
        lifecycle=LifecycleState.AUDIT_REQUIRED,
        token_count=12,
        raw_ref=archive.put({"tool": "update_order", "id": "A-1", "attempt": 2}),
        side_effect=True,
        metadata={"tool_name": "update_order"},
    )
    updated = graph.create_node(
        NodeType.OBSERVATION,
        {"status": "updated", "version": 2},
        4,
        lifecycle=LifecycleState.ACTIVE,
        token_count=10,
        raw_ref=archive.put({"status": "updated", "version": 2}),
        metadata={"tool_name": "update_order"},
    )
    graph.connect(retry_call.node_id, failed_call.node_id, EdgeType.RETRIES)
    graph.connect(retry_call.node_id, updated.node_id, EdgeType.PRODUCES)
    graph.connect(updated.node_id, failure.node_id, EdgeType.RESOLVES)
    graph.connect(updated.node_id, old_observation.node_id, EdgeType.SUPERSEDES)

    final = graph.create_node(
        NodeType.DECISION,
        "The confirmed order update succeeded at version 2.",
        5,
        lifecycle=LifecycleState.ACTIVE,
        token_count=13,
        metadata={"final": True},
    )
    graph.connect(confirmation.node_id, final.node_id, EdgeType.SUPPORTS)
    graph.connect(updated.node_id, final.node_id, EdgeType.SUPPORTS)
    graph.connect(final.node_id, retry_call.node_id, EdgeType.LEADS_TO)

    transitions = LifecycleEngine().apply(graph)
    graph.metadata["lifecycle_transitions"] = {
        node_id: [before.value, after.value]
        for node_id, (before, after) in transitions.items()
    }
    graph.metadata["synthetic_payload_tokens"] = estimate_tokens("x" * 1200)
    return graph
