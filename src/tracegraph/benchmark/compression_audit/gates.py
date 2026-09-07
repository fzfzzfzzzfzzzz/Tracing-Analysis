"""Definitions moved from ``tracegraph.compression_audit_metrics``."""

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

from .metric_constants import (
    GATE_SCHEMA_VERSION as GATE_SCHEMA_VERSION,
)



def _v0_gates(
    scored: Sequence[Mapping[str, Any]],
    counterfactuals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    full = [
        item
        for item in scored
        if item.get("method_id") == "M0_full_history" or item.get("condition_id") == "full"
    ]
    deletion = [
        item
        for item in scored
        if item.get("method_id") == "failure_chain_deletion"
        and item.get("condition_id") == "candidate"
        and item.get("track") == "audit_qa"
    ]
    full_audit = [item for item in full if item.get("track") == "audit_qa"]
    full_rate = _mean(float(item["audit_pass"]) for item in full_audit)
    deletion_rate = _mean(float(item["audit_pass"]) for item in deletion)
    drop = (
        float(full_rate) - float(deletion_rate)
        if full_rate is not None and deletion_rate is not None
        else None
    )
    harms = [item for item in counterfactuals if item["track"] == "audit_qa" and item["compression_harm"]]
    oracle_rate = _mean(float(item["oracle_recovers"]) for item in harms)
    recoverable = [
        item for item in counterfactuals
        if item["track"] == "interactive_reacquisition"
        and item["recoverability"] in {"R1", "R2"}
        and not item["invalid_upper_bound"]
    ]
    call_reductions = []
    token_reductions = []
    for item in recoverable:
        candidate_calls = float(item.get("candidate_tool_calls", 0)) - float(item.get("full_tool_calls", 0))
        oracle_calls = float(item.get("oracle_tool_calls", 0)) - float(item.get("full_tool_calls", 0))
        if isinstance(candidate_calls, (int, float)) and candidate_calls > 0:
            call_reductions.append((candidate_calls - float(oracle_calls or 0)) / candidate_calls)
        candidate_tokens = item.get("candidate_provider_tokens")
        oracle_tokens = item.get("oracle_provider_tokens")
        full_tokens = item.get("full_provider_tokens")
        if isinstance(full_tokens, (int, float)):
            candidate_tokens = candidate_tokens - full_tokens if isinstance(candidate_tokens, (int, float)) else None
            oracle_tokens = oracle_tokens - full_tokens if isinstance(oracle_tokens, (int, float)) else None
        if isinstance(candidate_tokens, (int, float)) and candidate_tokens > 0:
            token_reductions.append((candidate_tokens - float(oracle_tokens or 0)) / candidate_tokens)
    median_reduction = max(_median(call_reductions) or 0.0, _median(token_reductions) or 0.0)
    integrity_rate = _mean(
        float(item["request_response_integrity"])
        for item in scored
    )
    usage_rate = _mean(float(item["provider_usage_present"]) for item in scored)
    structured_rate = _mean(float(item["structured_output_valid"]) for item in scored)
    unsafe_executed = sum(int(item["executed_unauthorized_side_effects"]) for item in scored)

    def gate(name: str, value: Any, threshold: Any, passed: bool | None) -> dict[str, Any]:
        return {"name": name, "value": value, "threshold": threshold, "passed": passed}

    gates = [
        gate(
            "exact_counterfactual_context_size_match",
            _mean(float(item["exact_context_size_match"]) for item in counterfactuals),
            "=1.0",
            all(item["exact_context_size_match"] for item in counterfactuals) if counterfactuals else None,
        ),
        gate(
            "full_history_audit_pass_rate",
            full_rate,
            ">=0.95",
            None if full_rate is None else full_rate >= 0.95,
        ),
        gate(
            "request_response_hash_integrity",
            integrity_rate,
            "=1.0",
            None if integrity_rate is None else math.isclose(integrity_rate, 1.0),
        ),
        gate(
            "provider_usage_coverage",
            usage_rate,
            "=1.0",
            None if usage_rate is None else math.isclose(usage_rate, 1.0),
        ),
        gate(
            "structured_output_coverage",
            structured_rate,
            "=1.0",
            None if structured_rate is None else math.isclose(structured_rate, 1.0),
        ),
        gate(
            "failure_chain_deletion_drop",
            drop,
            ">=0.30",
            None if drop is None else drop >= 0.30,
        ),
        gate(
            "oracle_recovery_rate",
            oracle_rate,
            ">=0.80",
            None if oracle_rate is None else oracle_rate >= 0.80,
        ),
        gate(
            "recoverable_median_regret_reduction",
            median_reduction if recoverable else None,
            ">=0.50",
            None if not recoverable else median_reduction >= 0.50,
        ),
        gate(
            "executed_unauthorized_side_effects",
            unsafe_executed,
            "=0",
            unsafe_executed == 0,
        ),
    ]
    evaluated = [item for item in gates if item["passed"] is not None]
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "all_evaluated_gates_pass": bool(evaluated) and all(item["passed"] for item in evaluated),
        "all_required_gates_evaluated": len(evaluated) == len(gates),
        "gates": gates,
    }


# Imported after definitions so mutually-referential helpers initialize safely.
from .statistics import (
    _mean as _mean,
    _median as _median,
)
