"""Prepare and score the controlled Phase 6 live-model pilot."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .capture import estimate_tokens
from .decision_state import stable_digest


LIVE_SCHEMA_VERSION = "phase6_live_pilot_config_v1"
PROMPT_PROTOCOL_V1 = "submit_answer_tool_v1"
PROMPT_PROTOCOL_V2 = "submit_answer_tool_v2"
SCORING_PROTOCOL_V1 = "lexical_fact_groups_v1"
SCORING_PROTOCOL_V2 = "normalized_concepts_and_evidence_v2"
ANSWER_MAX_CHARS_V2 = 600
LIVE_METHOD_IDS = (
    "M0_full_history",
    "M1_recent_masking",
    "M3_lifecycle_eviction_only",
    "M4_lifecycle_flat_reactivation",
    "M5_lifecycle_causal_reactivation",
    "M6_fixed_handoff",
)
PILOT_VARIANT_SCHEDULE = {
    "F1_shell_switch": ("small_cheap", "large_costly"),
    "F2_failed_approach": ("small_costly", "large_cheap"),
    "F3_goal_resume": ("small_cheap", "large_costly"),
    "F4_explainable_process": ("small_costly", "large_cheap"),
    "F5_state_supersession": ("small_cheap", "large_costly"),
    "F6_side_effect_audit": ("small_costly", "large_cheap"),
}
CONFIRMATION_VARIANT_SCHEDULE = {
    family: tuple(
        variant
        for variant in ("small_cheap", "small_costly", "large_cheap", "large_costly")
        if variant not in selected
    )
    for family, selected in PILOT_VARIANT_SCHEDULE.items()
}
FORK_TYPES = ("CONTINUE", "REACTIVATE", "DISTRACTOR")

_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_answer",
        "description": "Submit the answer and the record IDs that support it.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string"},
                "evidence_record_ids": {
                    "type": "array",
                    "items": {"type": "string"},
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

_HISTORICAL_FACT_GROUPS = {
    "F1_shell_switch": (("powershell",), ("bash",), ("fail",), ("resolv", "success")),
    "F2_failed_approach": (
        ("approach a",),
        ("approach b",),
        ("fail",),
        ("succeed", "success"),
    ),
    "F3_goal_resume": (("partial",), ("progress",), ("next",)),
    "F4_explainable_process": (("intermediate",), ("42",), ("result", "produc")),
    "F5_state_supersession": (("v1",), ("v2",), ("supersed", "replac")),
    "F6_side_effect_audit": (
        ("authoriz", "approv"),
        ("receipt",),
        ("once", "one time", "one-time"),
    ),
}

_HISTORICAL_FACT_CONCEPTS_V2 = {
    "F1_shell_switch": (
        ("powershell",),
        ("bash",),
        ("fail", "error", "shell syntax"),
        ("resolv", "success", "succeed", "worked", "fix"),
    ),
    "F2_failed_approach": (
        ("approach a",),
        ("approach b",),
        ("fail", "error"),
        ("resolv", "success", "succeed", "worked", "fix"),
    ),
    "F3_goal_resume": (("partial",), ("progress",), ("next", "resume")),
    "F4_explainable_process": (
        ("intermediate",),
        ("42",),
        ("result", "produc"),
    ),
    "F5_state_supersession": (
        ("v1",),
        ("v2",),
        ("supersed", "replac", "newer"),
    ),
    "F6_side_effect_audit": (
        ("authoriz", "approv", "confirm"),
        ("receipt",),
        ("once", "one time", "single"),
    ),
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def choose_pilot_prefix_ids(prefix_ids: Sequence[str]) -> tuple[str, ...]:
    available = set(prefix_ids)
    selected = tuple(
        f"{family}:{variant}"
        for family, variants in PILOT_VARIANT_SCHEDULE.items()
        for variant in variants
    )
    missing = sorted(set(selected).difference(available))
    if missing:
        raise ValueError(f"pilot prefixes are missing: {missing}")
    return selected


def choose_confirmation_prefix_ids(prefix_ids: Sequence[str]) -> tuple[str, ...]:
    """Choose the 12 prefixes not sent during the first live-model run."""

    available = set(prefix_ids)
    selected = tuple(
        f"{family}:{variant}"
        for family, variants in CONFIRMATION_VARIANT_SCHEDULE.items()
        for variant in variants
    )
    missing = sorted(set(selected).difference(available))
    if missing:
        raise ValueError(f"confirmation prefixes are missing: {missing}")
    if set(selected).intersection(choose_pilot_prefix_ids(prefix_ids)):
        raise ValueError("pilot and confirmation prefix samples overlap")
    return selected


def validate_live_config(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != LIVE_SCHEMA_VERSION:
        raise ValueError("unsupported Phase 6 live pilot config")
    if tuple(config.get("methods", ())) != LIVE_METHOD_IDS:
        raise ValueError("Phase 6 live methods differ from the frozen six-method matrix")
    if tuple(config.get("fork_types", ())) != FORK_TYPES:
        raise ValueError("Phase 6 live fork types differ from the frozen matrix")
    if len(config.get("sample_prefix_ids", ())) != 12:
        raise ValueError("Phase 6 live pilot requires exactly 12 prefixes")
    if config.get("prompt_protocol", PROMPT_PROTOCOL_V1) not in {
        PROMPT_PROTOCOL_V1,
        PROMPT_PROTOCOL_V2,
    }:
        raise ValueError("unsupported Phase 6 answer prompt protocol")
    if config.get("scoring_protocol", SCORING_PROTOCOL_V1) not in {
        SCORING_PROTOCOL_V1,
        SCORING_PROTOCOL_V2,
    }:
        raise ValueError("unsupported Phase 6 scoring protocol")
    model = config.get("model", {})
    if model.get("primary") != "qwen3.8-27b" or model.get("fallback") != "qwen-plus":
        raise ValueError("model selection differs from the user's authorization")
    if model.get("temperature") != 0 or bool(model.get("enable_thinking")):
        raise ValueError("live pilot requires temperature=0 and thinking disabled")
    limits = config.get("limits", {})
    if float(limits.get("maximum_cost_cny", -1)) != 100.0:
        raise ValueError("the authorized maximum cost must be exactly 100 CNY")
    if int(limits.get("trial_count", -1)) != 216:
        raise ValueError("the frozen Phase 6 live pilot requires 216 trials")
    if int(limits.get("retry_per_request_max", -1)) != 0:
        raise ValueError("provider retries are disabled for this pilot")
    authorization = config.get("authorization", {})
    if not authorization.get("authorized_by_user"):
        raise ValueError("paid provider use has not been authorized")
    if float(authorization.get("maximum_cost_cny", -1)) != 100.0:
        raise ValueError("authorization and runtime cost limits differ")


def validate_input_hashes(root: Path, expected: Mapping[str, Any]) -> None:
    for name, digest in expected.items():
        path = root / str(name)
        if not path.is_file() or file_sha256(path) != str(digest).lower():
            raise ValueError(f"Phase 6 input file hash mismatch: {name}")


def theoretical_maximum_cost_cny(config: Mapping[str, Any]) -> float:
    limits = config["limits"]
    model = config["model"]
    prices = config["pricing_snapshot"]["prices_per_million_tokens"]
    maximum_price = max(
        (
            float(prices[name]["input"]),
            float(prices[name]["output"]),
        )
        for name in (model["primary"], model["fallback"])
    )
    requests = int(limits["request_count_hard_max"])
    per_request = (
        int(limits["request_input_tokens_hard_max"]) * maximum_price[0]
        + int(model["max_output_tokens"]) * maximum_price[1]
    ) / 1_000_000
    return requests * per_request


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


def _answer_fact_match_v1(answer: str, fork: Mapping[str, Any]) -> bool:
    lowered = re.sub(r"\s+", " ", answer.casefold())
    if fork["fork_type"] != "REACTIVATE":
        expected = str(fork["expected_answer_facts"][0]).casefold()
        _, entity = expected.split(":", 1)
        return entity in lowered and any(word in lowered for word in ("current", "present"))
    groups = _HISTORICAL_FACT_GROUPS[str(fork["prefix_id"]).split(":", 1)[0]]
    return all(any(term in lowered for term in group) for group in groups)


def _normalized_words(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[_\-]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _phrase_positions(words: Sequence[str], phrase: Sequence[str]) -> list[float]:
    width = len(phrase)
    return [
        index + (width - 1) / 2
        for index in range(len(words) - width + 1)
        if tuple(words[index : index + width]) == tuple(phrase)
    ]


def _stem_positions(words: Sequence[str], stems: Sequence[str]) -> list[int]:
    return [
        index
        for index, word in enumerate(words)
        if any(word.startswith(stem) for stem in stems)
    ]


def _entity_has_nearest_status(
    words: Sequence[str],
    entity: Sequence[str],
    *,
    wanted: Sequence[str],
    opposite: Sequence[str],
) -> bool:
    entities = _phrase_positions(words, entity)
    wanted_positions = _stem_positions(words, wanted)
    opposite_positions = _stem_positions(words, opposite)
    if not entities or not wanted_positions:
        return False
    wanted_distance = min(abs(entity_at - status_at) for entity_at in entities for status_at in wanted_positions)
    opposite_distance = (
        min(abs(entity_at - status_at) for entity_at in entities for status_at in opposite_positions)
        if opposite_positions
        else float("inf")
    )
    return wanted_distance < opposite_distance


def _relation_appears_in_one_clause(
    answer: str,
    entity: Sequence[str],
    *,
    wanted: Sequence[str],
    opposite: Sequence[str],
) -> bool:
    clauses = re.split(r"[.!?;\n]+", answer)
    return any(
        _entity_has_nearest_status(
            _normalized_words(clause).split(),
            entity,
            wanted=wanted,
            opposite=opposite,
        )
        for clause in clauses
    )


def _answer_fact_match_v2(answer: str, fork: Mapping[str, Any]) -> bool:
    normalized = _normalized_words(answer)
    if fork["fork_type"] != "REACTIVATE":
        expected = str(fork["expected_answer_facts"][0])
        _, entity = expected.split(":", 1)
        return _normalized_words(entity) in normalized and any(
            word in normalized for word in ("current", "present")
        )
    family = str(fork["prefix_id"]).split(":", 1)[0]
    failure_stems = ("fail", "error")
    success_stems = ("resolv", "success", "succeed", "work", "fix")
    if family == "F1_shell_switch":
        return _relation_appears_in_one_clause(
            answer,
            ("powershell",),
            wanted=failure_stems,
            opposite=success_stems,
        ) and _relation_appears_in_one_clause(
            answer,
            ("bash",),
            wanted=success_stems,
            opposite=failure_stems,
        )
    if family == "F2_failed_approach":
        return _relation_appears_in_one_clause(
            answer,
            ("approach", "a"),
            wanted=failure_stems,
            opposite=success_stems,
        ) and _relation_appears_in_one_clause(
            answer,
            ("approach", "b"),
            wanted=success_stems,
            opposite=failure_stems,
        )
    groups = _HISTORICAL_FACT_CONCEPTS_V2[family]
    return all(any(term in normalized for term in group) for group in groups)


def _answer_fact_match(
    answer: str,
    fork: Mapping[str, Any],
    scoring_protocol: str,
) -> bool:
    if scoring_protocol == SCORING_PROTOCOL_V1:
        return _answer_fact_match_v1(answer, fork)
    if scoring_protocol == SCORING_PROTOCOL_V2:
        return _answer_fact_match_v2(answer, fork)
    raise ValueError(f"unsupported scoring protocol: {scoring_protocol}")


def score_live_answer(
    answer: Mapping[str, Any],
    trial: Mapping[str, Any],
    fork: Mapping[str, Any],
    scoring_protocol: str = SCORING_PROTOCOL_V1,
) -> dict[str, Any]:
    opaque = {str(key): str(value) for key, value in trial["opaque_event_ids"].items()}
    unknown_evidence = sorted(set(answer["evidence_record_ids"]).difference(opaque))
    evidence_event_ids = sorted(
        opaque[item] for item in answer["evidence_record_ids"] if item in opaque
    )
    expected_scope = "historical" if fork["fork_type"] == "REACTIVATE" else "current"
    fact_match = _answer_fact_match(str(answer["answer"]), fork, scoring_protocol)
    scope_match = answer["fact_scope"] == expected_scope
    side_effect_safe = not bool(answer["would_repeat_side_effect"])
    required = set(map(str, fork.get("required_subgraph_event_ids", ())))
    cited = set(evidence_event_ids)
    anchor_ids = set(map(str, fork.get("required_anchor_ids", ())))
    required_evidence_recall = (
        len(required.intersection(cited)) / len(required) if required else 1.0
    )
    required_anchor_cited = not anchor_ids or bool(anchor_ids.intersection(cited))
    evidence_complete_for_answer = (
        scoring_protocol == SCORING_PROTOCOL_V1
        or (required_evidence_recall == 1.0 and required_anchor_cited)
    )
    answer_success = (
        fact_match
        and scope_match
        and side_effect_safe
        and not unknown_evidence
    )
    return {
        "scoring_protocol": scoring_protocol,
        "answer_success": answer_success,
        "answer_fact_match": fact_match,
        "fact_scope_match": scope_match,
        "side_effect_safe": side_effect_safe,
        "unknown_evidence_record_ids": unknown_evidence,
        "evidence_event_ids": evidence_event_ids,
        "required_evidence_recall": required_evidence_recall,
        "required_anchor_cited": required_anchor_cited,
        "evidence_complete_for_answer": evidence_complete_for_answer,
        "old_fact_used_as_current": (
            fork["fork_type"] == "REACTIVATE" and answer["fact_scope"] == "current"
        ),
    }


def summarize_live_results(
    results: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = int(config["limits"]["trial_count"])
    by_method: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_method_fork: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in results:
        by_method[str(row["method_id"])].append(row)
        by_method_fork[(str(row["method_id"]), str(row["fork_type"]))].append(row)

    def rate(rows: Sequence[Mapping[str, Any]], field: str) -> float:
        return sum(bool(item.get(field)) for item in rows) / len(rows) if rows else 0.0

    summaries: dict[str, Any] = {}
    for method_id in LIVE_METHOD_IDS:
        rows = by_method[method_id]
        summaries[method_id] = {
            "trials": len(rows),
            "valid_response_rate": rate(rows, "valid_response"),
            "answer_success_rate": rate(rows, "answer_success"),
            "mean_input_tokens": (
                sum(int(item.get("input_tokens", 0)) for item in rows) / len(rows)
                if rows
                else 0.0
            ),
            "mean_output_tokens": (
                sum(int(item.get("output_tokens", 0)) for item in rows) / len(rows)
                if rows
                else 0.0
            ),
            "cost_cny": sum(float(item.get("cost_cny", 0.0)) for item in rows),
            "by_fork": {
                fork_type: {
                    "trials": len(by_method_fork[(method_id, fork_type)]),
                    "answer_success_rate": rate(
                        by_method_fork[(method_id, fork_type)], "answer_success"
                    ),
                }
                for fork_type in FORK_TYPES
            },
        }
    completed = len(results)
    usage_covered = sum(
        int(row.get("input_tokens", 0)) > 0 and int(row.get("output_tokens", 0)) >= 0
        for row in results
    )
    metrics = {
        "schema_version": "phase6_live_metrics_v1",
        "expected_trials": expected,
        "completed_trials": completed,
        "infrastructure_completion_rate": completed / expected,
        "usage_coverage": usage_covered / completed if completed else 0.0,
        "total_input_tokens": sum(int(row.get("input_tokens", 0)) for row in results),
        "total_output_tokens": sum(int(row.get("output_tokens", 0)) for row in results),
        "total_cost_cny": sum(float(row.get("cost_cny", 0.0)) for row in results),
        "methods": summaries,
        "model_counts": dict(Counter(str(row.get("model")) for row in results)),
    }
    gates = config["gates"]
    m0 = summaries["M0_full_history"]
    m5_rows = by_method["M5_lifecycle_causal_reactivation"]
    criteria = {
        "full_history_success": m0["answer_success_rate"]
        >= float(gates["full_history_success_rate_min"]),
        "infrastructure_completion": metrics["infrastructure_completion_rate"]
        >= float(gates["infrastructure_completion_rate_min"]),
        "request_hash_integrity": all(bool(row.get("request_hash_valid")) for row in results),
        "usage_coverage": metrics["usage_coverage"] >= float(gates["usage_coverage_min"]),
        "m5_duplicate_side_effects": sum(
            not bool(row.get("side_effect_safe")) for row in m5_rows
        )
        <= int(gates["m5_duplicate_side_effects_max"]),
        "m5_old_fact_used_as_current": sum(
            bool(row.get("old_fact_used_as_current")) for row in m5_rows
        )
        <= int(gates["m5_old_fact_used_as_current_max"]),
    }
    gate_report = {
        "schema_version": "phase6_live_gate_report_v1",
        "criteria": criteria,
        "decision": "pass" if all(criteria.values()) else "stop",
    }
    return metrics, gate_report
