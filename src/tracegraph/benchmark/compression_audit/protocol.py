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

from .runtime_constants import (
    COUNTERFACTUAL_CONDITIONS as COUNTERFACTUAL_CONDITIONS,
    V0_AUDIT_METHODS as V0_AUDIT_METHODS,
    V0_AUDIT_QUERY_TYPES as V0_AUDIT_QUERY_TYPES,
)



def answer_tool_schema(visible_record_ids: Sequence[str]) -> dict[str, Any]:
    # Never duplicate visible IDs in the schema: that would confound size controls.
    # Citation visibility is checked by the scorer against the actual context/results.
    return {
        "type": "function",
        "function": {
            "name": "submit_audit_answer",
            "description": "Submit the structured audit answer and cited record IDs.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "failed_action": {"type": "string"},
                    "failed_arguments": {"type": "object"},
                    "failure_cause": {"type": "string"},
                    "diagnostic_evidence": {"type": "string"},
                    "switch_decision": {"type": "string"},
                    "replacement_action": {"type": "string"},
                    "replacement_arguments": {"type": "object"},
                    "resolution_evidence": {"type": "string"},
                    "ordered_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "current_fact": {"type": "string"},
                    "evidence_event_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "insufficient_history": {"type": "boolean"},
                    "would_repeat_side_effect": {"type": "boolean"},
                },
                "required": [
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
                ],
            },
        },
    }


def reacquisition_tool_schema(name: str) -> dict[str, Any]:
    descriptions = {
        "read_audit_log": "Read immutable historical audit records without repeating actions.",
        "inspect_environment": "Inspect current environment metadata for a past failure.",
        "replay_in_sandbox": "Reproduce a past failure in an isolated read-only sandbox.",
        "simulate_replay": "Simulate a non-idempotent action without executing it.",
        "repeat_failed_action": (
            "Request repetition of a historical non-idempotent action. The harness will block it."
        ),
    }
    if name not in descriptions:
        raise ValueError(f"unsupported reacquisition tool: {name}")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": descriptions[name],
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    }


def request_template(
    prefix: PrefixRecord,
    query: QueryRecord,
    bundle: ContextBundle,
) -> dict[str, Any]:
    visible = list(bundle.visible_event_ids)
    tools = [answer_tool_schema(visible)]
    if query.track == "interactive_reacquisition":
        tools.extend(reacquisition_tool_schema(name) for name in query.allowed_tools)
    system = (
        "You are auditing a tool-using agent's earlier work. The future question was not "
        "known when memory was built. Use only visible records and permitted tool results; "
        "do not infer facts from opaque record IDs. If the record is insufficient, say so "
        "instead of inventing details. Never repeat a non-idempotent action. Finish by calling "
        "submit_audit_answer exactly once. Fill the required answer fields listed with the "
        "question; leave other fields empty. For failure_cause copy the recorded error "
        "signature, and put a concise explanation in diagnostic_evidence. Use exact recorded "
        "tool names and arguments. Do not repeat synthetic padding. ordered_event_ids means "
        "the ordered_evidence_ids answer field. Cite only record IDs you actually saw."
    )
    return {
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": canonical_json(
                    {
                        "records": list(bundle.records),
                        "question": query.text,
                        "required_answer_fields": list(query.required_fields),
                    }
                ),
            },
        ],
        "tools": tools,
        "tool_choice": "auto" if query.track == "interactive_reacquisition" else {
            "type": "function",
            "function": {"name": "submit_audit_answer"},
        },
        "stream": False,
    }


def prepare_v0_trials(dataset_root: Path) -> list[dict[str, Any]]:
    prefixes, queries, gold_rows = load_dataset(dataset_root, legacy=True)
    gold_by_id = {item.prefix_id: item for item in gold_rows}
    query_by_key = {(item.prefix_id, item.query_type): item for item in queries}
    rows: list[dict[str, Any]] = []
    for prefix in prefixes:
        # Freeze each method once, before revealing either future audit question.
        adapters = {
            method_id: ReferenceMemoryAdapter(
                method_id,
                deletion_event_ids=(
                    gold_by_id[prefix.prefix_id].ordered_event_ids
                    if method_id == "failure_chain_deletion" else None
                ),
            )
            for method_id in V0_AUDIT_METHODS
        }
        states = {
            method_id: adapter.ingest(prefix, prefix.budget_tokens)
            for method_id, adapter in adapters.items()
        }
        for query_type in V0_AUDIT_QUERY_TYPES:
            query = query_by_key[(prefix.prefix_id, query_type)]
            contexts = []
            for method_id in V0_AUDIT_METHODS:
                adapter = adapters[method_id]
                state = states[method_id]
                bundle = adapter.materialize(state, query, prefix.budget_tokens)
                contexts.append((state, bundle))
            for condition_id in ("oracle_failure_chain", "irrelevant_size_control"):
                contexts.append(counterfactual_bundle(
                    prefix, query, gold_by_id[prefix.prefix_id], condition_id=condition_id,
                ))
            for state, bundle in contexts:
                method_id = bundle.method_id
                artifact = memory_artifact(prefix, state, bundle)
                template = request_template(prefix, query, bundle)
                rows.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "benchmark_id": BENCHMARK_ID,
                        "trial_id": f"{query.query_id}:{method_id}:{bundle.condition_id}",
                        "prefix_id": prefix.prefix_id,
                        "query_id": query.query_id,
                        "query_type": query.query_type,
                        "track": query.track,
                        "recoverability": prefix.recoverability,
                        "method_id": method_id,
                        "condition_id": bundle.condition_id,
                        "max_model_turns": 1,
                        "request_template": template,
                        "request_template_sha256": stable_digest(template),
                        "estimated_input_tokens": estimate_tokens(template),
                        "artifact": artifact.to_dict(),
                    }
                )
    selected_interactive: list[PrefixRecord] = []
    for recoverability in ("R0", "R1", "R2", "R3"):
        candidates = sorted(
            (item for item in prefixes if item.recoverability == recoverability),
            key=lambda item: item.prefix_id,
        )
        if len(candidates) < 2:
            raise ValueError(f"legacy diagnostic lacks two {recoverability} prefixes")
        selected_interactive.extend(candidates[:2])
    for prefix in selected_interactive:
        query = query_by_key[(prefix.prefix_id, "interactive_reacquisition")]
        gold = gold_by_id[prefix.prefix_id]
        conditions = ("full", *COUNTERFACTUAL_CONDITIONS)
        for condition_id in conditions:
            if condition_id == "full":
                adapter = ReferenceMemoryAdapter("M0_full_history")
                state = adapter.ingest(prefix, prefix.budget_tokens)
                bundle = adapter.materialize(state, query, prefix.budget_tokens)
            else:
                state, bundle = counterfactual_bundle(
                    prefix,
                    query,
                    gold,
                    condition_id=condition_id,
                )
            artifact = memory_artifact(prefix, state, bundle)
            template = request_template(prefix, query, bundle)
            rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "benchmark_id": BENCHMARK_ID,
                    "trial_id": f"{query.query_id}:{condition_id}",
                    "prefix_id": prefix.prefix_id,
                    "query_id": query.query_id,
                    "query_type": query.query_type,
                    "track": query.track,
                    "recoverability": prefix.recoverability,
                    "method_id": bundle.method_id,
                    "condition_id": condition_id,
                    "max_model_turns": 4,
                    "request_template": template,
                    "request_template_sha256": stable_digest(template),
                    "estimated_input_tokens": estimate_tokens(template),
                    "artifact": artifact.to_dict(),
                }
            )
    if len(rows) != 272:
        raise ValueError(f"v0 matrix must contain 272 episodes, found {len(rows)}")
    maximum_requests = sum(int(item["max_model_turns"]) for item in rows)
    if maximum_requests != 368:
        raise ValueError(f"v0 matrix must allow at most 368 requests, found {maximum_requests}")
    if len({str(item["trial_id"]) for item in rows}) != len(rows):
        raise ValueError("duplicate v0 trial IDs")
    return rows


# Imported after definitions so mutually-referential helpers initialize safely.
from .adapters import (
    ReferenceMemoryAdapter as ReferenceMemoryAdapter,
)

from .artifacts import (
    counterfactual_bundle as counterfactual_bundle,
    load_dataset as load_dataset,
    memory_artifact as memory_artifact,
)
