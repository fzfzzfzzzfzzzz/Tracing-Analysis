"""Definitions moved from ``tracegraph.compression_audit_runtime``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from ...capture import estimate_tokens
from ...message_protocol import close_message_protocol
from ...reactivation import detect_reactivation_trigger, tokenize_terms
from ...compression_audit import BENCHMARK_ID, SCHEMA_VERSION, ContextBundle, EpisodeRecord, FailureChainGold, MemoryArtifact, MemoryState, PrefixRecord, QueryRecord, canonical_json, file_sha256, git_provenance, implementation_provenance, load_config, load_jsonl, stable_digest, verify_file_manifest, write_file_manifest





def parse_submit_answer(response: Mapping[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("provider response must contain exactly one choice")
    calls = choices[0].get("message", {}).get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("provider must emit exactly one tool call")
    function = calls[0].get("function", {})
    if function.get("name") != "submit_audit_answer":
        raise ValueError("provider did not call submit_audit_answer")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError("submit_audit_answer arguments must be an object")
    required = (
        "failed_action",
        "failed_arguments",
        "failure_cause",
        "diagnostic_evidence",
        "switch_decision",
        "replacement_action",
        "replacement_arguments",
        "resolution_evidence",
        "ordered_evidence_ids",
        "current_fact",
        "evidence_event_ids",
        "insufficient_history",
        "would_repeat_side_effect",
    )
    if any(key not in arguments for key in required):
        raise ValueError("submit_audit_answer omitted required structured fields")
    if not isinstance(arguments["evidence_event_ids"], list):
        raise ValueError("evidence_event_ids must be a list")
    if not isinstance(arguments["ordered_evidence_ids"], list):
        raise ValueError("ordered_evidence_ids must be a list")
    if not isinstance(arguments["failed_arguments"], dict):
        raise ValueError("failed_arguments must be an object")
    if not isinstance(arguments["replacement_arguments"], dict):
        raise ValueError("replacement_arguments must be an object")
    if not isinstance(arguments["insufficient_history"], bool):
        raise ValueError("insufficient_history must be boolean")
    if not isinstance(arguments["would_repeat_side_effect"], bool):
        raise ValueError("would_repeat_side_effect must be boolean")
    for name in (
        "failed_action", "failure_cause", "diagnostic_evidence", "switch_decision",
        "replacement_action", "resolution_evidence", "current_fact",
    ):
        if not isinstance(arguments[name], str):
            raise ValueError(f"{name} must be a string")
    for name in ("ordered_evidence_ids", "evidence_event_ids"):
        if not all(isinstance(item, str) for item in arguments[name]):
            raise ValueError(f"{name} must contain string IDs")
        if len(set(arguments[name])) != len(arguments[name]):
            raise ValueError(f"{name} contains duplicate evidence IDs")
    return dict(arguments)


def asdict_without_none(value: Any) -> dict[str, Any]:
    return {key: item for key, item in asdict(value).items() if item is not None}
