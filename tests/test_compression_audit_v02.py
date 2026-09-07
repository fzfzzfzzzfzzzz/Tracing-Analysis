from __future__ import annotations

import json

import pytest

from tracegraph.benchmark.compression_audit.development_protocol import (
    AgentTurnRecord,
    ReacquisitionCost,
    development_metadata,
    parse_development_submission,
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


def test_development_metadata_forbids_independent_or_aggregate_claims() -> None:
    metadata = development_metadata(run_id="run-1")
    assert metadata["development_only"] is True
    assert metadata["independent_validation"] is False
    assert metadata["single_aggregate_score"] is None
    assert "Seen v0.1" in metadata["interpretation"]
