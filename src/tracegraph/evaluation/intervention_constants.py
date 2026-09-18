"""Constants moved from ``tracegraph.interventions``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from ..archive import ArchiveStore
from ..capture import ToolExecutor, estimate_tokens
from ..context import ContextView, build_context_managers
from ..failure_cards import build_failure_cards
from ..graph import TraceGraph
from ..lifecycle import LifecycleEngine
from ..message_protocol import project_context_items_to_messages
from ..schema import FailureClass, Node, NodeType, ToolStatus, utc_now

P1_INTERVENTION_KINDS = (
    "argument_correction",
    "latest_failure_only",
    "alternative_tool_completion",
    "malformed_then_valid",
)

P1_CONDITIONS = (
    "full_trajectory",
    "ours_without_failure_retention",
    "raw_hard_failure_retention",
    "full_ours",
)
