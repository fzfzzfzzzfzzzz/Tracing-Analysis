"""Constants moved from ``tracegraph.phase5_offline``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from ..archive import ArchiveStore
from ..graph import TraceGraph
from ..schema import EdgeType, Node, NodeType
from ..trajectory_artifacts import sha256_json

DEVELOPMENT_MANIFEST_VERSION = "phase5_development_prefix_manifest_v1"

TOOL_SCHEMA_ARTIFACT_VERSION = "phase5_tool_schemas_v1"

F5_G1_THRESHOLDS: dict[str, int | float | bool] = {
    "all_frozen_prefixes_included": True,
    "source_load_determinism_rate_min": 1.0,
    "frozen_prefix_hash_match_rate_min": 1.0,
    "deterministic_artifact_rate_min": 1.0,
    "future_suffix_independence_rate_min": 1.0,
    "protocol_valid_rate_min": 1.0,
    "root_event_recall_min": 1.0,
    "critical_event_recall_min": 1.0,
    "archive_reactivation_rate_min": 1.0,
    "request_hash_match_rate_min": 1.0,
    "policy_false_dead_max": 0,
    "confirmation_false_dead_max": 0,
    "side_effect_receipt_false_dead_max": 0,
    "cost_analysis_eligible_min": 1,
    "reduced_prefix_count_min": 1,
    "paired_median_serialized_token_delta_max_exclusive": 0,
    "external_provider_generations_max": 0,
}
