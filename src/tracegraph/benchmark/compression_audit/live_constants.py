"""Constants moved from ``tracegraph.compression_audit_live``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from ...capture import estimate_tokens
from ...compression_audit import BENCHMARK_ID, EpisodeRecord, FailureChainGold, PrefixRecord, QueryRecord, canonical_json, file_sha256, git_provenance, implementation_provenance, load_config, load_jsonl, stable_digest, verify_file_manifest, write_file_manifest
from ...compression_audit_runtime import answer_tool_schema, load_dataset, parse_submit_answer, prepare_v0_trials, reacquisition_tool_schema
from ...compression_audit_tokenization import VerifiedContextTokenizer, retokenize_trials
from ...live_guard import require_live_authorization_id

LIVE_RUN_SCHEMA_VERSION = "compression_audit_live_v0_1"
