from __future__ import annotations

import json

import pytest

from tracegraph.benchmark.compression_audit.development_protocol import (
    AgentTurnRecord,
    ReacquisitionCost,
    development_metadata,
    normalize_server_submission,
    parse_development_submission,
    server_submission_json_schema,
    server_submission_response_format,
    server_submission_tool_schema,
    submission_json_schema,
    submission_response_format,
    submission_vllm_085_response_format,
    submission_tool_schema,
)


def _response(arguments: object, *, finish_reason: str = "stop") -> dict[str, object]:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_compression_audit_v02",
                                "arguments": (
                                    arguments
                                    if isinstance(arguments, str)
                                    else json.dumps(arguments)
                                ),
                            }
                        }
                    ]
                },
            }
        ]
    }


def test_submission_schema_is_short_and_closed() -> None:
    parameters = submission_tool_schema()["function"]["parameters"]
    assert list(parameters["properties"]) == ["a", "e", "t", "s"]
    assert parameters["required"] == ["a", "e", "t", "s"]
    assert parameters["additionalProperties"] is False


def test_qwen37plus_response_format_uses_the_same_strict_schema() -> None:
    response_format = submission_response_format()
    assert response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "compression_audit_v02",
            "strict": True,
            "schema": submission_json_schema(),
        },
    }
    assert response_format["json_schema"]["schema"] == (
        submission_tool_schema()["function"]["parameters"]
    )


def test_submission_schema_can_constrain_answer_lines_on_wire() -> None:
    pattern = r"^diagnostic_evidence: [^\n]+$"
    response = submission_response_format(answer_pattern=pattern)
    tool = submission_tool_schema(answer_pattern=pattern)
    assert response["json_schema"]["schema"]["properties"]["a"]["pattern"] == pattern
    assert tool["function"]["parameters"]["properties"]["a"]["pattern"] == pattern


def test_vllm_085_wire_schema_omits_only_unsupported_uniqueness_keyword() -> None:
    canonical = submission_response_format()
    compatible = submission_vllm_085_response_format()
    assert "uniqueItems" in canonical["json_schema"]["schema"]["properties"]["e"]
    assert "uniqueItems" not in compatible["json_schema"]["schema"]["properties"]["e"]
    compatible["json_schema"]["schema"]["properties"]["e"]["uniqueItems"] = True
    assert compatible == canonical


def test_server_field_transport_is_typed_closed_and_lossless() -> None:
    exact = ["error_signature", "failed_action", "failed_arguments"]
    semantic = ["diagnostic_evidence"]
    schema = server_submission_json_schema(exact, semantic)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["answer_fields"]["required"] == exact + semantic
    assert schema["properties"]["answer_fields"]["properties"][
        "failed_arguments"] == {"type": "object"}
    assert server_submission_response_format(exact, semantic)["json_schema"][
        "schema"] == schema
    assert server_submission_tool_schema(exact, semantic)["function"][
        "parameters"] == schema

    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "answer_fields": {
            "error_signature": "bad_syntax",
            "failed_action": "shell_old",
            "failed_arguments": {"path": "a"},
            "diagnostic_evidence": "The old shell rejected path a.",
        },
        "e": ["e2", "e3"], "t": "historical", "s": False,
    })}}]}
    parsed = parse_development_submission(normalize_server_submission(
        response, exact, semantic))
    assert parsed["answer"] == (
        'error_signature: "bad_syntax"\nfailed_action: "shell_old"\n'
        'failed_arguments: {"path":"a"}\n'
        "diagnostic_evidence: The old shell rejected path a.")
    assert parsed["evidence_record_ids"] == ["e2", "e3"]

    response["choices"][0]["message"]["content"] = (
        "<think>Reason over the records first.</think>\n"
        + response["choices"][0]["message"]["content"]
    )
    parsed_after_thinking = parse_development_submission(
        normalize_server_submission(response, exact, semantic)
    )
    assert parsed_after_thinking == parsed

    response["choices"][0]["message"]["content"] = """<think>
Reason over the records first.
</think>
error_signature: "bad_syntax"
failed_action: "shell_old"
failed_arguments: {"path":"a"}
diagnostic_evidence: "e2"
diagnostic_evidence: The old shell rejected path a.
e:
"e2"
"e3"
t: "historical"
s: false
"""
    parsed_labelled = parse_development_submission(
        normalize_server_submission(response, exact, semantic)
    )
    assert parsed_labelled == parsed


def test_parser_only_unwraps_complete_provider_submission() -> None:
    parsed = parse_development_submission(
        _response({"a": "Use the cached receipt", "e": ["r-2"], "t": "current", "s": False})
    )
    assert parsed == {
        "answer": "Use the cached receipt",
        "evidence_record_ids": ["r-2"],
        "fact_scope": "current",
        "would_repeat_side_effect": False,
    }


def test_parser_accepts_json_schema_message_content() -> None:
    parsed = parse_development_submission(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {"a": "Use receipt", "e": ["r-2"], "t": "current", "s": False}
                        )
                    },
                }
            ]
        }
    )
    assert parsed["answer"] == "Use receipt"
    assert parsed["evidence_record_ids"] == ["r-2"]


def test_parser_accepts_json_schema_content_with_empty_optional_tool_calls() -> None:
    parsed = parse_development_submission(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {"a": "Use receipt", "e": ["r-2"], "t": "current", "s": False}
                        ),
                        "tool_calls": [],
                    },
                }
            ]
        }
    )
    assert parsed["answer"] == "Use receipt"
    assert parsed["evidence_record_ids"] == ["r-2"]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response("{"), "malformed JSON"),
        (_response({"a": "x", "e": [], "t": "current", "s": False}, finish_reason="length"), "truncated"),
        ({"choices": [{"finish_reason": "stop", "message": {}}]}, "one structured tool call"),
        (_response({"a": "x", "e": [], "t": "current"}), "missing=['s']"),
        (_response({"a": "x", "e": [], "t": "current", "s": False, "guess": 1}), "extra=['guess']"),
    ],
)
def test_parser_rejects_malformed_truncated_or_incomplete_responses(
    response: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        parse_development_submission(response)


def test_format_repair_is_a_separate_billed_turn() -> None:
    cost = ReacquisitionCost(
        model_calls=1,
        tool_calls=2,
        provider_input_tokens=11,
        provider_output_tokens=3,
        tool_observation_tokens=7,
        latency_seconds=0.5,
        cost_cny=0.01,
    )
    turn = AgentTurnRecord("turn-2", "format_repair", "req-2", cost)
    assert turn.billed is True
    assert turn.provider_retry is False
    assert set(turn.to_dict()["cost"]) == {
        "model_calls",
        "tool_calls",
        "provider_input_tokens",
        "provider_output_tokens",
        "tool_observation_tokens",
        "latency_seconds",
        "cost_cny",
    }
    with pytest.raises(ValueError, match="not a provider retry"):
        AgentTurnRecord("turn-2", "format_repair", "req-2", cost, provider_retry=True)
    with pytest.raises(ValueError, match="must be billed"):
        AgentTurnRecord("turn-2", "format_repair", "req-2", cost, billed=False)


def test_development_metadata_marks_weighted_score_and_component_requirement() -> None:
    metadata = development_metadata(run_id="run-1")
    assert metadata["default_model"] == "qwen3.7-plus"
    assert metadata["structured_output"] == {
        "type": "json_schema",
        "strict": True,
        "enable_thinking": False,
    }
    assert metadata["development_only"] is True
    assert metadata["independent_validation"] is False
    assert metadata["single_aggregate_score"] == "overall_failure_memory_score"
    assert metadata["aggregate_score_requires_component_reporting"] is True
    assert "Seen v0.1" in metadata["interpretation"]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"choices": []}, "exactly one choice"),
        ({"choices": ["bad"]}, "choice must be an object"),
        ({"choices": [{"finish_reason": "stop"}]}, "no message object"),
        (
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"tool_calls": [{"function": {"name": "wrong"}}]},
                }]
            },
            "wrong submission tool",
        ),
        (_response(1), "must be an object"),
        (_response({"a": "", "e": [], "t": "current", "s": False}), "non-empty"),
        (_response({"a": "x", "e": ["r", "r"], "t": "current", "s": False}), "unique list"),
        (_response({"a": "x", "e": [], "t": "future", "s": False}), "current or historical"),
        (_response({"a": "x", "e": [], "t": "current", "s": 0}), "must be boolean"),
    ],
)
def test_parser_rejects_other_invalid_provider_shapes(
    response: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_development_submission(response)


def test_cost_ledger_addition_and_invariants() -> None:
    first = ReacquisitionCost(model_calls=1, provider_input_tokens=4, cost_cny=0.1)
    second = ReacquisitionCost(model_calls=1, tool_calls=1, provider_output_tokens=2)
    total = first + second
    assert total.to_dict() == {
        "model_calls": 2,
        "tool_calls": 1,
        "provider_input_tokens": 4,
        "provider_output_tokens": 2,
        "tool_observation_tokens": 0,
        "latency_seconds": 0.0,
        "cost_cny": 0.1,
    }
    with pytest.raises(ValueError, match="non-negative"):
        ReacquisitionCost(cost_cny=-0.1)
    with pytest.raises(ValueError, match="unsupported"):
        AgentTurnRecord("turn", "retry", "req", first)
    with pytest.raises(ValueError, match="exactly one model call"):
        AgentTurnRecord("turn", "answer", "req", ReacquisitionCost())
