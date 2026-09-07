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





def _method_summaries(scored: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in scored:
        groups[(str(row["method_id"]), str(row["condition_id"]), str(row["track"]))].append(row)
    summaries = []
    full_by_query = {
        (str(item["prefix_id"]), str(item["query_id"]), str(item["model"])): item
        for item in scored
        if item.get("method_id") == "M0_full_history" or item.get("condition_id") == "full"
    }
    for (method, condition, track), rows in sorted(groups.items()):
        full_tokens_by_query = {
            key: float(item["provider_input_tokens"]) + float(item["provider_output_tokens"])
            for key, item in full_by_query.items()
            if item.get("provider_input_tokens") is not None and item.get("provider_output_tokens") is not None
        }
        net_savings = []
        net_cost_savings = []
        for row in rows:
            key = (str(row["prefix_id"]), str(row["query_id"]), str(row["model"]))
            full_tokens = full_tokens_by_query.get(key)
            method_input = row.get("provider_input_tokens")
            method_output = row.get("provider_output_tokens")
            if isinstance(full_tokens, (int, float)) and isinstance(method_input, (int, float)) and isinstance(method_output, (int, float)):
                # Tool observations already appear in measured model input: do not charge twice.
                net_savings.append(
                    float(full_tokens)
                    - float(method_input)
                    - float(method_output)
                    - float(row.get("compression_input_tokens", 0))
                    - float(row.get("compression_output_tokens", 0))
                )
            baseline = full_by_query.get(key, {})
            if isinstance(baseline.get("cost_cny"), (int, float)) and isinstance(row.get("cost_cny"), (int, float)):
                net_cost_savings.append(
                    float(baseline["cost_cny"]) - float(row["cost_cny"])
                    - float(row.get("compression_cost_cny", 0))
                )
        summaries.append(
            {
                "method_id": method,
                "condition_id": condition,
                "track": track,
                "episodes": len(rows),
                "prefix_clusters": len({str(item["prefix_id"]) for item in rows}),
                "audit_pass": _cluster_bootstrap(rows, "audit_pass"),
                "mean_slot_accuracy": _mean(item["slot_accuracy"] for item in rows),
                "mean_primary_structured_accuracy": _mean(
                    item["primary_structured_accuracy"] for item in rows
                ),
                "mean_auxiliary_explanation_quality": _mean(
                    item["auxiliary_explanation_quality"] for item in rows
                ),
                "hallucination_rate": _mean(float(item["hallucination"]) for item in rows),
                "causal_order_inversion_rate": _mean(
                    float(item["causal_order_inversion"]) for item in rows
                ),
                "mean_compression_ratio": _mean(item["compression_ratio"] for item in rows),
                "mean_model_calls": _mean(item["model_call_count"] for item in rows),
                "mean_tool_calls": _mean(item["tool_call_count"] for item in rows),
                "mean_provider_input_tokens": _mean(
                    item["provider_input_tokens"] for item in rows
                ),
                "mean_provider_output_tokens": _mean(
                    item["provider_output_tokens"] for item in rows
                ),
                "mean_tool_observation_tokens": _mean(
                    item["tool_observation_tokens"] for item in rows
                ),
                "mean_latency_seconds": _mean(item["latency_seconds"] for item in rows),
                "mean_cost_cny": _mean(item["cost_cny"] for item in rows),
                "mean_end_to_end_net_token_savings": _mean(net_savings),
                "mean_end_to_end_net_cost_savings_cny": _mean(net_cost_savings),
                "repeated_failed_operation_rate": _mean(
                    float(item["repeated_failed_operation_count"] > 0) for item in rows
                ),
                "unsafe_side_effect_attempt_rate": _mean(
                    float(item["unsafe_side_effect_attempts"] > 0) for item in rows
                ),
                "unsafe_side_effect_attempts": sum(
                    int(item["unsafe_side_effect_attempts"]) for item in rows
                ),
                "executed_unauthorized_side_effects": sum(
                    int(item["executed_unauthorized_side_effects"]) for item in rows
                ),
            }
        )
    return summaries


def _pareto_front(summaries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in summaries
        if item.get("condition_id") in {"candidate", "full"}
        and item.get("method_id") in FORMAL_METHOD_IDS
        and item.get("audit_pass", {}).get("estimate") is not None
    ]
    front: list[dict[str, Any]] = []
    for item in candidates:
        compression = float(item.get("mean_compression_ratio") or 0.0)
        accuracy = float(item["audit_pass"]["estimate"])
        regret = float(item.get("mean_tool_calls") or 0.0)
        dominated = False
        for other in candidates:
            if other is item or other["track"] != item["track"]:
                continue
            other_values = (
                float(other.get("mean_compression_ratio") or 0.0),
                float(other["audit_pass"]["estimate"]),
                float(other.get("mean_tool_calls") or 0.0),
            )
            no_worse = (
                other_values[0] >= compression
                and other_values[1] >= accuracy
                and other_values[2] <= regret
            )
            strictly_better = other_values != (compression, accuracy, regret)
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            front.append(
                {
                    "method_id": item["method_id"],
                    "condition_id": item["condition_id"],
                    "track": item["track"],
                    "compression_ratio": compression,
                    "audit_pass_rate": accuracy,
                    "mean_tool_calls": regret,
                }
            )
    return sorted(front, key=lambda item: str(item["method_id"]))


# Imported after definitions so mutually-referential helpers initialize safely.
from .statistics import (
    _cluster_bootstrap as _cluster_bootstrap,
    _mean as _mean,
)
