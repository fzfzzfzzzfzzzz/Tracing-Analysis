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





def _mean(values: Iterable[float | int | None]) -> float | None:
    selected = [float(item) for item in values if isinstance(item, (int, float))]
    return sum(selected) / len(selected) if selected else None


def _median(values: Iterable[float | int | None]) -> float | None:
    selected = [float(item) for item in values if isinstance(item, (int, float))]
    return statistics.median(selected) if selected else None


def _cluster_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    field_name: str,
    *,
    samples: int = 10_000,
    seed: int = 20260901,
) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("bootstrap sample count must be positive")
    by_prefix: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(field_name)
        if isinstance(value, bool):
            by_prefix[str(row["prefix_id"])].append(float(value))
        elif isinstance(value, (int, float)):
            by_prefix[str(row["prefix_id"])].append(float(value))
    cluster_values = [sum(items) / len(items) for items in by_prefix.values() if items]
    if not cluster_values:
        return {"estimate": None, "ci95": [None, None], "clusters": 0, "samples": samples}
    estimate = sum(cluster_values) / len(cluster_values)
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        sample = [rng.choice(cluster_values) for _ in cluster_values]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    lower = draws[int(0.025 * (samples - 1))]
    upper = draws[int(0.975 * (samples - 1))]
    return {
        "estimate": estimate,
        "ci95": [lower, upper],
        "clusters": len(cluster_values),
        "samples": samples,
    }


def _sign_test_pvalue(differences: Sequence[float]) -> float | None:
    nonzero = [item for item in differences if not math.isclose(item, 0.0)]
    if not nonzero:
        return None
    positive = sum(item > 0 for item in nonzero)
    n = len(nonzero)
    extreme = min(positive, n - positive)
    probability = sum(math.comb(n, k) for k in range(extreme + 1)) / (2**n)
    return min(1.0, 2.0 * probability)


def _holm_adjust(values: Mapping[str, float | None]) -> dict[str, float | None]:
    valid = sorted(
        ((name, value) for name, value in values.items() if value is not None),
        key=lambda item: float(item[1]),
    )
    adjusted: dict[str, float | None] = {name: None for name in values}
    running = 0.0
    total = len(valid)
    for index, (name, value) in enumerate(valid):
        current = min(1.0, float(value) * (total - index))
        running = max(running, current)
        adjusted[name] = running
    return adjusted


def _paired_statistics(scored: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    full = {
        (str(row["prefix_id"]), str(row["query_id"]), str(row["model"])): row
        for row in scored
        if row.get("method_id") == "M0_full_history" or row.get("condition_id") == "full"
    }
    by_method_prefix: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    graded_by_method_prefix: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    overall_by_method_prefix: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    query_pairs: dict[str, int] = defaultdict(int)
    for row in scored:
        method = str(row["method_id"])
        if method not in FORMAL_METHOD_IDS[1:] or row.get("condition_id") != "candidate":
            continue
        key = (str(row["prefix_id"]), str(row["query_id"]), str(row["model"]))
        baseline = full.get(key)
        if baseline is None:
            continue
        by_method_prefix[method][str(row["prefix_id"])].append(
            float(row["audit_pass"]) - float(baseline["audit_pass"])
        )
        if (
            isinstance(row.get("graded_audit_score"), (int, float))
            and isinstance(baseline.get("graded_audit_score"), (int, float))
        ):
            graded_by_method_prefix[method][str(row["prefix_id"])].append(
                float(row["graded_audit_score"])
                - float(baseline["graded_audit_score"])
            )
        if (
            isinstance(row.get("overall_failure_memory_score"), (int, float))
            and isinstance(baseline.get("overall_failure_memory_score"), (int, float))
        ):
            overall_by_method_prefix[method][str(row["prefix_id"])].append(
                float(row["overall_failure_memory_score"])
                - float(baseline["overall_failure_memory_score"])
            )
        query_pairs[method] += 1
    by_method = {
        method: [sum(values) / len(values) for values in by_prefix.values() if values]
        for method, by_prefix in by_method_prefix.items()
    }
    raw = {method: _sign_test_pvalue(values) for method, values in by_method.items()}
    adjusted = _holm_adjust(raw)
    graded_by_method = {
        method: [sum(values) / len(values) for values in by_prefix.values() if values]
        for method, by_prefix in graded_by_method_prefix.items()
    }
    overall_by_method = {
        method: [sum(values) / len(values) for values in by_prefix.values() if values]
        for method, by_prefix in overall_by_method_prefix.items()
    }
    return [
        {
            "method_id": method,
            "paired_source_prefixes": len(by_method_prefix[method]),
            "paired_prefix_queries": query_pairs[method],
            "mean_audit_pass_delta": _mean(values),
            "mean_graded_audit_score_delta": _mean(graded_by_method.get(method, ())),
            "mean_overall_failure_memory_score_delta": _mean(
                overall_by_method.get(method, ())
            ),
            "paired_sign_pvalue": raw[method],
            "holm_adjusted_pvalue": adjusted[method],
        }
        for method, values in sorted(by_method.items())
    ]
