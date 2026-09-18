"""Constants moved from ``tracegraph.compression_audit_metrics``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
import math
import random
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from ...compression_audit import BENCHMARK_ID, SCHEMA_VERSION, FailureChainGold, PrefixRecord, QueryRecord, canonical_json, file_sha256, git_provenance, implementation_provenance, load_jsonl, stable_digest, verify_file_manifest, write_file_manifest
from ...compression_audit_runtime import FORMAL_METHOD_IDS, load_dataset

SCORE_SCHEMA_VERSION = "compression_audit_score_v1"

REPORT_SCHEMA_VERSION = "compression_audit_report_v1"

GATE_SCHEMA_VERSION = "compression_audit_gate_v1"

PRIMARY_STRUCTURED_FIELDS = {
    "failed_action",
    "failed_arguments",
    "failure_cause",
    "replacement_action",
    "replacement_arguments",
    "ordered_event_ids",
}
