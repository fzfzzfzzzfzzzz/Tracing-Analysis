from __future__ import annotations

import json
from copy import deepcopy

import pytest

from tracegraph.graph import TraceGraph
from tracegraph.lifecycle_annotation import (
    AnnotationBudget,
    assert_prefix_only_payload,
    consensus_labels,
    derive_relation_boolean_disposition,
    derive_relation_first_disposition,
    extract_function_arguments,
    prepare_annotation_request,
    prepare_validation_feedback_request,
    remaining_attempt_numbers,
    validate_machine_labels,
    validate_relation_boolean_labels,
    validate_relation_first_labels,
)
from tracegraph.lifecycle_state_machine import (
    build_forbidden_offline_projection,
    load_tool_effect_registry,
    replay_lifecycle_state_machine,
)
from tracegraph.schema import EdgeType, NodeType


def _annotation_config() -> dict:
    return {
        "model": {
            "api_model": "glm-4.7-flash",
            "temperature": 0.0,
            "thinking": "disabled",
            "max_output_tokens": 4096,
        },
        "split": {
            "development_task_ids": ["0", "1", "2"],
            "calibration_task_ids": ["3"],
            "held_out_task_ids": ["4"],
        },
    }


def _qwen_annotation_config() -> dict:
    config = _annotation_config()
    config["model"] = {
        **config["model"],
        "provider": "dashscope",
        "api_model": "qwen3.7-plus",
        "enable_thinking": False,
    }
    return config


def _qwen_relation_config() -> dict:
    config = _qwen_annotation_config()
    config["annotation"] = {
        "label_protocol": "relation_first_v1",
        "repair_retry_mode": "validation_feedback",
    }
    return config


def _qwen_relation_boolean_config() -> dict:
    config = _qwen_annotation_config()
    config["annotation"] = {
        "label_protocol": "relation_first_boolean_v2",
        "repair_retry_mode": "validation_feedback",
    }
    return config


def _registry_config() -> dict:
    specs = [
        {
            "tool_name": f"unused_{index}",
            "effect_type": "read",
            "entity_type": f"unused_{index}",
            "entity_keys": ["id"],
            "read_scope": ["*"],
            "write_scope": [],
            "snapshot": "complete",
            "receipt_required": False,
            "invalidation_scope": [],
        }
        for index in range(11)
    ]
    specs.extend(
        [
            {
                "tool_name": "get_order_details",
                "effect_type": "read",
                "entity_type": "order",
                "entity_keys": ["order_id"],
                "read_scope": ["*"],
                "write_scope": [],
                "snapshot": "complete",
                "receipt_required": False,
                "invalidation_scope": [],
            },
            {
                "tool_name": "modify_pending_order_items",
                "effect_type": "write",
                "entity_type": "order",
                "entity_keys": ["order_id"],
                "read_scope": [],
                "write_scope": ["items"],
                "snapshot": "partial",
                "receipt_required": True,
                "invalidation_scope": ["items"],
            },
            {
                "tool_name": "find_user_id_by_name_zip",
                "effect_type": "lookup",
                "entity_type": "user_lookup",
                "entity_keys": ["first_name", "last_name", "zip"],
                "read_scope": ["user_id"],
                "write_scope": [],
                "snapshot": "complete",
                "receipt_required": False,
                "invalidation_scope": [],
            },
            {
                "tool_name": "get_user_details",
                "effect_type": "read",
                "entity_type": "user",
                "entity_keys": ["user_id"],
                "read_scope": ["*"],
                "write_scope": [],
                "snapshot": "complete",
                "receipt_required": False,
                "invalidation_scope": [],
            },
        ]
    )
    return {"tool_effect_specs": specs}


def _exchange(
    graph: TraceGraph,
    *,
    index: int,
    tool_name: str,
    arguments: dict,
    result: object,
    error: bool = False,
    side_effect: bool = False,
) -> tuple[str, str]:
    call_id = f"call_{index}"
    call = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": tool_name, "arguments": arguments, "call_id": call_id},
        index * 2,
        node_id=call_id,
        side_effect=side_effect,
        metadata={
            "tool_name": tool_name,
            "call_id": call_id,
            "source_message_ordinal": index * 2,
        },
    )
    result_id = f"result_{index}"
    result_node = graph.create_node(
        NodeType.ERROR if error else NodeType.OBSERVATION,
        result,
        index * 2 + 1,
        node_id=result_id,
        metadata={
            "source_message_ordinal": index * 2 + 1,
            "status": "error" if error else "success",
        },
    )
    graph.connect(
        call.node_id,
        result_node.node_id,
        EdgeType.FAILED_WITH if error else EdgeType.PRODUCES,
    )
    return call.node_id, result_node.node_id


def _graph() -> TraceGraph:
    graph = TraceGraph(session_id="phase52-test", metadata={"cutoff_step": 99})
    graph.create_node(
        NodeType.GOAL,
        "Inspect order O-1",
        0,
        node_id="goal",
        metadata={"source": "user_message", "source_message_ordinal": 1},
    )
    return graph


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_double_pass_is_deterministic_blind_and_prefix_only() -> None:
    graph = _graph()
    for index in range(1, 5):
        _exchange(
            graph,
            index=index,
            tool_name="get_order_details",
            arguments={"order_id": f"O-{index}"},
            result={"order_id": f"O-{index}", "status": "pending"},
        )
    prefix_row = {"prefix_id": "phase52-shuffle-3", "task_id": "4"}
    first = prepare_annotation_request(
        prefix=graph,
        prefix_row=prefix_row,
        tool_schemas=[_schema("get_order_details")],
        pass_id="pass_a",
        config=_annotation_config(),
    )
    repeated = prepare_annotation_request(
        prefix=graph,
        prefix_row=prefix_row,
        tool_schemas=[_schema("get_order_details")],
        pass_id="pass_a",
        config=_annotation_config(),
    )
    pass_b = prepare_annotation_request(
        prefix=graph,
        prefix_row=prefix_row,
        tool_schemas=[_schema("get_order_details")],
        pass_id="pass_b",
        config=_annotation_config(),
    )
    assert first.request == repeated.request
    assert first.request_sha256 == repeated.request_sha256
    a_map = {item["span_id"]: item["opaque_span_id"] for item in first.mapping["spans"]}
    b_map = {item["span_id"]: item["opaque_span_id"] for item in pass_b.mapping["spans"]}
    assert a_map != b_map
    serialized = json.dumps(first.request, ensure_ascii=False).lower()
    assert '"reward"' not in serialized
    assert '"future_suffix"' not in serialized
    assert '"f5_label"' not in serialized
    assert first.request["model"] == "glm-4.7-flash"
    assert first.request["thinking"] == {"type": "disabled"}


def test_qwen_request_disables_thinking_without_glm_protocol_field() -> None:
    graph = _graph()
    _exchange(
        graph,
        index=1,
        tool_name="get_order_details",
        arguments={"order_id": "O-1"},
        result={"order_id": "O-1", "status": "pending"},
    )
    prepared = prepare_annotation_request(
        prefix=graph,
        prefix_row={"prefix_id": "phase52-qwen", "task_id": "4"},
        tool_schemas=[_schema("get_order_details")],
        pass_id="pass_a",
        config=_qwen_annotation_config(),
    )
    assert prepared.request["model"] == "qwen3.7-plus"
    assert prepared.request["enable_thinking"] is False
    assert "thinking" not in prepared.request
    assert prepared.request["tool_choice"]["function"]["name"] == (
        "submit_lifecycle_labels"
    )


def test_relation_first_request_omits_disposition_and_is_deterministic() -> None:
    graph = _graph()
    _exchange(
        graph,
        index=1,
        tool_name="get_order_details",
        arguments={"order_id": "O-1"},
        result={"order_id": "O-1", "status": "pending"},
    )
    prepared = prepare_annotation_request(
        prefix=graph,
        prefix_row={"prefix_id": "phase52-relation", "task_id": "4"},
        tool_schemas=[_schema("get_order_details")],
        pass_id="pass_a",
        config=_qwen_relation_config(),
    )
    function = prepared.request["tools"][0]["function"]
    properties = function["parameters"]["properties"]["labels"]["items"][
        "properties"
    ]
    assert function["name"] == "submit_lifecycle_relations"
    assert "disposition" not in properties
    assert set(properties["current_target_need"]["enum"]) == {
        "required",
        "useful",
        "not_needed",
        "uncertain",
    }
    assert prepared.request["tool_choice"]["function"]["name"] == (
        "submit_lifecycle_relations"
    )


def test_relation_boolean_request_uses_booleans_not_need_enum() -> None:
    graph = _graph()
    _exchange(
        graph,
        index=1,
        tool_name="get_order_details",
        arguments={"order_id": "O-1"},
        result={"order_id": "O-1", "status": "pending"},
    )
    prepared = prepare_annotation_request(
        prefix=graph,
        prefix_row={"prefix_id": "phase52-relation-bool", "task_id": "4"},
        tool_schemas=[_schema("get_order_details")],
        pass_id="pass_a",
        config=_qwen_relation_boolean_config(),
    )
    properties = prepared.request["tools"][0]["function"]["parameters"][
        "properties"
    ]["labels"]["items"]["properties"]
    assert "current_target_need" not in properties
    assert properties["required_for_current_target"] == {"type": "boolean"}
    assert properties["requirement_uncertain"] == {"type": "boolean"}


def test_relation_first_validation_and_fail_closed_derivation() -> None:
    raw = {
        "labels": [
            {
                "span_id": "S001",
                "terminal_reason": "superseded",
                "current_target_need": "not_needed",
                "relation_target_ids": ["S002"],
                "obligations": [],
                "evidence_event_ids": ["E001"],
                "reactivation_risk": False,
            },
            {
                "span_id": "S002",
                "terminal_reason": "active",
                "current_target_need": "required",
                "relation_target_ids": [],
                "obligations": ["receipt"],
                "evidence_event_ids": ["E002"],
                "reactivation_risk": False,
            },
        ]
    }
    labels = validate_relation_first_labels(
        raw,
        expected_span_ids={"S001", "S002"},
        allowed_event_ids={"E001", "E002"},
    )
    assert labels[0]["disposition"] == "safe_to_evict"
    assert labels[1]["disposition"] == "live_critical"
    assert all(
        item["disposition_provenance"] == "deterministic_relation_first_v1"
        for item in labels
    )
    invalid = deepcopy(raw)
    invalid["labels"][0]["current_target_need"] = "superseded"
    with pytest.raises(ValueError, match="invalid current_target_need enum for S001"):
        validate_relation_first_labels(
            invalid,
            expected_span_ids={"S001", "S002"},
            allowed_event_ids={"E001", "E002"},
        )


@pytest.mark.parametrize(
    ("reason", "need", "obligations", "risk", "expected"),
    [
        ("consumed", "not_needed", [], False, "safe_to_evict"),
        ("superseded", "useful", [], False, "live_noncritical"),
        ("active", "required", [], False, "live_critical"),
        ("active", "not_needed", [], False, "uncertain"),
        ("consumed", "not_needed", ["audit"], False, "live_critical"),
        ("consumed", "not_needed", [], True, "uncertain"),
    ],
)
def test_relation_first_disposition_mapping_is_fail_closed(
    reason: str,
    need: str,
    obligations: list[str],
    risk: bool,
    expected: str,
) -> None:
    assert derive_relation_first_disposition(
        terminal_reason=reason,
        current_target_need=need,
        obligations=obligations,
        reactivation_risk=risk,
    ) == expected


def test_validation_feedback_request_is_deterministic_and_does_not_mutate_base() -> None:
    base = {
        "model": "qwen3.7-plus",
        "messages": [{"role": "user", "content": "Return JSON labels"}],
        "tools": [],
    }
    before = deepcopy(base)
    first = prepare_validation_feedback_request(
        base,
        validation_error="invalid terminal_reason enum for S001",
    )
    second = prepare_validation_feedback_request(
        base,
        validation_error="invalid terminal_reason enum for S001",
    )
    assert base == before
    assert first == second
    assert len(first["messages"]) == 2
    assert "invalid terminal_reason enum for S001" in first["messages"][-1][
        "content"
    ]


def test_relation_boolean_validation_and_derivation() -> None:
    raw = {
        "labels": [
            {
                "span_id": "S001",
                "terminal_reason": "superseded",
                "required_for_current_target": False,
                "requirement_uncertain": False,
                "relation_target_ids": ["S002"],
                "obligations": [],
                "evidence_event_ids": ["E001"],
                "reactivation_risk": False,
            },
            {
                "span_id": "S002",
                "terminal_reason": "active",
                "required_for_current_target": True,
                "requirement_uncertain": False,
                "relation_target_ids": [],
                "obligations": [],
                "evidence_event_ids": ["E002"],
                "reactivation_risk": False,
            },
        ]
    }
    labels = validate_relation_boolean_labels(
        raw,
        expected_span_ids={"S001", "S002"},
        allowed_event_ids={"E001", "E002"},
    )
    assert labels[0]["disposition"] == "safe_to_evict"
    assert labels[1]["disposition"] == "live_critical"
    invalid = deepcopy(raw)
    invalid["labels"][0]["required_for_current_target"] = "false"
    with pytest.raises(ValueError, match="required_for_current_target must be boolean"):
        validate_relation_boolean_labels(
            invalid,
            expected_span_ids={"S001", "S002"},
            allowed_event_ids={"E001", "E002"},
        )
    assert derive_relation_boolean_disposition(
        terminal_reason="consumed",
        required_for_current_target=False,
        requirement_uncertain=True,
        obligations=[],
        reactivation_risk=False,
    ) == "uncertain"


@pytest.mark.parametrize("forbidden", ["reward", "future_events", "f5_label", "prune_result", "token_gain"])
def test_leakage_injection_is_rejected(forbidden: str) -> None:
    with pytest.raises(ValueError, match="forbidden annotation input key"):
        assert_prefix_only_payload({"safe": {forbidden: 1}})


def test_response_schema_rejects_missing_duplicate_unknown_and_extra() -> None:
    valid = {
        "span_id": "S001",
        "disposition": "safe_to_evict",
        "terminal_reason": "consumed",
        "relation_target_ids": ["S002"],
        "obligations": [],
        "evidence_event_ids": ["E001"],
        "reactivation_risk": False,
    }
    second = {**valid, "span_id": "S002", "relation_target_ids": []}
    labels = validate_machine_labels(
        {"labels": [valid, second]},
        expected_span_ids={"S001", "S002"},
        allowed_event_ids={"E001"},
    )
    assert len(labels) == 2
    with pytest.raises(ValueError, match="missing or duplicate"):
        validate_machine_labels(
            {"labels": [valid, valid]},
            expected_span_ids={"S001", "S002"},
            allowed_event_ids={"E001"},
        )
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_machine_labels(
            {"labels": [{**valid, "evidence_event_ids": ["E999"]}, second]},
            expected_span_ids={"S001", "S002"},
            allowed_event_ids={"E001"},
        )
    with pytest.raises(ValueError, match="additional"):
        validate_machine_labels(
            {"labels": [{**valid, "extra": True}, second]},
            expected_span_ids={"S001", "S002"},
            allowed_event_ids={"E001"},
        )


def test_consensus_requires_disposition_reason_target_and_obligations() -> None:
    base = {
        "span_id": "original",
        "disposition": "safe_to_evict",
        "terminal_reason": "consumed",
        "relation_target_ids": ["later"],
        "obligations": [],
        "evidence_event_ids": ["e1"],
        "reactivation_risk": False,
    }
    agreed = consensus_labels([base], [{**base, "evidence_event_ids": ["e2"]}])[0]
    assert agreed["machine_consensus"] is True
    assert agreed["evidence_event_ids"] == ["e1", "e2"]
    disagreed = consensus_labels([base], [{**base, "terminal_reason": "duplicate"}])[0]
    assert disagreed["machine_consensus"] is False
    assert disagreed["disposition"] == "uncertain"


def test_truncated_json_single_retry_and_global_budgets_stop() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_lifecycle_labels",
                                "arguments": '{"labels":[',
                            }
                        }
                    ]
                }
            }
        ]
    }
    with pytest.raises(json.JSONDecodeError):
        extract_function_arguments(response)
    assert remaining_attempt_numbers(0, retry_per_request_max=1) == (1, 2)
    assert remaining_attempt_numbers(1, retry_per_request_max=1) == (2,)
    assert remaining_attempt_numbers(2, retry_per_request_max=1) == ()
    limits = {
        "request_count_hard_max": 2,
        "estimated_input_tokens_hard_max": 100,
        "actual_output_tokens_hard_max": 50,
    }
    ledger = [
        {
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
        {
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
    ]
    with pytest.raises(RuntimeError, match="request-count"):
        AnnotationBudget.from_ledger(ledger, limits=limits).assert_can_submit()
    output_limited = {
        **limits,
        "request_count_hard_max": 3,
        "actual_output_tokens_hard_max": 4,
    }
    with pytest.raises(RuntimeError, match="output-token"):
        AnnotationBudget.from_ledger(ledger, limits=output_limited).assert_can_submit()


def test_state_machine_full_read_supersession_is_deterministic() -> None:
    graph = _graph()
    _, old_result = _exchange(
        graph,
        index=1,
        tool_name="get_order_details",
        arguments={"order_id": "O-1"},
        result={"order_id": "O-1", "status": "pending"},
    )
    _exchange(
        graph,
        index=2,
        tool_name="get_order_details",
        arguments={"order_id": "O-1"},
        result={"order_id": "O-1", "status": "shipped"},
    )
    registry = load_tool_effect_registry(_registry_config())
    before = deepcopy(graph.to_dict())
    first = replay_lifecycle_state_machine(graph, registry=registry)
    second = replay_lifecycle_state_machine(graph, registry=registry)
    prediction = next(item for item in first if item.source_event_id == old_result)
    assert prediction.disposition == "safe_to_evict"
    assert prediction.terminal_reason == "superseded"
    assert first == second
    assert graph.to_dict() == before
    projection = build_forbidden_offline_projection(graph, first)
    assert projection["never_send_to_provider"] is True
    assert prediction.span_id in projection["evicted_span_ids"]


def test_future_tool_exchange_is_excluded_by_frozen_cutoff() -> None:
    graph = _graph()
    _exchange(
        graph,
        index=1,
        tool_name="get_order_details",
        arguments={"order_id": "O-1"},
        result={"order_id": "O-1"},
    )
    baseline = replay_lifecycle_state_machine(
        graph, registry=load_tool_effect_registry(_registry_config())
    )
    graph.metadata["cutoff_step"] = 3
    _exchange(
        graph,
        index=100,
        tool_name="get_order_details",
        arguments={"order_id": "O-1"},
        result={"order_id": "O-1", "future": True},
    )
    observed = replay_lifecycle_state_machine(
        graph, registry=load_tool_effect_registry(_registry_config())
    )
    assert observed == baseline


def test_state_machine_partial_invalidation_and_receipt_fail_closed() -> None:
    graph = _graph()
    _, old_result = _exchange(
        graph,
        index=1,
        tool_name="get_order_details",
        arguments={"order_id": "O-1"},
        result={"order_id": "O-1", "items": ["A"], "address": "X"},
    )
    _, receipt = _exchange(
        graph,
        index=2,
        tool_name="modify_pending_order_items",
        arguments={"order_id": "O-1", "item_ids": ["A"]},
        result={"status": "success"},
        side_effect=True,
    )
    predictions = replay_lifecycle_state_machine(
        graph, registry=load_tool_effect_registry(_registry_config())
    )
    old = next(item for item in predictions if item.source_event_id == old_result)
    write = next(item for item in predictions if item.source_event_id == receipt)
    assert old.disposition == "uncertain"
    assert write.disposition == "live_critical"
    assert set(write.obligations) == {"audit", "receipt"}


def test_state_machine_consumption_retry_reactivation_and_unknown() -> None:
    graph = _graph()
    _, lookup_result = _exchange(
        graph,
        index=1,
        tool_name="find_user_id_by_name_zip",
        arguments={"first_name": "A", "last_name": "B", "zip": "1"},
        result={"user_id": "U-1"},
    )
    call, user_result = _exchange(
        graph,
        index=2,
        tool_name="get_user_details",
        arguments={"user_id": "U-1"},
        result={"user_id": "U-1", "name": "A"},
    )
    _, failed = _exchange(
        graph,
        index=3,
        tool_name="get_order_details",
        arguments={"order_id": "O-2"},
        result={"error": "temporary"},
        error=True,
    )
    _exchange(
        graph,
        index=4,
        tool_name="get_order_details",
        arguments={"order_id": "O-2"},
        result={"order_id": "O-2"},
    )
    _, unknown_result = _exchange(
        graph,
        index=5,
        tool_name="not_registered",
        arguments={},
        result={"ok": True},
    )
    registry = load_tool_effect_registry(_registry_config())
    predictions = replay_lifecycle_state_machine(graph, registry=registry)
    assert next(item for item in predictions if item.source_event_id == lookup_result).terminal_reason == "consumed"
    assert next(item for item in predictions if item.source_event_id == failed).terminal_reason == "resolved"
    assert next(item for item in predictions if item.source_event_id == unknown_result).disposition == "uncertain"
    reactivated = replay_lifecycle_state_machine(
        graph, registry=registry, referenced_event_ids=[call]
    )
    call_prediction = next(item for item in reactivated if item.source_event_id == user_result)
    assert call_prediction.disposition == "live_critical"
