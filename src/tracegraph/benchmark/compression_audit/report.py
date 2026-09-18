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
    REPORT_SCHEMA_VERSION as REPORT_SCHEMA_VERSION,
    SCORE_SCHEMA_VERSION as SCORE_SCHEMA_VERSION,
)



def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def score_run(
    dataset_root: Path,
    run_path: Path,
    output_root: Path,
    *,
    bootstrap_samples: int = 10_000,
) -> dict[str, Any]:
    if run_path.is_dir() and (run_path / "manifest.json").is_file():
        manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") == "compression_audit_pilot_v02_1":
            from .development_rescore import rescore_pilot

            return rescore_pilot(dataset_root, run_path, output_root)
    if output_root.exists():
        raise FileExistsError(f"score output already exists: {output_root}")
    immutable_roots = (dataset_root, run_path) if run_path.is_dir() else (dataset_root,)
    if any(output_root.resolve().is_relative_to(root.resolve()) for root in immutable_roots):
        raise ValueError("score output must be outside immutable dataset and run directories")
    verify_file_manifest(dataset_root)
    dataset_manifest_hash = file_sha256(dataset_root / "manifest.json")
    dataset_manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
    run_manifest_verified = run_path.is_dir()
    if run_manifest_verified:
        verify_file_manifest(run_path)
        run_manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
        if run_manifest.get("dataset_manifest_sha256") != dataset_manifest_hash:
            raise ValueError("score dataset differs from the frozen run dataset")
    ranking_inputs_eligible = run_manifest_verified and dataset_manifest.get("v1_ready") is True
    controlled = load_dataset(dataset_root, legacy=False)
    legacy = load_dataset(dataset_root, legacy=True)
    prefixes = {item.prefix_id: item for item in (*controlled[0], *legacy[0])}
    queries = {item.query_id: item for item in (*controlled[1], *legacy[1])}
    gold = {item.prefix_id: item for item in (*controlled[2], *legacy[2])}
    episode_path = run_path / "episodes.jsonl" if run_path.is_dir() else run_path
    episodes = load_jsonl(episode_path)
    protocols = {row.get("protocol", "v0.1-diagnostic") for row in episodes}
    if len(protocols) > 1:
        raise ValueError("different answer protocols require separate score reports")
    report_protocol = next(iter(protocols), "v0.1-diagnostic")
    if len({row.get("episode_id") for row in episodes}) != len(episodes):
        raise ValueError("run contains duplicate episode IDs")
    scored = []
    for episode in episodes:
        prefix_id = str(episode.get("prefix_id"))
        query_id = str(episode.get("query_id"))
        if prefix_id not in prefixes or query_id not in queries or prefix_id not in gold:
            raise ValueError(f"episode references unknown benchmark data: {episode.get('episode_id')}")
        if queries[query_id].prefix_id != prefix_id:
            raise ValueError("episode query belongs to a different source prefix")
        result = score_episode(episode, prefixes[prefix_id], queries[query_id], gold[prefix_id])
        if not ranking_inputs_eligible:
            result["ranking_eligible"] = False
        scored.append(result)
    summaries = _method_summaries(scored)
    for summary in summaries:
        rows = [
            item
            for item in scored
            if item["method_id"] == summary["method_id"]
            and item["condition_id"] == summary["condition_id"]
            and item["track"] == summary["track"]
        ]
        summary["audit_pass"] = _cluster_bootstrap(
            rows,
            "audit_pass",
            samples=bootstrap_samples,
        )
        summary["graded_audit_score"] = _cluster_bootstrap(
            rows,
            "graded_audit_score",
            samples=bootstrap_samples,
        )
        for metric in (
            "overall_failure_memory_score",
            "fact_retention_score",
            "semantic_causal_score",
            "provenance_score",
            "scope_score",
            "safety_score",
        ):
            summary[metric] = _cluster_bootstrap(
                rows,
                metric,
                samples=bootstrap_samples,
            )
    counterfactuals = _counterfactual_rows(scored)
    audit_pairs = _audit_harm_pairs(scored)
    valid_audit_pairs = [item for item in audit_pairs if not item["invalid_upper_bound"]]
    ranking_rows = [item for item in scored if item["ranking_eligible"]]
    harms = [item for item in counterfactuals if not item["invalid_upper_bound"]]
    audit_counterfactuals = [item for item in harms if item["track"] == "audit_qa"]
    causal_eligible = [item for item in audit_counterfactuals if not item["invalid_size_control"]]
    interactive_pairs = [item for item in harms if item["track"] == "interactive_reacquisition"]
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "protocol": report_protocol,
        "development_only": True,
        "independent_validation": False,
        "interpretation": (
            "Seen v0.1 development data; results are not independent validation evidence."
        ),
        "episode_count": len(scored),
        "source_prefix_count": len({item["prefix_id"] for item in scored}),
        "legacy_diagnostic_episode_count": sum(item["source_kind"] == "legacy_diagnostic" for item in scored),
        "non_failure_legacy_episode_count": sum(not item["chain_applicable"] for item in scored),
        "ranked_episode_count": len(ranking_rows),
        "inference_policy": "held_out_only" if ranking_rows else "diagnostic_descriptive_only",
        "input_verification": {
            "dataset_manifest_verified": True,
            "run_manifest_verified": run_manifest_verified,
            "dataset_v1_ready": dataset_manifest.get("v1_ready") is True,
        },
        "audit_qa_compression_harm_rate": _mean(float(item["compression_harm"]) for item in valid_audit_pairs),
        "audit_qa_invalid_upper_bound_pairs": sum(item["invalid_upper_bound"] for item in audit_pairs),
        "invalid_upper_bound_count": sum(
            item["invalid_upper_bound"] for item in counterfactuals
        ),
        "compression_harm_rate": _mean(float(item["compression_harm"]) for item in audit_counterfactuals),
        "causal_omission_rate": _mean(float(item["causal_omission"]) for item in causal_eligible),
        "invalid_size_control_pairs": sum(item["invalid_size_control"] for item in counterfactuals),
        "oracle_recovery_rate": _mean(
            float(item["oracle_recovers"])
            for item in audit_counterfactuals
            if item["compression_harm"]
        ),
        "reacquisition_regret": {
            field_name: {
                "mean": _mean(item[field_name] for item in interactive_pairs),
                "median": _median(item[field_name] for item in interactive_pairs),
            }
            for field_name in (
                "extra_model_calls",
                "extra_tool_calls",
                "extra_provider_input_tokens",
                "extra_provider_output_tokens",
                "extra_tool_observation_tokens",
                "extra_latency_seconds",
                "extra_cost_cny",
            )
        },
        "method_summaries": summaries,
        "paired_statistics": _paired_statistics(ranking_rows),
        "pareto_front": _pareto_front(_method_summaries(ranking_rows)),
        "diagnostic_pareto_front": _pareto_front(summaries),
        "single_aggregate_score": "overall_failure_memory_score",
        "aggregate_score_requires_component_reporting": True,
        "statistical_unit": "source_prefix",
        "hallucination_definition": "unsupported structured value or unseen evidence ID; free-text explanation quality is auxiliary",
        "bootstrap_samples": bootstrap_samples,
    }
    gate_report = _v0_gates(scored, counterfactuals)
    gate_report.update(
        {
            "protocol": report_protocol,
            "development_only": True,
            "independent_validation": False,
            "interpretation": (
                "Seen v0.1 development data; results are not independent validation evidence."
            ),
        }
    )
    output_root.mkdir(parents=True, exist_ok=False)
    _write_jsonl(output_root / "scored_episodes.jsonl", scored)
    _write_jsonl(output_root / "counterfactual_pairs.jsonl", counterfactuals)
    _write_jsonl(output_root / "audit_harm_pairs.jsonl", audit_pairs)
    _write_json(output_root / "report.json", report)
    _write_json(output_root / "gate_report.json", gate_report)
    artifacts = write_file_manifest(output_root)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "protocol": report_protocol,
        "development_only": True,
        "independent_validation": False,
        "run_path": str(run_path),
        "run_sha256": file_sha256(episode_path),
        "dataset_manifest_sha256": dataset_manifest_hash,
        "dataset_manifest_verified": True,
        "run_manifest_verified": run_manifest_verified,
        "repository": git_provenance(Path.cwd()),
        "implementation": implementation_provenance(Path.cwd()),
        "score_protocol": SCORE_SCHEMA_VERSION,
        "episodes": len(scored),
        "artifacts": artifacts,
    }
    _write_json(output_root / "manifest.json", manifest)
    return {"report": report, "gates": gate_report, "manifest": manifest}


# Imported after definitions so mutually-referential helpers initialize safely.
from .counterfactuals import (
    _audit_harm_pairs as _audit_harm_pairs,
    _counterfactual_rows as _counterfactual_rows,
)

from .episode_scoring import (
    score_episode as score_episode,
)

from .gates import (
    _v0_gates as _v0_gates,
)

from .statistics import (
    _cluster_bootstrap as _cluster_bootstrap,
    _mean as _mean,
    _median as _median,
    _paired_statistics as _paired_statistics,
)

from .summaries import (
    _method_summaries as _method_summaries,
    _pareto_front as _pareto_front,
)
