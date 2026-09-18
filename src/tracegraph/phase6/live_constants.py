"""Constants moved from ``tracegraph.phase6_live``."""

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
