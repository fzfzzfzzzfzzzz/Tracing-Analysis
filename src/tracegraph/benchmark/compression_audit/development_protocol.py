"""Compression Audit v0.2 development-only provider boundary and cost ledger."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


DEVELOPMENT_PROTOCOL = "v0.2-development"
DEVELOPMENT_ONLY_NOTICE = (
    "Seen v0.1 development data; results are development_only and are not "
    "independent validation evidence."
)
SUBMISSION_TOOL_NAME = "submit_compression_audit_v02"


def submission_tool_schema() -> dict[str, Any]:
    """Return the deliberately short v0.2 structured submission contract."""

    return {
        "type": "function",
        "function": {
            "name": SUBMISSION_TOOL_NAME,
            "description": "Submit one audit answer with cited record IDs.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "a": {"type": "string", "minLength": 1},
                    "e": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "t": {"type": "string", "enum": ["current", "historical"]},
                    "s": {"type": "boolean"},
                },
                "required": ["a", "e", "t", "s"],
            },
        },
    }


def _provider_message(response: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("provider response must contain exactly one choice")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ValueError("provider choice must be an object")
    finish_reason = str(choice.get("finish_reason") or "")
    if finish_reason == "length":
        raise ValueError("structured response was truncated with finish_reason=length")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("provider choice has no message object")
    return message, finish_reason


def parse_development_submission(response: Mapping[str, Any]) -> dict[str, Any]:
    """Unwrap provider syntax and validate fields without inventing omissions."""

    message, finish_reason = _provider_message(response)
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        reason = f" after finish_reason={finish_reason}" if finish_reason else ""
        raise ValueError(f"expected exactly one structured tool call{reason}")
    call = calls[0]
    function = call.get("function") if isinstance(call, Mapping) else None
    if not isinstance(function, Mapping) or function.get("name") != SUBMISSION_TOOL_NAME:
        raise ValueError("provider called the wrong submission tool")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as error:
            raise ValueError("submission arguments are malformed JSON") from error
    if not isinstance(arguments, Mapping):
        raise ValueError("submission arguments must be an object")
    required = {"a", "e", "t", "s"}
    if set(arguments) != required:
        missing = sorted(required.difference(arguments))
        extra = sorted(set(arguments).difference(required))
        raise ValueError(f"submission fields differ; missing={missing}; extra={extra}")
    answer = arguments["a"]
    evidence = arguments["e"]
    fact_scope = arguments["t"]
    side_effect = arguments["s"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("a must be a non-empty string")
    if (
        not isinstance(evidence, list)
        or any(not isinstance(item, str) or not item for item in evidence)
        or len(evidence) != len(set(evidence))
    ):
        raise ValueError("e must be a unique list of non-empty record IDs")
    if fact_scope not in {"current", "historical"}:
        raise ValueError("t must be current or historical")
    if type(side_effect) is not bool:
        raise ValueError("s must be boolean")
    return {
        "answer": answer,
        "evidence_record_ids": list(evidence),
        "fact_scope": fact_scope,
        "would_repeat_side_effect": side_effect,
    }


@dataclass(frozen=True, slots=True)
class ReacquisitionCost:
    """Disaggregated cost; intentionally has no unstable composite ratio."""

    model_calls: int = 0
    tool_calls: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0
    tool_observation_tokens: int = 0
    latency_seconds: float = 0.0
    cost_cny: float = 0.0

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.model_calls,
                self.tool_calls,
                self.provider_input_tokens,
                self.provider_output_tokens,
                self.tool_observation_tokens,
                self.latency_seconds,
                self.cost_cny,
            )
        ):
            raise ValueError("reacquisition costs must be non-negative")

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)

    def __add__(self, other: "ReacquisitionCost") -> "ReacquisitionCost":
        return ReacquisitionCost(
            **{
                field: getattr(self, field) + getattr(other, field)
                for field in self.__dataclass_fields__
            }
        )


@dataclass(frozen=True, slots=True)
class AgentTurnRecord:
    """Every model round, including format repair, is independently billed."""

    turn_id: str
    turn_kind: str
    provider_request_id: str
    cost: ReacquisitionCost
    billed: bool = True
    provider_retry: bool = False

    def __post_init__(self) -> None:
        if self.turn_kind not in {"answer", "reacquisition", "format_repair"}:
            raise ValueError("unsupported agent turn kind")
        if self.turn_kind == "format_repair" and not self.billed:
            raise ValueError("format repair must be billed as a separate agent turn")
        if self.turn_kind == "format_repair" and self.provider_retry:
            raise ValueError("format repair must be a separate agent turn, not a provider retry")
        if self.cost.model_calls != 1:
            raise ValueError("each agent turn must account for exactly one model call")

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "turn_kind": self.turn_kind,
            "provider_request_id": self.provider_request_id,
            "billed": self.billed,
            "provider_retry": self.provider_retry,
            "cost": self.cost.to_dict(),
        }


def development_metadata(*, run_id: str) -> dict[str, Any]:
    return {
        "schema_version": "compression_audit_development_v0_2",
        "protocol": DEVELOPMENT_PROTOCOL,
        "run_id": run_id,
        "development_only": True,
        "independent_validation": False,
        "interpretation": DEVELOPMENT_ONLY_NOTICE,
        "single_aggregate_score": None,
    }
