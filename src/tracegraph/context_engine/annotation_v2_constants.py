"""Constants moved from ``tracegraph.failure_chain_annotation_v2``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from ..failure_chain_annotation import build_failure_chain_items
from ..graph import TraceGraph

V2_LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "should_card_remain_active": ("yes", "no", "unclear"),
    "expiry_cause": (
        "resolved",
        "superseded",
        "corrected_syntax",
        "alternative_completed",
        "user_abandoned",
        "constraint_changed",
        "final_accepted",
        "ttl_expired",
        "still_active",
        "other",
        "unclear",
    ),
    "scope_relation": (
        "same_operation",
        "different_operation",
        "alternative_completion",
        "syntax_correction",
        "not_applicable",
        "unclear",
    ),
    "failure_class": (
        "actionable",
        "terminal",
        "policy_denied",
        "malformed",
        "stale",
        "unclear",
    ),
    "card_covers_next_step": ("yes", "no", "not_applicable", "unclear"),
}

ANNOTATION_METADATA_FIELDS = (
    "annotation_provenance",
    "annotator_identity",
    "annotation_version",
    "independence_warning",
)

ANNOTATION_CONTEXT_FIELDS = (
    "annotation_id",
    "source_kind",
    "domain",
    "task_id",
    "failure_step",
    "tool_name",
    "failed_call",
    "failure_result",
    "later_chain_context",
    "local_trace_window",
)

ANNOTATION_FIELDS = (
    *ANNOTATION_CONTEXT_FIELDS,
    *V2_LABEL_FIELDS,
    *ANNOTATION_METADATA_FIELDS,
    "confidence",
    "notes",
)

V2_SCHEMA_VERSION = "2.0"

V2_GENERATOR_VERSION = "phase4_failure_chain_v2"
