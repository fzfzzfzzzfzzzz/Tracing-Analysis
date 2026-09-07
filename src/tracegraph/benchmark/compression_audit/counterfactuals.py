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





def _counterfactual_rows(scored: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in scored:
        if row.get("track") not in {"audit_qa", "interactive_reacquisition"}:
            continue
        if row.get("method_id") not in {"M0_full_history", "failure_chain_deletion"}:
            continue
        key = (str(row["prefix_id"]), str(row["query_id"]), str(row["model"]))
        by_key[key][str(row["condition_id"])] = row
    results: list[dict[str, Any]] = []
    for (prefix_id, query_id, model), conditions in sorted(by_key.items()):
        full = conditions.get("full")
        candidate = conditions.get("candidate")
        oracle = conditions.get("oracle_failure_chain")
        irrelevant = conditions.get("irrelevant_size_control")
        if not all((full, candidate, oracle, irrelevant)):
            continue
        invalid_upper = not bool(full["audit_pass"])
        harm = bool(full["audit_pass"] and not candidate["audit_pass"])
        exact_size_match = bool(
            oracle["exact_context_tokens"]
            and irrelevant["exact_context_tokens"]
            and oracle["context_tokens"] == irrelevant["context_tokens"]
            and oracle["context_budget_tokens"] == irrelevant["context_budget_tokens"]
            and oracle["context_tokens"] <= oracle["context_budget_tokens"]
        )
        provider_size_match = bool(
            oracle["first_request_input_tokens"] is not None
            and oracle["first_request_input_tokens"] == irrelevant["first_request_input_tokens"]
        )
        causal = bool(
            harm and exact_size_match and provider_size_match
            and oracle["audit_pass"] and not irrelevant["audit_pass"]
        )

        def delta(field_name: str) -> float | None:
            left = candidate.get(field_name)
            right = full.get(field_name)
            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                return None
            return float(left) - float(right)

        results.append(
            {
                "prefix_id": prefix_id,
                "query_id": query_id,
                "model": model,
                "recoverability": candidate["recoverability"],
                "track": candidate["track"],
                "invalid_upper_bound": invalid_upper,
                "compression_harm": harm,
                "causal_omission": causal,
                "exact_context_size_match": exact_size_match,
                "provider_first_request_size_match": provider_size_match,
                "invalid_size_control": not (exact_size_match and provider_size_match),
                "oracle_recovers": harm and bool(oracle["audit_pass"]),
                "irrelevant_recovers": harm and bool(irrelevant["audit_pass"]),
                "extra_model_calls": delta("model_call_count"),
                "extra_tool_calls": delta("tool_call_count"),
                "extra_provider_input_tokens": delta("provider_input_tokens"),
                "extra_provider_output_tokens": delta("provider_output_tokens"),
                "extra_tool_observation_tokens": delta("tool_observation_tokens"),
                "extra_latency_seconds": delta("latency_seconds"),
                "extra_cost_cny": delta("cost_cny"),
                "oracle_tool_calls": oracle["tool_call_count"],
                "candidate_tool_calls": candidate["tool_call_count"],
                "full_tool_calls": full["tool_call_count"],
                "full_provider_tokens": (
                    None if full.get("provider_input_tokens") is None
                    else float(full["provider_input_tokens"]) + float(full.get("provider_output_tokens") or 0)
                ),
                "oracle_provider_tokens": (
                    None
                    if oracle.get("provider_input_tokens") is None
                    else float(oracle["provider_input_tokens"])
                    + float(oracle.get("provider_output_tokens") or 0)
                ),
                "candidate_provider_tokens": (
                    None
                    if candidate.get("provider_input_tokens") is None
                    else float(candidate["provider_input_tokens"])
                    + float(candidate.get("provider_output_tokens") or 0)
                ),
            }
        )
    return results


def _audit_harm_pairs(scored: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    full = {
        (row["prefix_id"], row["query_id"], row["model"]): row
        for row in scored
        if row["condition_id"] == "full"
    }
    pairs = []
    for row in scored:
        if row["condition_id"] != "candidate" or row["track"] != "audit_qa":
            continue
        baseline = full.get((row["prefix_id"], row["query_id"], row["model"]))
        if baseline is None:
            continue
        pairs.append(
            {
                "prefix_id": row["prefix_id"],
                "query_id": row["query_id"],
                "model": row["model"],
                "method_id": row["method_id"],
                "invalid_upper_bound": not baseline["audit_pass"],
                "compression_harm": bool(baseline["audit_pass"] and not row["audit_pass"]),
                "chain_applicable": row["chain_applicable"],
                "source_kind": row["source_kind"],
            }
        )
    return pairs
