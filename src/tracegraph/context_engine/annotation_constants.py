"""Constants moved from ``tracegraph.lifecycle_annotation``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..capture import estimate_tokens
from ..graph import TraceGraph
from ..liveness import EventSpan
from ..phase5_offline import policy_text
from ..schema import EdgeType, Node, NodeType
from ..trajectory_artifacts import sha256_json

DISPOSITIONS = (
    "live_critical",
    "live_noncritical",
    "safe_to_evict",
    "uncertain",
)

TERMINAL_REASONS = (
    "active",
    "consumed",
    "superseded",
    "invalidated",
    "resolved",
    "duplicate",
    "audit_required",
    "unknown",
)

OBLIGATIONS = ("policy", "confirmation", "retry", "receipt", "audit")

CURRENT_TARGET_NEEDS = ("required", "useful", "not_needed", "uncertain")

TERMINAL_SAFE_REASONS = (
    "consumed",
    "superseded",
    "invalidated",
    "resolved",
    "duplicate",
)

_FORBIDDEN_INPUT_KEYS = {
    "reward",
    "outcome",
    "task_success",
    "future_suffix",
    "future_events",
    "f5_label",
    "phase5_label",
    "phase51_label",
    "prune_result",
    "pruning_result",
    "token_gain",
    "token_savings",
    "treatment",
}

_CALL_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}

_SYSTEM_PROMPT = """You label historical tool-exchange spans using only the supplied prefix.
Return exactly one label for every span through submit_lifecycle_labels.

safe_to_evict means the entire call/result span is no longer needed for the current target because prefix-visible evidence proves it was fully consumed, superseded, invalidated, resolved, or duplicated. Do not guess. Use uncertain when entity identity, field coverage, success, relation direction, or future reactivation is ambiguous.

live_critical covers policy, explicit confirmation, unresolved retry/error, side-effect receipt, audit, or evidence still required for the current target. live_noncritical is useful but not critical. A side-effect receipt or a span carrying policy/confirmation/audit obligations must never be safe_to_evict. relation_target_ids name the later span(s) that establish a terminal relation. evidence_event_ids must come only from the supplied event log. Keep both ID arrays sorted and unique."""

_RELATION_SYSTEM_PROMPT = """Identify lifecycle relations using only the supplied prefix. Return one JSON-compatible label per span through submit_lifecycle_relations.

Never output disposition; code derives it. terminal_reason is only one of active, consumed, superseded, invalidated, resolved, duplicate, audit_required, or unknown. Superseded belongs only in terminal_reason.

current_target_need is required, useful, not_needed, or uncertain. Use not_needed only when prefix evidence proves this exact span is no longer needed. Use uncertain for ambiguous identity, coverage, success, relation, or reuse.

Put policy, confirmation, unresolved retry, receipt, and audit only in obligations. relation_target_ids are later proving spans. evidence_event_ids must be supplied events. Sort and deduplicate both ID arrays."""

_RELATION_BOOLEAN_SYSTEM_PROMPT = """Identify lifecycle relations using only the supplied prefix. Return one JSON-compatible label per span through submit_lifecycle_relations.

Never output disposition. terminal_reason is active, consumed, superseded, invalidated, resolved, duplicate, audit_required, or unknown. Superseded belongs only in terminal_reason.

required_for_current_target and requirement_uncertain are booleans, never strings. Set required_for_current_target=true only when the current target still needs this exact historical span. Set requirement_uncertain=true when that need cannot be proven either way.

Put policy, confirmation, unresolved retry, receipt, and audit only in obligations. relation_target_ids are later proving spans. evidence_event_ids must be supplied events. Sort and deduplicate both ID arrays."""
