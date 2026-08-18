from __future__ import annotations

from copy import deepcopy

import pytest

from tracegraph.graph import TraceGraph
from tracegraph.lifecycle_evidence import (
    apply_grade_a_overlay,
    extract_lifecycle_evidence,
)
from tracegraph.schema import EdgeType, NodeType


def _config() -> dict:
    return {
        "evidence": {
            "grade_a": {
                "complete_scalar_consumption": {
                    "enabled": True,
                    "verifier": "deterministic_complete_scalar_consumption_v1",
                    "producer_tool_allowlist": ["find_user_id_by_email"],
                    "allowed_scalar_types": ["string", "integer", "number", "boolean"],
                    "allowed_singleton_wrapper_key_regex": (
                        r"^(id|[a-z][a-z0-9_]*_id|result|value)$"
                    ),
                    "confidence": 1.0,
                },
                "side_effect_receipt": {
                    "enabled": True,
                    "verifier": "deterministic_structured_receipt_v1",
                    "confidence": 1.0,
                },
            },
            "grade_b_ceiling_only": {
                "exact_entity_flow_candidate": {
                    "enabled": True,
                    "verifier": "deterministic_exact_entity_overlap_v1",
                    "identifier_key_regex": (
                        r"(^id$|_id$|^ids$|_ids$|number$|reference$|"
                        r"confirmation_code$|email$|username$)"
                    ),
                },
                "mutation_invalidation_candidate": {
                    "enabled": True,
                    "verifier": "deterministic_mutation_entity_overlap_v1",
                    "write_tool_prefix_regex": (
                        r"^(book|cancel|change|create|delete|exchange|modify|refund|"
                        r"return|send|transfer|update|write)"
                    ),
                },
            },
        }
    }


def _call_exchange(
    graph: TraceGraph,
    *,
    step: int,
    ordinal: int,
    name: str,
    arguments: dict,
    result: object,
    side_effect: bool = False,
) -> tuple[str, str, str]:
    decision = graph.create_node(
        NodeType.DECISION,
        {"tool_calls": [{"name": name, "arguments": arguments}]},
        step,
        node_id=f"decision_{ordinal}",
        metadata={"source_message_ordinal": ordinal},
    )
    call = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": name, "arguments": arguments, "call_id": f"call_{ordinal}"},
        step,
        node_id=f"call_{ordinal}",
        side_effect=side_effect,
        metadata={
            "source_message_ordinal": ordinal,
            "tool_name": name,
            "status": "success",
        },
    )
    observation = graph.create_node(
        NodeType.OBSERVATION,
        result,
        step + 1,
        node_id=f"observation_{ordinal + 1}",
        metadata={
            "source_message_ordinal": ordinal + 1,
            "tool_name": name,
            "status": "success",
            "semantic_outcome": "positive",
        },
    )
    graph.connect(decision.node_id, call.node_id, EdgeType.LEADS_TO)
    graph.connect(call.node_id, observation.node_id, EdgeType.PRODUCES)
    return decision.node_id, call.node_id, observation.node_id


def test_complete_scalar_consumption_is_grade_a_and_overlay_is_deterministic() -> None:
    graph = TraceGraph(session_id="phase51_scalar")
    _, _, source = _call_exchange(
        graph,
        step=1,
        ordinal=1,
        name="find_user_id_by_email",
        arguments={"email": "a@example.com"},
        result={"user_id": "user_123"},
    )
    target_decision, _, _ = _call_exchange(
        graph,
        step=3,
        ordinal=3,
        name="get_user_details",
        arguments={"user_id": "user_123"},
        result={"user_id": "user_123", "name": "A"},
    )

    first = extract_lifecycle_evidence(graph, cutoff_step=4, config=_config())
    second = extract_lifecycle_evidence(graph, cutoff_step=4, config=_config())

    hard = first.hard_dead_records
    assert first.to_dict() == second.to_dict()
    assert len(hard) == 1
    assert hard[0].source_event_id == source
    assert hard[0].target_event_id == target_decision
    overlay_a = apply_grade_a_overlay(graph, first)
    overlay_b = apply_grade_a_overlay(graph, second)
    assert overlay_a.to_dict() == overlay_b.to_dict()
    edges = overlay_a.outgoing(source, EdgeType.PROVIDES_INPUT)
    assert len(edges) == 1
    assert edges[0].confidence == 1.0
    assert edges[0].metadata["verifier"].startswith("deterministic_")


def test_multi_field_result_is_grade_b_only() -> None:
    graph = TraceGraph(session_id="phase51_multifield")
    _, _, source = _call_exchange(
        graph,
        step=1,
        ordinal=1,
        name="get_user_details",
        arguments={"user_id": "user_123"},
        result={"user_id": "user_123", "name": "A"},
    )
    _call_exchange(
        graph,
        step=3,
        ordinal=3,
        name="get_order_details",
        arguments={"user_id": "user_123"},
        result={"order_id": "order_1"},
    )

    report = extract_lifecycle_evidence(graph, cutoff_step=4, config=_config())

    assert not report.hard_dead_records
    candidates = [
        item
        for item in report.grade_b_records
        if item.relation == "exact_entity_flow_candidate"
    ]
    assert len(candidates) == 1
    assert candidates[0].source_event_id == source
    assert not candidates[0].may_generate_hard_dead
    assert apply_grade_a_overlay(graph, report).to_dict() == graph.to_dict()


def test_json_type_coercion_and_future_suffix_are_rejected() -> None:
    graph = TraceGraph(session_id="phase51_types")
    _call_exchange(
        graph,
        step=1,
        ordinal=1,
        name="find_user_id_by_email",
        arguments={"email": "a@example.com"},
        result={"user_id": 123},
    )
    _call_exchange(
        graph,
        step=3,
        ordinal=3,
        name="get_user_details",
        arguments={"user_id": "123"},
        result={"user_id": "123"},
    )
    before = extract_lifecycle_evidence(graph, cutoff_step=4, config=_config())
    graph.create_node(
        NodeType.OBSERVATION,
        {"user_id": 123},
        100,
        node_id="future_suffix",
        metadata={"source_message_ordinal": 100, "status": "success"},
    )
    after = extract_lifecycle_evidence(graph, cutoff_step=4, config=_config())

    assert not before.records
    assert before.to_dict() == after.to_dict()


def test_successful_write_receipt_is_retention_only() -> None:
    graph = TraceGraph(session_id="phase51_receipt")
    call_data = _call_exchange(
        graph,
        step=1,
        ordinal=1,
        name="cancel_pending_order",
        arguments={"order_id": "order_1"},
        result={"status": "cancelled", "order_id": "order_1"},
        side_effect=True,
    )

    report = extract_lifecycle_evidence(graph, cutoff_step=2, config=_config())

    receipts = [item for item in report.grade_a_records if item.relation == "side_effect_receipt"]
    assert len(receipts) == 1
    assert receipts[0].source_event_id == call_data[1]
    assert not receipts[0].may_generate_hard_dead
    assert not report.hard_dead_records


def test_overlay_rejects_report_from_another_prefix() -> None:
    graph = TraceGraph(session_id="phase51_origin")
    _call_exchange(
        graph,
        step=1,
        ordinal=1,
        name="find_user_id_by_email",
        arguments={"email": "a@example.com"},
        result="user_123",
    )
    report = extract_lifecycle_evidence(graph, cutoff_step=2, config=_config())
    changed = deepcopy(graph.to_dict())
    changed["metadata"] = {"changed": True}
    other = TraceGraph.from_dict(changed)

    # Graph metadata is intentionally outside the prefix evidence hash.
    apply_grade_a_overlay(other, report)
    other.nodes["observation_2"].content = "user_456"
    with pytest.raises(ValueError, match="does not belong"):
        apply_grade_a_overlay(other, report)

