from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tracegraph import EdgeType, TraceGraph
from tracegraph.context_engine import GraphConstrainedPolicy, PolicyRegistry
from tracegraph.context_engine.types import MemorySnapshot
from tracegraph.schema import (
    LifecycleProfile,
    Node,
    NodeType,
    StorageState,
    ToolStatus,
)


def add(
    graph: TraceGraph,
    node_id: str,
    node_type: NodeType,
    content,
    step: int,
    *,
    ordinal: int,
    active: bool = True,
    raw_ref: str | None = "archive://fixture",
    metadata: dict | None = None,
    side_effect: bool = False,
    profile: LifecycleProfile | None = None,
) -> Node:
    node = Node(
        node_id=node_id,
        node_type=node_type,
        content=content,
        step_id=step,
        active=active,
        raw_ref=raw_ref,
        side_effect=side_effect,
        metadata={"source_message_ordinal": ordinal, **(metadata or {})},
        lifecycle_profile=profile or LifecycleProfile(),
    )
    graph.add_node(node)
    return node


def test_snapshot_is_prefix_only_stable_and_deeply_immutable() -> None:
    graph = TraceGraph("prefix-only")
    add(graph, "goal", NodeType.GOAL, "repair alpha", 1, ordinal=1, raw_ref=None)
    policy = GraphConstrainedPolicy()
    context = {"cutoff_step": 1, "messages": [{"role": "user", "content": "repair alpha"}]}
    before = policy.snapshot(graph, context, 128)
    add(graph, "future", NodeType.OBSERVATION, "future suffix", 2, ordinal=2)
    after = policy.snapshot(graph, context, 128)
    assert before.to_dict() == after.to_dict()
    assert MemorySnapshot.from_dict(before.to_dict()) == before
    with pytest.raises(TypeError):
        before.goal_context["new"] = "mutation"
    with pytest.raises(FrozenInstanceError):
        before.cutoff_step = 9


def test_lifecycle_requires_exact_consumption_and_complete_same_field_update() -> None:
    graph = TraceGraph("lifecycle")
    old = add(
        graph,
        "old",
        NodeType.OBSERVATION,
        {"entity": "db", "field": "port", "value": 5432},
        1,
        ordinal=1,
        active=False,
    )
    consumer = add(
        graph,
        "consumer",
        NodeType.DECISION,
        "connect",
        2,
        ordinal=2,
        raw_ref=None,
        metadata={
            "consumed_facts": [{"entity": "db", "field": "port", "value": 5432}]
        },
    )
    graph.connect(old.node_id, consumer.node_id, EdgeType.PROVIDES_INPUT)
    stale = add(
        graph,
        "stale",
        NodeType.OBSERVATION,
        {"entity": "svc", "field": "status", "value": "old"},
        3,
        ordinal=3,
        active=False,
    )
    current = add(
        graph,
        "current",
        NodeType.OBSERVATION,
        {"entity": "svc", "field": "status", "value": "ready"},
        4,
        ordinal=4,
    )
    graph.connect(stale.node_id, current.node_id, EdgeType.SUPERSEDED_BY)
    partial = add(
        graph,
        "partial",
        NodeType.OBSERVATION,
        {"entity": "svc", "field": "status", "value": "maybe", "partial": True},
        5,
        ordinal=5,
        active=False,
    )
    graph.connect(current.node_id, partial.node_id, EdgeType.SUPERSEDED_BY)
    snapshot = GraphConstrainedPolicy().snapshot(graph, {}, 512)
    records = {record.event_id: record for record in snapshot.lifecycle_records}
    assert records["old"].relevance == "consumed"
    assert records["stale"].validity == "superseded"
    assert records["current"].validity != "superseded"
    assert records["current"].uncertain
    assert "unverified_or_conflicting_relation" in records["current"].reasons


def test_unresolved_constraint_unknown_partial_and_side_effect_are_hard() -> None:
    graph = TraceGraph("hard")
    add(graph, "constraint", NodeType.CONSTRAINT, "never deploy", 1, ordinal=1, raw_ref=None)
    add(graph, "error", NodeType.ERROR, {"error": "E42"}, 2, ordinal=2)
    call = add(
        graph,
        "unknown",
        NodeType.TOOL_CALL,
        {"tool_name": "mystery", "arguments": {}},
        3,
        ordinal=3,
        raw_ref=None,
        metadata={"call_id": "c1", "tool_name": "mystery"},
    )
    result = add(
        graph,
        "partial",
        NodeType.OBSERVATION,
        {"status": "partial"},
        3,
        ordinal=4,
        metadata={"call_id": "c1", "status": ToolStatus.PARTIAL_SUCCESS.value},
        side_effect=True,
    )
    graph.connect(call.node_id, result.node_id, EdgeType.PRODUCES)
    snapshot = GraphConstrainedPolicy().snapshot(
        graph,
        {"tool_schemas": [{"name": "known"}]},
        512,
    )
    records = {record.event_id: record for record in snapshot.lifecycle_records}
    assert "active_constraint" in records["constraint"].retention_obligations
    assert records["error"].validity == "negative_unresolved"
    assert records["unknown"].uncertain
    assert {"unknown_tool", "missing_raw_reference"}.issubset(records["unknown"].reasons)
    assert records["partial"].uncertain
    assert "audit_required" in records["partial"].retention_obligations


def test_parallel_tool_exchange_is_one_atomic_hard_span() -> None:
    graph = TraceGraph("parallel")
    call_a = add(
        graph, "call-a", NodeType.TOOL_CALL,
        {"tool_name": "read", "arguments": {}}, 1, ordinal=1,
        metadata={"call_id": "a", "tool_name": "read"},
    )
    call_b = add(
        graph, "call-b", NodeType.TOOL_CALL,
        {"tool_name": "write", "arguments": {}}, 1, ordinal=1,
        metadata={"call_id": "b", "tool_name": "write"}, side_effect=True,
    )
    result_a = add(
        graph, "result-a", NodeType.OBSERVATION, {"value": 1}, 2, ordinal=2,
        metadata={"call_id": "a"},
    )
    result_b = add(
        graph, "result-b", NodeType.OBSERVATION, {"value": 2}, 2, ordinal=3,
        metadata={"call_id": "b"},
    )
    graph.connect(call_a.node_id, result_a.node_id, EdgeType.PRODUCES)
    graph.connect(call_b.node_id, result_b.node_id, EdgeType.PRODUCES)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "read", "arguments": "{}"}},
            {"id": "b", "function": {"name": "write", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "1"},
        {"role": "tool", "tool_call_id": "b", "content": "2"},
    ]
    snapshot = GraphConstrainedPolicy().snapshot(graph, {"messages": messages}, 512)
    assert len(snapshot.spans) == 1
    assert snapshot.spans[0].hard
    assert set(snapshot.spans[0].node_ids) == {"call-a", "call-b", "result-a", "result-b"}


def historical_graph() -> tuple[TraceGraph, list[dict], str]:
    graph = TraceGraph("retrieval")
    call = add(
        graph, "call-old", NodeType.TOOL_CALL,
        {"tool_name": "legacy_shell", "arguments": {"path": "alpha"}},
        1, ordinal=1, active=False,
        metadata={"call_id": "old-call", "tool_name": "legacy_shell"},
    )
    error = add(
        graph, "error-old", NodeType.ERROR,
        {"error": "shell_syntax_mismatch", "entity": "alpha"},
        2, ordinal=2, active=False, metadata={"call_id": "old-call"},
    )
    resolution = add(
        graph, "resolution", NodeType.DECISION,
        "Use native shell for alpha", 3, ordinal=3, active=False, raw_ref=None,
    )
    add(
        graph, "current", NodeType.OBSERVATION,
        {"entity": "alpha", "field": "status", "value": "healthy"},
        4, ordinal=4, raw_ref=None, metadata={"critical_evidence": True},
    )
    graph.connect(call.node_id, error.node_id, EdgeType.FAILED_WITH)
    graph.connect(error.node_id, resolution.node_id, EdgeType.RESOLVED_BY)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "old-call", "function": {"name": "legacy_shell", "arguments": "{}"}
        }]},
        {"role": "tool", "tool_call_id": "old-call", "content": "shell syntax mismatch"},
        {"role": "assistant", "content": "Use native shell for alpha"},
        {"role": "system", "content": "current alpha status is healthy"},
    ]
    return graph, messages, error.node_id


def test_retrieval_uses_explicit_id_and_closes_causal_chain_but_ignores_unrelated() -> None:
    graph, messages, error_id = historical_graph()
    policy = GraphConstrainedPolicy()
    snapshot = policy.snapshot(graph, {"messages": messages}, 1)
    unrelated = policy.materialize(snapshot, "What is the weather?", {"max_input_tokens": 800})
    assert unrelated.retrieved_span_ids == ()
    plan = policy.materialize(
        snapshot,
        {"text": "Explain the old failure", "referenced_event_ids": [error_id]},
        {"max_input_tokens": 800, "retrieval_budget_tokens": 1024},
    )
    assert plan.send_eligible
    assert len(plan.retrieved_span_ids) >= 2
    text = " ".join(str(message.get("content", "")) for message in plan.messages)
    assert "historical evidence" in text
    assert text.index("shell syntax") < text.index("current alpha status")
    assert plan.provenance["causal_conclusion_eligible"] is True


def test_hard_budget_and_incomplete_protocol_fail_closed() -> None:
    graph = TraceGraph("budget")
    add(
        graph, "constraint", NodeType.CONSTRAINT, "must preserve " * 80,
        1, ordinal=1, raw_ref=None,
    )
    policy = GraphConstrainedPolicy()
    snapshot = policy.snapshot(graph, {}, 16)
    plan = policy.materialize(snapshot, "continue", {"max_input_tokens": 8})
    assert not plan.send_eligible
    assert "hard_closure_exceeds_provider_limit" in plan.safety_reasons

    broken = TraceGraph("broken")
    add(
        broken, "call", NodeType.TOOL_CALL,
        {"tool_name": "read", "arguments": {}}, 1, ordinal=1,
        metadata={"call_id": "missing", "tool_name": "read"},
    )
    snapshot = policy.snapshot(broken, {}, 512)
    plan = policy.materialize(snapshot, "continue", {"max_input_tokens": 512})
    assert not plan.send_eligible
    assert "tool_call_missing_result:missing" in plan.safety_reasons


def test_missing_archive_and_policy_registry() -> None:
    graph = TraceGraph("archive")
    profile = LifecycleProfile(storage=StorageState.ARCHIVED)
    add(
        graph, "archived", NodeType.OBSERVATION, {"value": "receipt"},
        1, ordinal=1, profile=profile,
    )
    policy = GraphConstrainedPolicy()
    snapshot = policy.snapshot(
        graph,
        {"archive_reader": lambda _: (_ for _ in ()).throw(FileNotFoundError("gone"))},
        128,
    )
    record = snapshot.lifecycle_records[0]
    assert record.uncertain
    assert "archive_round_trip_failed" in record.reasons
    plan = policy.materialize(snapshot, "receipt", {"max_input_tokens": 128})
    assert not plan.send_eligible
    assert "selected_archive_cannot_be_verified" in plan.safety_reasons
    assert "graph-v4" in PolicyRegistry.defaults().ids()
    assert "acon" in PolicyRegistry.defaults().ids()
