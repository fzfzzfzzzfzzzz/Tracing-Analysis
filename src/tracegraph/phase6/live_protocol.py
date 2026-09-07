"""Definitions moved from ``tracegraph.phase6_live``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from ..capture import estimate_tokens
from ..decision_state import stable_digest

from .live_constants import (
    ANSWER_MAX_CHARS_V2 as ANSWER_MAX_CHARS_V2,
    FORK_TYPES as FORK_TYPES,
    LIVE_METHOD_IDS as LIVE_METHOD_IDS,
    PROMPT_PROTOCOL_V1 as PROMPT_PROTOCOL_V1,
    PROMPT_PROTOCOL_V2 as PROMPT_PROTOCOL_V2,
    _ANSWER_TOOL as _ANSWER_TOOL,
)



def _opaque_ids(prefix: Mapping[str, Any]) -> dict[str, str]:
    nodes = sorted(
        prefix["graph"]["nodes"],
        key=lambda item: (int(item["step_id"]), str(item["node_id"])),
    )
    return {str(node["node_id"]): f"R{index:03d}" for index, node in enumerate(nodes, 1)}


def _compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _compact_value(child)
            for key, child in value.items()
            if str(key).lower() not in {"detail", "padding", "blob"}
        }
    if isinstance(value, list):
        return [_compact_value(item) for item in value]
    if isinstance(value, str) and len(value) > 400:
        return value[:400] + "…"
    return value


def _projection_scope(projection: Mapping[str, Any], event_id: str) -> str | None:
    if event_id in set(map(str, projection.get("current_fact_ids", ()))):
        return "current"
    if event_id in set(map(str, projection.get("historical_fact_ids", ()))):
        return "historical"
    return None


def _lifecycle_scope(projection: Mapping[str, Any], event_id: str) -> str:
    record = next(
        item
        for item in projection["lifecycle"]["records"]
        if str(item["event_id"]) == event_id
    )
    return "current" if record["state"] in {"active", "pinned"} else "historical"


def _record_rows(
    prefix: Mapping[str, Any],
    projection: Mapping[str, Any],
    method_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    node_by_id = {
        str(item["node_id"]): item
        for item in prefix["graph"]["nodes"]
    }
    opaque = _opaque_ids(prefix)
    raw = set(map(str, projection.get("raw_event_ids", ())))
    injected = set(map(str, projection.get("injected_event_ids", ())))
    represented = set(map(str, projection.get("represented_event_ids", ())))
    masked = set(map(str, projection.get("masked_event_ids", ())))
    if method_id == "M6_fixed_handoff":
        selected = set(node_by_id)
    else:
        selected = raw.union(injected).union(represented)
    rows: list[dict[str, Any]] = []
    for event_id in sorted(
        selected,
        key=lambda item: (int(node_by_id[item]["step_id"]), item),
    ):
        node = node_by_id[event_id]
        content = node["content"]
        representation = "full"
        if method_id == "M6_fixed_handoff":
            content = _compact_value(content)
            representation = "fixed_handoff"
        elif event_id in represented and event_id not in raw and event_id not in injected:
            guard = node.get("metadata", {}).get("guard_text")
            content = guard if guard else _compact_value(content)
            representation = "short_record"
        row = {
            "record_id": opaque[event_id],
            "kind": node["node_type"],
            "representation": representation,
            "content": content,
        }
        if method_id in {
            "M3_lifecycle_eviction_only",
            "M4_lifecycle_flat_reactivation",
            "M5_lifecycle_causal_reactivation",
        }:
            row["fact_scope"] = _projection_scope(projection, event_id)
        elif method_id == "M6_fixed_handoff":
            row["fact_scope"] = _lifecycle_scope(projection, event_id)
        rows.append(row)
    if method_id == "M1_recent_masking":
        rows.extend(
            {
                "record_id": f"MASKED_{index:03d}",
                "kind": "masked",
                "representation": "masked",
                "content": "Earlier observation hidden.",
            }
            for index, _ in enumerate(sorted(masked), 1)
        )
    return rows, opaque


def _answer_tool_v2(visible_record_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": (
                "Submit a short answer using only visible record IDs. "
                "Never copy padding or repeated placeholder characters."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "answer": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": ANSWER_MAX_CHARS_V2,
                    },
                    "evidence_record_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": sorted(set(visible_record_ids)),
                        },
                        "uniqueItems": True,
                    },
                    "fact_scope": {
                        "type": "string",
                        "enum": ["current", "historical"],
                    },
                    "would_repeat_side_effect": {"type": "boolean"},
                },
                "required": [
                    "answer",
                    "evidence_record_ids",
                    "fact_scope",
                    "would_repeat_side_effect",
                ],
            },
        },
    }


def _rows_for_prompt_v2(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    visible = [dict(row) for row in rows if row.get("representation") != "masked"]
    masked_count = len(rows) - len(visible)
    if masked_count:
        visible.append(
            {
                "kind": "masked_summary",
                "representation": "masked",
                "content": (
                    f"{masked_count} earlier records are hidden and unavailable as evidence."
                ),
            }
        )
    return visible


def prepare_request_template(
    prefix: Mapping[str, Any],
    projection: Mapping[str, Any],
    request: Mapping[str, Any],
    method_id: str,
    prompt_protocol: str = PROMPT_PROTOCOL_V1,
) -> tuple[dict[str, Any], dict[str, str]]:
    rows, opaque = _record_rows(prefix, projection, method_id)
    if prompt_protocol == PROMPT_PROTOCOL_V1:
        system = (
            "You are checking an artificial tool-use record. Use only the records shown. "
            "Do not infer facts from record IDs. For a current request, use current facts; "
            "for a history question, explain past facts without treating them as current. "
            "Cite every record needed for the answer and call submit_answer exactly once."
        )
        tool = _ANSWER_TOOL
        visible_opaque = opaque
    elif prompt_protocol == PROMPT_PROTOCOL_V2:
        rows = _rows_for_prompt_v2(rows)
        visible_ids = [
            str(row["record_id"])
            for row in rows
            if isinstance(row.get("record_id"), str)
            and str(row["record_id"]).startswith("R")
        ]
        system = (
            "You are checking an artificial tool-use record. Use only visible records. "
            "Hidden records are unavailable and must never be cited. Do not infer facts "
            "from record IDs. For a current request, use current facts; for a history "
            "question, explain past facts without treating them as current. Cite every "
            "record needed for the answer. Keep the answer within four short sentences "
            f"and {ANSWER_MAX_CHARS_V2} characters. Never copy padding, long repeated "
            "characters, blobs, or placeholder details. Call submit_answer exactly once."
        )
        tool = _answer_tool_v2(visible_ids)
        visible_set = set(visible_ids)
        visible_opaque = {
            event_id: record_id
            for event_id, record_id in opaque.items()
            if record_id in visible_set
        }
    else:
        raise ValueError(f"unsupported answer prompt protocol: {prompt_protocol}")
    user = json.dumps(
        {"records": rows, "request": dict(request)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    template = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "tools": [tool],
        "tool_choice": {
            "type": "function",
            "function": {"name": "submit_answer"},
        },
        "stream": False,
    }
    return template, visible_opaque


def provider_request(
    template: Mapping[str, Any],
    model: str,
    model_config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "model": model,
        **dict(template),
        "temperature": int(model_config["temperature"]),
        "enable_thinking": bool(model_config["enable_thinking"]),
        "max_tokens": int(model_config["max_output_tokens"]),
    }


def prepare_live_trials(config: Mapping[str, Any], input_root: Path) -> list[dict[str, Any]]:
    prefixes = load_jsonl(input_root / "prefixes.jsonl")
    forks = load_jsonl(input_root / "forks.jsonl")
    outputs = load_jsonl(input_root / "manager_outputs.jsonl")
    prefix_by_id = {str(item["prefix_id"]): item for item in prefixes}
    fork_by_id = {str(item["fork_id"]): item for item in forks}
    output_by_key = {
        (str(item["prefix_id"]), str(item["fork_id"]), str(item["manager_id"])): item
        for item in outputs
        if str(item["manager_id"]) in LIVE_METHOD_IDS[:-1]
    }
    selected = tuple(map(str, config["sample_prefix_ids"]))
    sample_role = str(config.get("sample_role", "pilot"))
    if sample_role == "pilot":
        expected_sample = choose_pilot_prefix_ids(tuple(prefix_by_id))
    elif sample_role == "confirmation":
        expected_sample = choose_confirmation_prefix_ids(tuple(prefix_by_id))
    else:
        raise ValueError(f"unsupported live sample role: {sample_role}")
    if selected != expected_sample:
        raise ValueError("configured sample differs from its frozen stratified schedule")
    rows: list[dict[str, Any]] = []
    for prefix_id in selected:
        prefix = prefix_by_id[prefix_id]
        prefix_forks = sorted(
            (item for item in forks if item["prefix_id"] == prefix_id),
            key=lambda item: FORK_TYPES.index(str(item["fork_type"])),
        )
        for fork in prefix_forks:
            fork_id = str(fork["fork_id"])
            if fork_by_id[fork_id] is not fork:
                raise ValueError(f"duplicate fork id: {fork_id}")
            m5_projection = output_by_key[(prefix_id, fork_id, "M5_lifecycle_causal_reactivation")]
            for method_id in LIVE_METHOD_IDS:
                projection = (
                    m5_projection
                    if method_id == "M6_fixed_handoff"
                    else output_by_key[(prefix_id, fork_id, method_id)]
                )
                template, opaque = prepare_request_template(
                    prefix,
                    projection,
                    fork["request"],
                    method_id,
                    str(config.get("prompt_protocol", PROMPT_PROTOCOL_V1)),
                )
                trial_id = f"{prefix_id}:{fork['fork_type']}:{method_id}"
                rows.append(
                    {
                        "schema_version": "phase6_live_request_v1",
                        "trial_id": trial_id,
                        "prefix_id": prefix_id,
                        "fork_id": fork_id,
                        "fork_type": fork["fork_type"],
                        "scenario_family": prefix["scenario_family"],
                        "variant_id": prefix["variant_id"],
                        "method_id": method_id,
                        "prefix_hash": prefix["prefix_hash"],
                        "manager_output_hash": (
                            stable_digest(
                                {
                                    "method_id": method_id,
                                    "prefix_hash": prefix["prefix_hash"],
                                    "records": json.loads(template["messages"][1]["content"])[
                                        "records"
                                    ],
                                }
                            )
                            if method_id == "M6_fixed_handoff"
                            else projection["manager_output_hash"]
                        ),
                        "request_template": template,
                        "request_template_sha256": stable_digest(template),
                        "estimated_input_tokens": estimate_tokens(
                            provider_request(template, config["model"]["primary"], config["model"])
                        ),
                        "opaque_event_ids": {
                            opaque[event_id]: event_id for event_id in sorted(opaque)
                        },
                    }
                )
    if len(rows) != int(config["limits"]["trial_count"]):
        raise ValueError(f"prepared {len(rows)} live trials instead of 216")
    if len({item["trial_id"] for item in rows}) != len(rows):
        raise ValueError("duplicate live trial ids")
    maximum = int(config["limits"]["request_input_tokens_hard_max"])
    if max(int(item["estimated_input_tokens"]) for item in rows) > maximum:
        raise ValueError("a live request exceeds the fixed per-request input limit")
    return rows


def parse_submit_answer(
    response: Mapping[str, Any],
    *,
    maximum_answer_chars: int | None = None,
) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("provider response must contain exactly one choice")
    message = choices[0].get("message", {})
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("model must call submit_answer exactly once")
    function = calls[0].get("function", {})
    if function.get("name") != "submit_answer":
        raise ValueError("model called an unexpected tool")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError("submit_answer arguments must be a JSON object")
    answer = arguments.get("answer")
    evidence = arguments.get("evidence_record_ids")
    scope = arguments.get("fact_scope")
    repeat = arguments.get("would_repeat_side_effect")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("submit_answer requires a non-empty answer")
    if maximum_answer_chars is not None and len(answer.strip()) > maximum_answer_chars:
        raise ValueError("submit_answer answer exceeds the fixed character limit")
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ValueError("submit_answer evidence_record_ids must be strings")
    if scope not in {"current", "historical"} or not isinstance(repeat, bool):
        raise ValueError("submit_answer contains an invalid scope or side-effect flag")
    return {
        "answer": answer.strip(),
        "evidence_record_ids": sorted(set(evidence)),
        "fact_scope": scope,
        "would_repeat_side_effect": repeat,
    }


def smoke_tool_contract_valid(answer: Mapping[str, Any]) -> bool:
    """Check tool-call availability without imposing task-quality scoring."""

    return bool(
        str(answer.get("answer", "")).strip()
        and "R001" in answer.get("evidence_record_ids", ())
        and answer.get("fact_scope") == "current"
        and answer.get("would_repeat_side_effect") is False
    )


# Imported after definitions so mutually-referential helpers initialize safely.
from .live_config import (
    choose_confirmation_prefix_ids as choose_confirmation_prefix_ids,
    choose_pilot_prefix_ids as choose_pilot_prefix_ids,
    load_jsonl as load_jsonl,
)
