"""Definitions moved from ``tracegraph.phase5_offline``."""

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

from .phase5_constants import (
    DEVELOPMENT_MANIFEST_VERSION as DEVELOPMENT_MANIFEST_VERSION,
    F5_G1_THRESHOLDS as F5_G1_THRESHOLDS,
)



def build_development_manifest(
    dataset: Mapping[str, Any],
    *,
    dataset_path: Path,
    schemas_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze all decision points without reading their outcome payloads."""

    declared_dataset_hash = dataset.get("dataset_sha256")
    if not isinstance(declared_dataset_hash, str) or len(declared_dataset_hash) != 64:
        raise ValueError("decision-point dataset embedded hash is missing or invalid")
    declared_schema_hash = schemas_artifact.get("artifact_sha256")
    schema_without_hash = dict(schemas_artifact)
    schema_without_hash.pop("artifact_sha256", None)
    if declared_schema_hash != sha256_json(schema_without_hash):
        raise ValueError("tool-schema artifact embedded hash mismatch")

    sources = _source_map(dataset)
    graphs: dict[str, TraceGraph] = {}
    source_rows: list[dict[str, Any]] = []
    for session_id, source in sorted(sources.items()):
        source_path = Path(str(source["source_path"]))
        graph = TraceGraph.load(source_path)
        repeated_graph = TraceGraph.load(source_path)
        recomputed_graph_hash = sha256_json(graph.to_dict())
        repeated_graph_hash = sha256_json(repeated_graph.to_dict())
        if recomputed_graph_hash != repeated_graph_hash:
            raise ValueError(f"source graph load is nondeterministic: {source_path}")
        declared_graph_hash = str(source.get("event_graph_sha256") or "")
        archive_root = source_path.parent / "archive"
        archive = ArchiveStore(archive_root)
        archive_failures = archive.verify_all()
        if archive_failures:
            raise ValueError(f"archive verification failed: {source_path}")
        graphs[session_id] = graph
        source_rows.append(
            {
                "session_id": session_id,
                "domain": str(source["domain"]),
                "task_id": str(source["task_id"]),
                "trial": source.get("trial"),
                "source_path": source_path.as_posix(),
                "source_file_sha256": file_sha256(source_path),
                "event_graph_sha256": recomputed_graph_hash,
                "declared_phase4_event_graph_sha256": declared_graph_hash,
                "declared_phase4_event_graph_hash_match": (
                    declared_graph_hash == recomputed_graph_hash
                ),
                "source_load_deterministic": True,
                "archive": _archive_tree(archive_root),
            }
        )

    prefix_rows: list[dict[str, Any]] = []
    for point in sorted(
        dataset.get("decision_points", ()),
        key=lambda item: str(item["decision_point_id"]),
    ):
        # Deliberately do not access point["outcome"]. The decision is located
        # from the frozen cutoff and source-message ordinal only.
        prefix_id = str(point["decision_point_id"])
        session_id = str(point["session_id"])
        cutoff_step = int(point["cutoff_step"])
        raw_ordinal = point.get("source_message_ordinal")
        source_ordinal = int(raw_ordinal) if raw_ordinal is not None else None
        graph = graphs[session_id]
        prefix = build_strict_prefix(
            graph,
            cutoff_step=cutoff_step,
            source_message_ordinal=source_ordinal,
            prefix_id=prefix_id,
        )
        recomputed_prefix_hash = sha256_json(
            [
                node.to_dict()
                for node in strict_predecision_nodes(
                    graph,
                    cutoff_step=cutoff_step,
                    source_message_ordinal=source_ordinal,
                )
            ]
        )
        if recomputed_prefix_hash != point.get("prefix_sha256"):
            raise ValueError(f"prefix hash mismatch: {prefix_id}")
        source_path = Path(str(sources[session_id]["source_path"]))
        archive = ArchiveStore(source_path.parent / "archive")
        domain = str(point["domain"])
        domain_schemas = schemas_artifact["domains"][domain]
        prefix_rows.append(
            {
                "prefix_id": prefix_id,
                "session_id": session_id,
                "domain": domain,
                "task_id": str(point["task_id"]),
                "trial": point.get("trial"),
                "cutoff_step": cutoff_step,
                "source_message_ordinal": source_ordinal,
                "prefix_sha256": recomputed_prefix_hash,
                "policy_sha256": sha256_json(policy_text(prefix)),
                "tool_schema_sha256": sha256_json(domain_schemas),
                "structural_features": structural_features(
                    prefix,
                    archive=archive,
                ),
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": DEVELOPMENT_MANIFEST_VERSION,
        "construction": "all_frozen_points_outcome_blind_v1",
        "selection": {
            "included_population": "all_frozen_decision_points",
            "selection_input_fields": [
                "decision_point_id",
                "session_id",
                "domain",
                "task_id",
                "trial",
                "cutoff_step",
                "source_message_ordinal",
                "prefix_sha256",
            ],
            "excluded_count": 0,
            "treatment_outcomes_accessed": False,
            "task_reward_accessed": False,
            "cost_analysis_stratum": (
                "archived_complete_tool_span_count>=1 and message_count>=1"
            ),
        },
        "f5_g1_thresholds": dict(F5_G1_THRESHOLDS),
        "inputs": {
            "decision_point_dataset_path": dataset_path.as_posix(),
            "decision_point_dataset_file_sha256": file_sha256(dataset_path),
            "decision_point_dataset_sha256": declared_dataset_hash,
            "tool_schema_artifact_sha256": declared_schema_hash,
            "source_integrity_policy": (
                "freeze source file hashes; require deterministic current loads "
                "and all frozen per-prefix node hashes; preserve but do not "
                "retroactively rewrite legacy Phase 4 event-graph hashes"
            ),
        },
        "sources": source_rows,
        "prefixes": prefix_rows,
        "counts": {
            "sources": len(source_rows),
            "prefixes": len(prefix_rows),
            "cost_analysis_eligible": sum(
                int(item["structural_features"]["cost_analysis_eligible"])
                for item in prefix_rows
            ),
            "reactivation_candidates": sum(
                int(item["structural_features"]["reactivation_candidate"])
                for item in prefix_rows
            ),
            "declared_phase4_event_graph_hash_mismatches": sum(
                int(not item["declared_phase4_event_graph_hash_match"])
                for item in source_rows
            ),
        },
        "input_anomalies": [
            {
                "kind": "legacy_phase4_event_graph_hash_not_reproducible",
                "count": sum(
                    int(not item["declared_phase4_event_graph_hash_match"])
                    for item in source_rows
                ),
                "cause": (
                    "legacy lifecycle normalization previously assigned random "
                    "derived edge ids/timestamps during load"
                ),
                "disposition": (
                    "old hashes retained unchanged; Phase 5 uses frozen raw-file "
                    "hashes, deterministic normalized graph hashes, and verifies "
                    "every frozen prefix-node hash"
                ),
            }
        ],
        "external_provider_generations": 0,
    }
    assert_no_outcome_fields(manifest)
    manifest["manifest_sha256"] = sha256_json(manifest)
    return manifest


def assert_no_outcome_fields(value: Any) -> None:
    """Reject leaked treatment/next-action labels from the frozen manifest."""

    forbidden = {
        "outcome",
        "reward",
        "task_success",
        "tool_names",
        "side_effect",
        "tool_call_count",
    }

    def visit(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key) in forbidden and not path.endswith(
                    ".structural_features"
                ):
                    raise ValueError(
                        f"forbidden outcome field in development manifest: {path}.{key}"
                    )
                visit(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")

    visit(value, "manifest")


def adjudicate_f5_g1(
    metrics: Mapping[str, Any],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the frozen F5-G1 comparators without post-result discretion."""

    rules = (
        (
            "all_frozen_prefixes_included",
            "all_frozen_prefixes_included",
            "equal",
        ),
        (
            "source_load_determinism_rate",
            "source_load_determinism_rate_min",
            "at_least",
        ),
        (
            "frozen_prefix_hash_match_rate",
            "frozen_prefix_hash_match_rate_min",
            "at_least",
        ),
        (
            "deterministic_artifact_rate",
            "deterministic_artifact_rate_min",
            "at_least",
        ),
        (
            "future_suffix_independence_rate",
            "future_suffix_independence_rate_min",
            "at_least",
        ),
        (
            "protocol_valid_rate",
            "protocol_valid_rate_min",
            "at_least",
        ),
        ("root_event_recall", "root_event_recall_min", "at_least"),
        (
            "critical_event_recall",
            "critical_event_recall_min",
            "at_least",
        ),
        (
            "archive_reactivation_rate",
            "archive_reactivation_rate_min",
            "at_least",
        ),
        (
            "request_hash_match_rate",
            "request_hash_match_rate_min",
            "at_least",
        ),
        ("policy_false_dead", "policy_false_dead_max", "at_most"),
        (
            "confirmation_false_dead",
            "confirmation_false_dead_max",
            "at_most",
        ),
        (
            "side_effect_receipt_false_dead",
            "side_effect_receipt_false_dead_max",
            "at_most",
        ),
        (
            "cost_analysis_eligible",
            "cost_analysis_eligible_min",
            "at_least",
        ),
        (
            "reduced_prefix_count",
            "reduced_prefix_count_min",
            "at_least",
        ),
        (
            "paired_median_serialized_token_delta",
            "paired_median_serialized_token_delta_max_exclusive",
            "below",
        ),
        (
            "external_provider_generations",
            "external_provider_generations_max",
            "at_most",
        ),
    )
    criteria: list[dict[str, Any]] = []
    for metric_name, threshold_name, comparator in rules:
        observed = metrics[metric_name]
        threshold = thresholds[threshold_name]
        if comparator == "equal":
            passed = observed == threshold
        elif comparator == "at_least":
            passed = observed >= threshold
        elif comparator == "at_most":
            passed = observed <= threshold
        elif comparator == "below":
            passed = observed < threshold
        else:  # pragma: no cover - frozen table is exhaustive
            raise ValueError(f"unsupported comparator: {comparator}")
        criteria.append(
            {
                "metric": metric_name,
                "observed": observed,
                "comparator": comparator,
                "threshold": threshold,
                "threshold_name": threshold_name,
                "passed": passed,
            }
        )
    return {
        "schema_version": "f5_g1_gate_report_v1",
        "decision": (
            "pass" if all(item["passed"] for item in criteria) else "fail"
        ),
        "criteria": criteria,
    }


# Imported after definitions so mutually-referential helpers initialize safely.
from .phase5_prefix import (
    _archive_tree as _archive_tree,
    _source_map as _source_map,
    build_strict_prefix as build_strict_prefix,
    file_sha256 as file_sha256,
    policy_text as policy_text,
    strict_predecision_nodes as strict_predecision_nodes,
    structural_features as structural_features,
)
