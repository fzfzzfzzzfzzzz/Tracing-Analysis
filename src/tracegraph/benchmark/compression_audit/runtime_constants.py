"""Constants moved from ``tracegraph.compression_audit_runtime``."""

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

REFERENCE_METHODS = (
    "M0_full_history",
    "M1_recent_masking",
    "M2_flat_lexical_archive",
    "M3_lifecycle_eviction_only",
    "M4_lifecycle_flat_reactivation",
    "M5_lifecycle_causal_reactivation",
    "M6_fixed_handoff",
    "failure_chain_deletion",
)

RANKED_REFERENCE_METHODS = REFERENCE_METHODS[:7]

FORMAL_METHOD_IDS = (*RANKED_REFERENCE_METHODS, "ACON_official")

COUNTERFACTUAL_CONDITIONS = (
    "candidate",
    "oracle_failure_chain",
    "irrelevant_size_control",
)

V0_AUDIT_METHODS = (
    "M0_full_history",
    "M1_recent_masking",
    "failure_chain_deletion",
)

V0_AUDIT_QUERY_TYPES = ("audit_failed_action", "audit_failure_cause")
