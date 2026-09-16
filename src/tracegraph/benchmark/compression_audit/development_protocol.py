"""Compression Audit v0.2 development-only provider boundary and cost ledger."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


DEVELOPMENT_PROTOCOL = "v0.2-development"
ANSWER_CONTRACT_REVISION = "compression_audit_answer_contract_v02_r8"
INTERACTION_POLICY_REVISION = "compression_audit_interaction_policy_v02_r5"
DEFAULT_DEVELOPMENT_MODEL = "qwen3.7-plus"
DEVELOPMENT_ONLY_NOTICE = (
    "Seen v0.1 development data; results are development_only and are not "
    "independent validation evidence."
)
SUBMISSION_TOOL_NAME = "submit_compression_audit_v02"
SUBMISSION_SCHEMA_NAME = "compression_audit_v02"
SERVER_SUBMISSION_SCHEMA_NAME = "compression_audit_v02_server_fields"
FORMAT_REPAIR_ERROR_CHARS = 240


def compact_format_repair_message(
        parse_error: str, *, missing_exact: list[str] | tuple[str, ...] = (),
        missing_semantic: list[str] | tuple[str, ...] = ()) -> str:
    """Return a bounded repair turn without echoing a potentially huge response.

    A prior implementation embedded the complete provider response in this message.
    A max-length malformed answer could therefore make an otherwise valid
    full-history episode exceed the model context window on its repair turn.
    The model only needs the validation error and frozen field names to regenerate
    the structured answer from the still-visible records.
    """

    compact_error = " ".join(str(parse_error).split())[:FORMAT_REPAIR_ERROR_CHARS]
    if missing_exact or missing_semantic:
        return (
            "The preceding response had valid JSON shape but failed the answer contract: "
            + compact_error
            + ". Rewrite the complete answer from the supplied records. Put every listed "
            "label on its own actual newline (semicolons do not count). Exact labels require "
            "JSON literals copied from visible records: " + ", ".join(missing_exact)
            + ". Semantic labels require complete plain-language facts with identifying "
            "details and observed outcomes, not only error categories, action names, or "
            "evidence IDs: " + ", ".join(missing_semantic)
            + ". Keep e/t/s valid and do not invent facts. The prior response is omitted "
            "to preserve the frozen context budget."
        )
    return (
        "The preceding response was not a valid submission: " + compact_error
        + ". Regenerate complete a/e/t/s from the supplied records without inventing "
        "missing facts. The prior response is omitted to preserve the frozen context budget."
    )


def server_submission_json_schema(
        exact_labels: list[str], semantic_labels: list[str]) -> dict[str, Any]:
    """Return a vLLM-0.8.5-safe transport schema for labelled answers.

    That server accepts a regex on the nested ``a`` string but its guided
    decoder may still emit output that violates the surrounding object schema.
    Transport the same answer as typed fields and render it back to the frozen
    a/e/t/s protocol after receipt.
    """

    properties: dict[str, dict[str, Any]] = {}
    for name in exact_labels:
        properties[name] = ({"type": "object"} if name.endswith("_arguments")
                            else {"type": "string"})
    properties.update({name: {"type": "string"} for name in semantic_labels})
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer_fields": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": [*exact_labels, *semantic_labels],
            },
            "e": {"type": "array", "items": {"type": "string"}},
            "t": {"type": "string", "enum": ["current", "historical"]},
            "s": {"type": "boolean"},
        },
        "required": ["answer_fields", "e", "t", "s"],
    }


def server_submission_response_format(
        exact_labels: list[str], semantic_labels: list[str]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {
        "name": SERVER_SUBMISSION_SCHEMA_NAME,
        "strict": True,
        "schema": server_submission_json_schema(exact_labels, semantic_labels),
    }}


def server_submission_tool_schema(
        exact_labels: list[str], semantic_labels: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": SUBMISSION_TOOL_NAME,
        "description": "Submit typed audit answer fields and cited record IDs.",
        "parameters": server_submission_json_schema(exact_labels, semantic_labels),
    }}


def _parse_labelled_thinking_submission(
        value: str, exact_labels: list[str], semantic_labels: list[str]) -> dict[str, Any]:
    """Parse Qwen's strict labelled fallback after a native-tool thinking turn.

    The fallback is intentionally narrow: it is available only after a complete
    ``<think>`` envelope, accepts no prose or unknown labels, and reconstructs
    exactly the same typed object as the submission tool.
    """

    labels = [*exact_labels, *semantic_labels]
    found: dict[str, list[Any]] = {label: [] for label in labels}
    evidence: list[str] | None = None
    scope: Any = None
    side_effect: Any = None
    reading_evidence = False
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = next((label for label in labels if line.startswith(label + ":")), None)
        if matched is not None:
            reading_evidence = False
            raw_value = line.split(":", 1)[1].strip()
            if not raw_value:
                raise ValueError("server labelled submission has an empty answer field")
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                if matched in exact_labels:
                    raise ValueError("server exact field is not a JSON literal") from None
                parsed = raw_value
            found[matched].append(parsed)
            continue
        if line.startswith("e:"):
            if evidence is not None:
                raise ValueError("server labelled submission repeats e")
            raw_value = line.split(":", 1)[1].strip()
            if raw_value:
                try:
                    evidence = json.loads(raw_value)
                except json.JSONDecodeError as error:
                    raise ValueError("server labelled e is malformed JSON") from error
                reading_evidence = False
            else:
                evidence = []
                reading_evidence = True
            continue
        if line.startswith("t:"):
            if scope is not None:
                raise ValueError("server labelled submission repeats t")
            raw_value = line.split(":", 1)[1].strip()
            try:
                scope = json.loads(raw_value)
            except json.JSONDecodeError:
                scope = raw_value
            reading_evidence = False
            continue
        if line.startswith("s:"):
            if side_effect is not None:
                raise ValueError("server labelled submission repeats s")
            raw_value = line.split(":", 1)[1].strip()
            try:
                side_effect = json.loads(raw_value)
            except json.JSONDecodeError as error:
                raise ValueError("server labelled s is malformed JSON") from error
            reading_evidence = False
            continue
        if reading_evidence:
            try:
                record_id = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("server labelled evidence ID is malformed JSON") from error
            if not isinstance(record_id, str) or not record_id:
                raise ValueError("server labelled evidence ID must be a non-empty string")
            evidence.append(record_id)
            continue
        raise ValueError("server labelled submission contains unexpected text")
    if any(len(found[label]) != 1 for label in exact_labels):
        raise ValueError("server labelled submission requires each exact field once")
    if any(not found[label] for label in semantic_labels):
        raise ValueError("server labelled submission is missing a semantic field")
    if evidence is None or scope is None or side_effect is None:
        raise ValueError("server labelled submission is missing e/t/s")
    return {
        "answer_fields": {
            **{label: found[label][0] for label in exact_labels},
            **{label: found[label][-1] for label in semantic_labels},
        },
        "e": evidence,
        "t": scope,
        "s": side_effect,
    }


def normalize_server_submission(
        response: Mapping[str, Any], exact_labels: list[str],
        semantic_labels: list[str]) -> dict[str, Any]:
    """Convert the typed server transport back to a strict a/e/t/s response."""

    message, finish_reason = _provider_message(response)
    calls = message.get("tool_calls")
    tool_transport = calls not in (None, [])
    if tool_transport:
        if not isinstance(calls, list) or len(calls) != 1:
            raise ValueError("server submission requires exactly one tool call")
        call = calls[0]
        function = call.get("function") if isinstance(call, Mapping) else None
        if not isinstance(function, Mapping) or function.get("name") != SUBMISSION_TOOL_NAME:
            raise ValueError("server submission called the wrong tool")
        arguments = function.get("arguments")
    else:
        arguments = message.get("content")
    if isinstance(arguments, str):
        thinking_envelope = re.match(
            r"^\s*<think>.*?</think>\s*", arguments, flags=re.DOTALL
        )
        arguments = re.sub(
            r"^\s*<think>.*?</think>\s*", "", arguments, count=1, flags=re.DOTALL
        )
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as error:
            if tool_transport or thinking_envelope is None:
                raise ValueError("server submission is malformed JSON") from error
            arguments = _parse_labelled_thinking_submission(
                arguments, exact_labels, semantic_labels
            )
    if not isinstance(arguments, Mapping) or set(arguments) != {"answer_fields", "e", "t", "s"}:
        raise ValueError("server submission fields differ")
    fields = arguments["answer_fields"]
    ordered = [*exact_labels, *semantic_labels]
    if not isinstance(fields, Mapping) or set(fields) != set(ordered):
        raise ValueError("server answer_fields differ")
    lines = []
    for name in exact_labels:
        value = fields[name]
        if name.endswith("_arguments"):
            if not isinstance(value, Mapping):
                raise ValueError(f"{name} must be an object")
        elif not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        lines.append(f"{name}: " + json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    for name in semantic_labels:
        value = fields[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        lines.append(f"{name}: {value}")
    wire = {"a": "\n".join(lines), "e": arguments["e"],
            "t": arguments["t"], "s": arguments["s"]}
    content = json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if tool_transport:
        normalized_message = {"tool_calls": [{"function": {
            "name": SUBMISSION_TOOL_NAME, "arguments": content}}]}
    else:
        normalized_message = {"content": content}
    return {"choices": [{"finish_reason": finish_reason or "stop",
                           "message": normalized_message}]}


def submission_json_schema(*, answer_pattern: str | None = None) -> dict[str, Any]:
    """Return the shared strict JSON Schema for v0.2 answers."""

    schema = {
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
    }
    if answer_pattern is not None:
        schema["properties"]["a"]["pattern"] = answer_pattern
    return schema


def submission_response_format(*, answer_pattern: str | None = None) -> dict[str, Any]:
    """Return DashScope/OpenAI-compatible strict structured-output settings."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": SUBMISSION_SCHEMA_NAME,
            "strict": True,
            "schema": submission_json_schema(answer_pattern=answer_pattern),
        },
    }


def submission_vllm_085_response_format() -> dict[str, Any]:
    """Return the strongest schema vLLM 0.8.5 can enforce on the wire.

    vLLM 0.8.5's llguidance backend treats ``uniqueItems`` as an
    unimplemented matcher key and can consequently emit an immediate EOS.  We
    remove only that transport keyword.  ``parse_development_submission``
    still enforces uniqueness, non-empty strings, the exact key set, enums,
    and booleans after receipt, so the logical answer contract is unchanged.
    """

    response_format = deepcopy(submission_response_format())
    response_format["json_schema"]["schema"]["properties"]["e"].pop("uniqueItems")
    return response_format


def submission_tool_schema(*, answer_pattern: str | None = None) -> dict[str, Any]:
    """Return the deliberately short v0.2 structured submission contract."""

    return {
        "type": "function",
        "function": {
            "name": SUBMISSION_TOOL_NAME,
            "description": "Submit one audit answer with cited record IDs.",
            "parameters": submission_json_schema(answer_pattern=answer_pattern),
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
    # OpenAI-compatible servers may serialize an unused optional field as an
    # empty list alongside a valid JSON Schema content response. Treat only a
    # non-empty list as the tool-call transport; never discard valid content
    # merely because the provider emitted ``tool_calls: []``.
    if calls not in (None, []):
        if not isinstance(calls, list) or len(calls) != 1:
            reason = f" after finish_reason={finish_reason}" if finish_reason else ""
            raise ValueError(f"expected exactly one structured tool call{reason}")
        call = calls[0]
        function = call.get("function") if isinstance(call, Mapping) else None
        if not isinstance(function, Mapping) or function.get("name") != SUBMISSION_TOOL_NAME:
            raise ValueError("provider called the wrong submission tool")
        arguments = function.get("arguments")
    else:
        if "content" not in message:
            raise ValueError("expected exactly one structured tool call or JSON Schema response")
        arguments = message.get("content")
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
        "default_provider": "dashscope",
        "default_model": DEFAULT_DEVELOPMENT_MODEL,
        "structured_output": {
            "type": "json_schema",
            "strict": True,
            "enable_thinking": False,
        },
        "development_only": True,
        "independent_validation": False,
        "interpretation": DEVELOPMENT_ONLY_NOTICE,
        "single_aggregate_score": None,
    }
