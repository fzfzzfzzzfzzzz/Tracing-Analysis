"""Constants moved from ``tracegraph.phase6_scenarios``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from ..archive import ArchiveStore
from ..capture import estimate_tokens
from ..decision_state import stable_digest
from ..goal_lifecycle import GoalContext, GoalLifecycleState
from ..graph import TraceGraph
from ..schema import Edge, EdgeType, Node, NodeType

SCENARIO_SCHEMA_VERSION = "trace_lifecycle_fork_v1"

DEFAULT_BASE_SEED = 20260830

_CREATED_AT = "2026-08-30T00:00:00+00:00"

SCENARIO_FAMILIES = (
    "F1_shell_switch",
    "F2_failed_approach",
    "F3_goal_resume",
    "F4_explainable_process",
    "F5_state_supersession",
    "F6_side_effect_audit",
)

VARIANTS = (
    ("small_cheap", 256, "cheap"),
    ("small_costly", 256, "costly"),
    ("large_cheap", 2048, "cheap"),
    ("large_costly", 2048, "costly"),
)

FORK_TYPES = ("CONTINUE", "REACTIVATE", "DISTRACTOR")
