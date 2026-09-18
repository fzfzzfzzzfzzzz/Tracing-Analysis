"""Constants moved from ``tracegraph.compression_audit``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from ...capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens

SCHEMA_VERSION = "compression_audit_v1"

CONFIG_SCHEMA_VERSION = "compression_audit_config_v1"

MANIFEST_SCHEMA_VERSION = "compression_audit_manifest_v1"

VALIDATION_SCHEMA_VERSION = "compression_audit_validation_v1"

BENCHMARK_ID = "compression_audit_v1"

IMPLEMENTATION_PATHS = (
    "src/tracegraph/compression_audit.py",
    "src/tracegraph/compression_audit_runtime.py",
    "src/tracegraph/compression_audit_live.py",
    "src/tracegraph/compression_audit_metrics.py",
    "src/tracegraph/compression_audit_real.py",
    "src/tracegraph/compression_audit_tokenization.py",
    "src/tracegraph/cli.py",
)

RECOVERABILITY_LEVELS = ("R0", "R1", "R2", "R3")

SPLITS = ("dev", "validation", "test")

TRACKS = ("audit_qa", "interactive_reacquisition", "distractor")

QUERY_TYPES = (
    "audit_failed_action",
    "audit_failure_cause",
    "audit_recovery",
    "audit_chain",
    "interactive_reacquisition",
    "distractor_current",
)

FAILURE_ROLES = (
    "failed_action",
    "failure_result",
    "diagnostic_evidence",
    "switch_decision",
    "replacement_action",
    "resolution_evidence",
)

FAILURE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "shell_syntax",
        "failed": "legacy_shell",
        "replacement": "native_shell",
        "error": "shell_syntax_mismatch",
        "diagnostic": "The command used syntax belonging to a different shell.",
        "switch": "Use the shell whose grammar matches the target environment.",
        "resolution": "The native-shell command completed and returned exit code 0.",
    },
    {
        "id": "parameter_schema",
        "failed": "submit_legacy_payload",
        "replacement": "submit_schema_v2_payload",
        "error": "required_field_missing",
        "diagnostic": "The request omitted the required resource_id field.",
        "switch": "Rebuild the request with the current schema and validated fields.",
        "resolution": "The schema-v2 request was accepted with status 200.",
    },
    {
        "id": "permission_policy",
        "failed": "write_directly",
        "replacement": "request_scoped_authorization",
        "error": "policy_denied",
        "diagnostic": "The active credential did not grant the required write scope.",
        "switch": "Obtain scoped authorization before attempting the write.",
        "resolution": "The authorized operation completed under the approved scope.",
    },
    {
        "id": "stale_state",
        "failed": "apply_cached_state",
        "replacement": "refresh_then_apply",
        "error": "stale_revision",
        "diagnostic": "The cached revision was older than the current server revision.",
        "switch": "Refresh current state and rebase the intended change.",
        "resolution": "The rebased change applied to the current revision.",
    },
    {
        "id": "dependency_version",
        "failed": "use_removed_api",
        "replacement": "use_supported_api",
        "error": "unsupported_dependency_api",
        "diagnostic": "The installed dependency version no longer exposes the old API.",
        "switch": "Use the supported API for the pinned dependency version.",
        "resolution": "The supported API executed under the pinned environment.",
    },
    {
        "id": "patch_test",
        "failed": "apply_unscoped_patch",
        "replacement": "apply_targeted_patch",
        "error": "regression_test_failed",
        "diagnostic": "The broad patch changed behavior outside the intended component.",
        "switch": "Restrict the patch to the failing component and preserve invariants.",
        "resolution": "The targeted patch passed the regression and focused tests.",
    },
    {
        "id": "path_environment",
        "failed": "open_relative_path",
        "replacement": "resolve_workspace_path",
        "error": "path_not_found",
        "diagnostic": "The relative path was resolved from the wrong working directory.",
        "switch": "Resolve and verify the path inside the intended workspace.",
        "resolution": "The verified workspace path opened the expected resource.",
    },
    {
        "id": "timeout_resource",
        "failed": "run_unbounded_query",
        "replacement": "run_bounded_query",
        "error": "resource_timeout",
        "diagnostic": "The unbounded query exceeded the fixed time and memory limits.",
        "switch": "Partition the query and enforce a bounded execution window.",
        "resolution": "All bounded partitions completed within the resource limit.",
    },
    {
        "id": "partial_side_effect",
        "failed": "repeat_non_idempotent_action",
        "replacement": "verify_receipt_then_resume",
        "error": "partial_commit_detected",
        "diagnostic": "A receipt proved the first action committed before the timeout.",
        "switch": "Inspect the receipt and resume without repeating the committed action.",
        "resolution": "The workflow completed with one and only one committed action.",
        "side_effect": True,
    },
    {
        "id": "multi_failure_recovery",
        "failed": "retry_same_strategy",
        "replacement": "switch_to_verified_strategy",
        "error": "repeated_strategy_failure",
        "diagnostic": "Two retries reproduced the same diagnostic signature.",
        "switch": "Stop retrying and select the independently verified strategy.",
        "resolution": "The verified strategy completed and produced matching evidence.",
    },
)

DOMAINS: tuple[dict[str, str], ...] = (
    {"id": "software", "entity": "component", "current": "tests are currently green"},
    {"id": "data", "entity": "dataset", "current": "the current snapshot is validated"},
    {"id": "operations", "entity": "service", "current": "the service is currently healthy"},
)

CONTEXT_VARIANTS: tuple[dict[str, Any], ...] = (
    {"id": "short", "target_tokens": 1024, "budget_tokens": 768},
    {"id": "long", "target_tokens": 4096, "budget_tokens": 1536},
)
