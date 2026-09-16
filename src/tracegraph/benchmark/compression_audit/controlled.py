"""Definitions moved from ``tracegraph.compression_audit``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from ...capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens

from .constants import (
    CONTEXT_VARIANTS as CONTEXT_VARIANTS,
    DOMAINS as DOMAINS,
    FAILURE_ROLES as FAILURE_ROLES,
    FAILURE_TEMPLATES as FAILURE_TEMPLATES,
    RECOVERABILITY_LEVELS as RECOVERABILITY_LEVELS,
)



def _split_for_family(index: int) -> str:
    if index < 2:
        return "dev"
    if index < 4:
        return "validation"
    return "test"


def _tool_schema(name: str, *, side_effect: bool = False) -> dict[str, Any]:
    description = f"Controlled benchmark tool {name}."
    if side_effect:
        description += " This operation can create a non-idempotent side effect."
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entity": {"type": "string"},
                    "operation": {"type": "string"},
                },
                "required": ["entity", "operation"],
            },
        },
    }


def _event(
    prefix_id: str,
    index: int,
    *,
    kind: str,
    content: Any,
    causal_role: str,
    call_id: str | None = None,
    tool_name: str | None = None,
    side_effect: bool = False,
) -> dict[str, Any]:
    value = {
        "event_id": f"{prefix_id}:E{index:03d}",
        "step_id": index,
        "kind": kind,
        "content": content,
        "causal_role": causal_role,
        "token_count": estimate_tokens(content),
        "side_effect": side_effect,
    }
    if call_id is not None:
        value["call_id"] = call_id
    if tool_name is not None:
        value["tool_name"] = tool_name
    return value


def _controlled_prefix(
    template: Mapping[str, Any],
    family_index: int,
    domain: Mapping[str, str],
    recoverability: str,
    context: Mapping[str, Any],
    *,
    base_seed: int,
) -> tuple[PrefixRecord, FailureChainGold, tuple[QueryRecord, ...]]:
    prefix_id = ":".join(
        (
            "controlled",
            str(template["id"]),
            str(domain["id"]),
            recoverability,
            str(context["id"]),
        )
    )
    entity = f"{domain['entity']}-{family_index + 1:02d}"
    failed = f"{domain['id']}_{template['failed']}"
    replacement = f"{domain['id']}_{template['replacement']}"
    failed_args = {"entity": entity, "operation": "attempt"}
    replacement_args = {"entity": entity, "operation": "recover"}
    failed_call = f"{prefix_id}:call:failed"
    replacement_call = f"{prefix_id}:call:replacement"
    side_effect = bool(template.get("side_effect")) or recoverability == "R3"

    events = [
        _event(
            prefix_id,
            1,
            kind="goal",
            content=f"Complete the historical task for {entity}.",
            causal_role="historical_goal",
        ),
        _event(
            prefix_id,
            2,
            kind="tool_call",
            content={"tool_name": failed, "arguments": failed_args},
            causal_role="failed_action",
            call_id=failed_call,
            tool_name=failed,
            side_effect=side_effect,
        ),
        _event(
            prefix_id,
            3,
            kind="error",
            content={
                "error": template["error"],
                "detail": template["diagnostic"],
            },
            causal_role="failure_result",
            call_id=failed_call,
            tool_name=failed,
        ),
        _event(
            prefix_id,
            4,
            kind="decision",
            content=template["diagnostic"],
            causal_role="diagnostic_evidence",
        ),
        _event(
            prefix_id,
            5,
            kind="decision",
            content=template["switch"],
            causal_role="switch_decision",
        ),
        _event(
            prefix_id,
            6,
            kind="tool_call",
            content={"tool_name": replacement, "arguments": replacement_args},
            causal_role="replacement_action",
            call_id=replacement_call,
            tool_name=replacement,
        ),
        _event(
            prefix_id,
            7,
            kind="observation",
            content={"status": "success", "detail": template["resolution"]},
            causal_role="resolution_evidence",
            call_id=replacement_call,
            tool_name=replacement,
        ),
    ]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Complete the historical task for {entity}."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": failed_call,
                    "type": "function",
                    "function": {
                        "name": failed,
                        "arguments": canonical_json(failed_args),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": failed_call,
            "content": canonical_json(
                {"error": template["error"], "detail": template["diagnostic"]}
            ),
        },
        {"role": "assistant", "content": template["diagnostic"]},
        {"role": "assistant", "content": template["switch"]},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": replacement_call,
                    "type": "function",
                    "function": {
                        "name": replacement,
                        "arguments": canonical_json(replacement_args),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": replacement_call,
            "content": canonical_json(
                {"status": "success", "detail": template["resolution"]}
            ),
        },
    ]

    if template["id"] == "multi_failure_recovery":
        retry_events = []
        retry_messages = []
        for retry_index in (1, 2):
            retry_call_id = f"{prefix_id}:call:retry-{retry_index}"
            retry_args = {"entity": entity, "operation": f"retry-{retry_index}"}
            retry_error = {"error": template["error"], "detail": "The same strategy failed again."}
            retry_events.extend((
                _event(prefix_id, 1, kind="tool_call", content={"tool_name": failed, "arguments": retry_args}, causal_role="failed_retry_action", call_id=retry_call_id, tool_name=failed, side_effect=side_effect),
                _event(prefix_id, 1, kind="error", content=retry_error, causal_role="failed_retry_result", call_id=retry_call_id, tool_name=failed),
            ))
            retry_messages.extend((
                {"role": "assistant", "content": "", "tool_calls": [{"id": retry_call_id, "type": "function", "function": {"name": failed, "arguments": canonical_json(retry_args)}}]},
                {"role": "tool", "tool_call_id": retry_call_id, "content": canonical_json(retry_error)},
            ))
        events[3:3] = retry_events
        messages[3:3] = retry_messages
        for index, event in enumerate(events, 1):
            event["event_id"] = f"{prefix_id}:E{index:03d}"
            event["step_id"] = index
    historical_count = len(events)
    filler_index = historical_count + 1
    current_tokens = estimate_tokens(messages)
    target_tokens = int(context["target_tokens"])
    while current_tokens < target_tokens:
        remaining = target_tokens - current_tokens
        words = max(8, min(96, remaining * 3))
        content = (
            f"Independent current-status observation {filler_index}: "
            + " ".join(f"neutral{filler_index}" for _ in range(words))
        )
        events.append(
            _event(
                prefix_id,
                filler_index,
                kind="observation",
                content=content,
                causal_role="distractor",
            )
        )
        messages.append({"role": "assistant", "content": content})
        filler_index += 1
        current_tokens = estimate_tokens(messages)

    current_text = f"Current state for {entity}: {domain['current']}."
    current_event = _event(
        prefix_id,
        filler_index,
        kind="observation",
        content=current_text,
        causal_role="current_fact",
    )
    events.append(current_event)
    messages.append({"role": "user", "content": f"Continue current work for {entity}."})
    messages.append({"role": "assistant", "content": current_text})
    for ordinal, event in enumerate(events[:-1], 1):
        event["source_message_ordinal"] = ordinal
    current_event["source_message_ordinal"] = len(messages)

    if recoverability == "R0":
        allowed_tools: tuple[str, ...] = ()
        minimum_calls = None
    elif recoverability == "R1":
        allowed_tools = ("read_audit_log",)
        minimum_calls = 1
    elif recoverability == "R2":
        allowed_tools = ("inspect_environment", "replay_in_sandbox", "read_audit_log")
        minimum_calls = 3
    else:
        allowed_tools = ("read_audit_log", "simulate_replay", "repeat_failed_action")
        minimum_calls = 1

    split = _split_for_family(family_index)
    snapshot = {
        "snapshot_id": stable_digest(
            {"prefix_id": prefix_id, "seed": base_seed, "recoverability": recoverability}
        ),
        "deterministic": True,
        "allowed_reacquisition_tools": list(allowed_tools),
        "minimum_reacquisition_calls": minimum_calls,
        "history_reconstructable": recoverability != "R0",
        "unsafe_to_repeat_failed_action": recoverability == "R3",
        "side_effects_sandboxed": True,
    }
    prefix = PrefixRecord(
        prefix_id=prefix_id,
        source_kind="controlled_synthetic",
        source_ref={
            "generator": "compression_audit.controlled_v1",
            "base_seed": base_seed,
            "template": template["id"],
        },
        split=split,
        failure_family=str(template["id"]),
        task_domain=str(domain["id"]),
        recoverability=recoverability,
        context_length=str(context["id"]),
        budget_tokens=int(context["budget_tokens"]),
        events=tuple(events),
        messages=tuple(messages),
        tool_schemas=tuple(
            [
                _tool_schema(failed, side_effect=side_effect),
                _tool_schema(replacement),
                *(_tool_schema(name) for name in allowed_tools),
            ]
        ),
        environment_snapshot=snapshot,
    )
    historical = events[:historical_count]
    role_ids = {
        role: tuple(event["event_id"] for event in historical if event["causal_role"] == role)
        for role in FAILURE_ROLES
    }
    ordered = tuple(event["event_id"] for event in historical[1:])
    evidence = {
        "failed_action": role_ids["failed_action"],
        "failed_arguments": role_ids["failed_action"],
        "error_signature": role_ids["failure_result"],
        "failure_cause": role_ids["failure_result"] + role_ids["diagnostic_evidence"],
        "diagnostic_evidence": role_ids["failure_result"] + role_ids["diagnostic_evidence"],
        "switch_decision": role_ids["switch_decision"],
        "replacement_action": role_ids["replacement_action"],
        "replacement_arguments": role_ids["replacement_action"],
        "resolution_evidence": role_ids["resolution_evidence"],
        "ordered_event_ids": ordered,
        "current_fact": (current_event["event_id"],),
    }
    gold = FailureChainGold(
        prefix_id=prefix_id,
        failed_action=failed,
        failed_arguments=failed_args,
        error_signature=str(template["error"]),
        diagnostic_evidence=str(template["diagnostic"]),
        switch_decision=str(template["switch"]),
        replacement_action=replacement,
        replacement_arguments=replacement_args,
        resolution_evidence=str(template["resolution"]),
        ordered_event_ids=ordered,
        evidence_by_field=evidence,
        recoverability=recoverability,
        current_fact=current_text,
        current_event_ids=(current_event["event_id"],),
        annotation={
            "gold_source": "deterministic_generator", "human_annotated": False,
            "failed_attempt_event_ids": [
                event["event_id"] for event in historical
                if event["causal_role"] in {"failed_action", "failed_retry_action"}
            ],
        },
    )
    queries = build_queries(prefix, interactive_tools=allowed_tools)
    return prefix, gold, queries


def build_queries(
    prefix: PrefixRecord, *, interactive_tools: Sequence[str] | None = None
) -> tuple[QueryRecord, ...]:
    tools = tuple(interactive_tools or prefix.environment_snapshot.get(
        "allowed_reacquisition_tools", ()
    ))
    specifications = (
        (
            "audit_failed_action",
            "audit_qa",
            "Which earlier method was first attempted and failed? Give its original recorded arguments "
            "and cite the supporting record IDs.",
            ("failed_action", "failed_arguments"),
            (),
        ),
        (
            "audit_failure_cause",
            "audit_qa",
            "Why did the earlier unsuccessful attempt fail? Use only the available record "
            "and cite the evidence.",
            ("failure_cause", "diagnostic_evidence"),
            (),
        ),
        (
            "audit_recovery",
            "audit_qa",
            "What change followed the failed attempt, which replacement worked, and what "
            "record proves success?",
            (
                "switch_decision",
                "replacement_action",
                "replacement_arguments",
                "resolution_evidence",
            ),
            (),
        ),
        (
            "audit_chain",
            "audit_qa",
            "Reconstruct the earlier failed-to-successful sequence in causal order and "
            "cite every necessary record.",
            (
                "failed_action",
                "failed_arguments",
                "failure_cause",
                "switch_decision",
                "replacement_action",
                "replacement_arguments",
                "resolution_evidence",
                "ordered_event_ids",
            ),
            (),
        ),
        (
            "interactive_reacquisition",
            "interactive_reacquisition",
            "Explain why the earlier method failed and what replacement succeeded. If the "
            "answer is not in memory, use only the permitted tools to verify it.",
            (
                "failed_action",
                "failed_arguments",
                "failure_cause",
                "replacement_action",
                "replacement_arguments",
                "resolution_evidence",
            ),
            tools,
        ),
        (
            "distractor_current",
            "distractor",
            "Report the current status only. Do not revisit unrelated historical work.",
            ("current_fact",),
            (),
        ),
    )
    return tuple(
        QueryRecord(
            query_id=f"{prefix.prefix_id}:{query_type}",
            prefix_id=prefix.prefix_id,
            track=track,
            query_type=query_type,
            text=text,
            allowed_tools=tuple(allowed),
            required_fields=tuple(required),
        )
        for query_type, track, text, required, allowed in specifications
    )


def generate_controlled_dataset(
    *, base_seed: int = 20260901
) -> tuple[list[PrefixRecord], list[FailureChainGold], list[QueryRecord]]:
    prefixes: list[PrefixRecord] = []
    gold: list[FailureChainGold] = []
    queries: list[QueryRecord] = []
    for family_index, template in enumerate(FAILURE_TEMPLATES):
        for domain in DOMAINS:
            for recoverability in RECOVERABILITY_LEVELS:
                for context in CONTEXT_VARIANTS:
                    prefix, item_gold, item_queries = _controlled_prefix(
                        template,
                        family_index,
                        domain,
                        recoverability,
                        context,
                        base_seed=base_seed,
                    )
                    prefixes.append(prefix)
                    gold.append(item_gold)
                    queries.extend(item_queries)
    return prefixes, gold, queries


# Imported after definitions so mutually-referential helpers initialize safely.
from .io import (
    canonical_json as canonical_json,
    stable_digest as stable_digest,
)

from .models import (
    FailureChainGold as FailureChainGold,
    PrefixRecord as PrefixRecord,
    QueryRecord as QueryRecord,
)
