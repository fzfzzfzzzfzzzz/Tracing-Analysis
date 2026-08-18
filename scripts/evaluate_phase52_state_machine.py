#!/usr/bin/env python3
"""Evaluate the frozen Phase 5.2 symbolic state machine without model completions."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tracegraph.archive import ArchiveStore  # noqa: E402
from tracegraph.capture import estimate_tokens  # noqa: E402
from tracegraph.decision_state import stable_digest  # noqa: E402
from tracegraph.graph import TraceGraph  # noqa: E402
from tracegraph.lifecycle_annotation import file_sha256, load_phase52_config  # noqa: E402
from tracegraph.lifecycle_state_machine import (  # noqa: E402
    build_forbidden_offline_projection,
    load_tool_effect_registry,
    replay_lifecycle_state_machine,
)
from tracegraph.phase5_offline import (  # noqa: E402
    build_strict_prefix,
    policy_text,
    prefix_messages,
)
from tracegraph.provider_cost import (  # noqa: E402
    ProviderProtocol,
    close_protocol_messages,
    serialized_request_cost,
)
from tracegraph.schema import EdgeType, NodeType  # noqa: E402
from tracegraph.trajectory_artifacts import sha256_json  # noqa: E402


PROTECTED_ROOT_HASHES = {
    "outputs/gdsc_r0_audit": "15ac8851550f3b3a7f9e4ce6caaf826252bb5a10b679814daadfcb02bb381613",
    "outputs/gdsc_r2_1": "12e443366e814eb3403601952dc88763a90bb24c482862402d9110da40d7f491",
    "outputs/phase4": "85a75eb998b08591f426ff64ce328ccc867407042f93485e254b4bf685b93867",
    "outputs/phase5": "be1871f159124856b78a364d6389fd3fc71355a3245a4076601933efca5cab83",
    "outputs/phase5_1": "052981da5bcc836c0fcf417482bc5f90dbdaae5026f7599b734e8a2e9ccea27d",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_new_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_new_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _tree_hash(root: Path) -> str:
    files = [
        {
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return sha256_json(files)


def _verify_protected() -> None:
    for relative, expected in PROTECTED_ROOT_HASHES.items():
        if _tree_hash(REPO_ROOT / relative) != expected:
            raise RuntimeError(f"protected artifact drift: {relative}")


def _system_messages(
    rules: Sequence[str], messages: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    system = [{"role": "system", "content": "\n\n".join(rules)}] if rules else []
    return tuple(system + [dict(item) for item in messages])


def _protocol_valid(messages: Sequence[Mapping[str, Any]]) -> bool:
    closure = close_protocol_messages(messages, set(range(1, len(messages) + 1)))
    return closure.valid and len(closure.ordinals) == len(messages)


def _future_copy(prefix: TraceGraph) -> TraceGraph:
    future = TraceGraph.from_dict(prefix.to_dict())
    call = future.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": "phase52_future_sentinel", "arguments": {}},
        int(prefix.metadata["cutoff_step"]) + 100,
        node_id=f"future_call_{stable_digest(prefix.metadata)[:16]}",
        metadata={"source_message_ordinal": 100000, "tool_name": "phase52_future_sentinel"},
    )
    result = future.create_node(
        NodeType.OBSERVATION,
        {"future": True},
        int(prefix.metadata["cutoff_step"]) + 101,
        node_id=f"future_result_{stable_digest(prefix.metadata)[:16]}",
        metadata={"source_message_ordinal": 100001, "status": "success"},
    )
    future.connect(call.node_id, result.node_id, EdgeType.PRODUCES)
    return future


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows]
    return statistics.median(values) if values else 0.0


def _mean(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows]
    return statistics.fmean(values) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs/phase52_lifecycle_modeling.json",
    )
    args = parser.parse_args()
    config = load_phase52_config(args.config)
    output_root = REPO_ROOT / str(config["output_root"])
    state_root = output_root / "state_machine"
    if state_root.exists():
        raise FileExistsError("Phase 5.2 state-machine artifact is create-only")
    if (output_root / "manifest.json").exists():
        raise FileExistsError("Phase 5.2 artifact is already finalized")
    pseudolabel_gate = _load_json(output_root / "pseudolabel_gate.json")
    if pseudolabel_gate["decision"] != "pass":
        raise RuntimeError("pseudo-label quality gate failed; Phase 5.2 must stop")
    _verify_protected()

    manifest = _load_json(REPO_ROOT / config["inputs"]["development_manifest"])
    schemas = _load_json(REPO_ROOT / config["inputs"]["tool_schemas"])
    consensus = _load_jsonl(output_root / "machine_consensus.jsonl")
    consensus_map = {
        (str(item["prefix_id"]), str(item["span_id"])): item for item in consensus
    }
    phase51_rows = _load_jsonl(REPO_ROOT / config["inputs"]["phase51_rows"])
    phase51_by_prefix = {str(item["prefix_id"]): item for item in phase51_rows}
    registry = load_tool_effect_registry(config)
    source_rows = {str(item["session_id"]): item for item in manifest["sources"]}
    graphs: dict[str, TraceGraph] = {}
    archive_ok: dict[str, bool] = {}
    for session_id, source in source_rows.items():
        source_path = REPO_ROOT / str(source["source_path"])
        if file_sha256(source_path) != source["source_file_sha256"]:
            raise RuntimeError(f"source graph file drift: {source_path}")
        graph = TraceGraph.load(source_path)
        graphs[session_id] = graph
        archive = ArchiveStore(source_path.parent / "archive")
        archive_ok[session_id] = not archive.verify_all()

    rows: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    for prefix_row in manifest["prefixes"]:
        prefix_id = str(prefix_row["prefix_id"])
        session_id = str(prefix_row["session_id"])
        ordinal = prefix_row.get("source_message_ordinal")
        prefix = build_strict_prefix(
            graphs[session_id],
            cutoff_step=int(prefix_row["cutoff_step"]),
            source_message_ordinal=int(ordinal) if ordinal is not None else None,
            prefix_id=prefix_id,
        )
        started = time.perf_counter_ns()
        predictions = replay_lifecycle_state_machine(prefix, registry=registry)
        elapsed_ns = time.perf_counter_ns() - started
        repeated = replay_lifecycle_state_machine(prefix, registry=registry)
        future_predictions = replay_lifecycle_state_machine(_future_copy(prefix), registry=registry)
        projection = build_forbidden_offline_projection(prefix, predictions)
        repeated_projection = build_forbidden_offline_projection(prefix, repeated)
        deterministic = predictions == repeated and projection == repeated_projection
        future_independent = predictions == future_predictions
        raw_messages = prefix_messages(prefix)
        projected_messages = tuple(projection["messages"])
        rules = policy_text(prefix)
        domain_schemas = tuple(dict(item) for item in schemas["domains"][prefix_row["domain"]])
        protocol = ProviderProtocol(
            model=config["model"]["report_identity"],
            system_rules=rules,
            base_messages=raw_messages,
            tools=domain_schemas,
            hard_context_limit=128000,
        )
        raw_prompt = _system_messages(rules, raw_messages)
        projected_prompt = _system_messages(rules, projected_messages)
        raw_tokens = serialized_request_cost(protocol, raw_prompt)
        projected_tokens = serialized_request_cost(protocol, projected_prompt)
        task_id = str(prefix_row["task_id"])
        split = (
            "held_out"
            if task_id in set(config["split"]["held_out_task_ids"])
            else "calibration"
            if task_id in set(config["split"]["calibration_task_ids"])
            else "development"
        )
        consensus_safe_count = sum(
            int(item["disposition"] == "safe_to_evict")
            for key, item in consensus_map.items()
            if key[0] == prefix_id
        )
        row = {
            "prefix_id": prefix_id,
            "session_id": session_id,
            "domain": prefix_row["domain"],
            "task_id": task_id,
            "split": split,
            "span_count": len(predictions),
            "predicted_safe_count": sum(
                int(item.disposition == "safe_to_evict") for item in predictions
            ),
            "consensus_safe_count": consensus_safe_count,
            "opportunity_positive": consensus_safe_count > 0,
            "raw_serialized_tokens": raw_tokens,
            "projected_serialized_tokens": projected_tokens,
            "serialized_token_delta": projected_tokens - raw_tokens,
            "reduced": projected_tokens < raw_tokens,
            "deterministic": deterministic,
            "future_suffix_independent": future_independent,
            "archive_valid": archive_ok[session_id],
            "protocol_valid": _protocol_valid(projected_prompt),
            "projection_send_forbidden": projection["never_send_to_provider"],
            "projection_sha256": projection["projection_sha256"],
            "state_machine_runtime_ns": elapsed_ns,
            "state_machine_artifact_token_estimate": estimate_tokens(
                [item.to_dict() for item in predictions]
            ),
            "fixed_policy_tool_schema_token_estimate": estimate_tokens(
                {"policy": list(rules), "tool_schemas": list(domain_schemas)}
            ),
            "phase51_grade_a_evicted_span_count": int(
                phase51_by_prefix[prefix_id]["grade_a_evicted_span_count"]
            ),
            "external_model_completions": 0,
        }
        rows.append(row)
        prediction_rows.extend(
            {
                "prefix_id": prefix_id,
                "domain": prefix_row["domain"],
                "task_id": task_id,
                "split": split,
                **item.to_dict(),
            }
            for item in predictions
        )

    heldout_predictions = {
        (item["prefix_id"], item["span_id"]): item
        for item in prediction_rows
        if item["split"] == "held_out"
    }
    heldout_consensus = {
        key: item for key, item in consensus_map.items() if item["split"] == "held_out"
    }
    if set(heldout_predictions) != set(heldout_consensus):
        raise RuntimeError("held-out state predictions and consensus units differ")
    predicted_safe_keys = {
        key
        for key, item in heldout_predictions.items()
        if item["disposition"] == "safe_to_evict"
    }
    consensus_safe_keys = {
        key
        for key, item in heldout_consensus.items()
        if item["disposition"] == "safe_to_evict"
    }
    consensus_critical_keys = {
        key
        for key, item in heldout_consensus.items()
        if item["disposition"] == "live_critical"
    }
    predicted_critical_keys = {
        key
        for key, item in heldout_predictions.items()
        if item["disposition"] == "live_critical"
    }
    severe = predicted_safe_keys & consensus_critical_keys
    safe_true_positive = predicted_safe_keys & consensus_safe_keys
    phase51_grade_a_heldout = sum(
        int(item["phase51_grade_a_evicted_span_count"])
        for item in rows
        if item["split"] == "held_out"
    )
    heldout_metrics = {
        "safe_to_evict_precision": (
            len(safe_true_positive) / len(predicted_safe_keys) if predicted_safe_keys else 1.0
        ),
        "live_critical_recall": (
            len(predicted_critical_keys & consensus_critical_keys) / len(consensus_critical_keys)
            if consensus_critical_keys
            else 1.0
        ),
        "severe_false_dead": len(severe),
        "consensus_safe_identified": len(safe_true_positive),
        "phase51_grade_a_same_test": phase51_grade_a_heldout,
        "determinism_rate": statistics.fmean(
            int(item["deterministic"]) for item in rows if item["split"] == "held_out"
        ),
        "future_suffix_independence_rate": statistics.fmean(
            int(item["future_suffix_independent"])
            for item in rows
            if item["split"] == "held_out"
        ),
        "archive_valid_rate": statistics.fmean(
            int(item["archive_valid"]) for item in rows if item["split"] == "held_out"
        ),
        "protocol_valid_rate": statistics.fmean(
            int(item["protocol_valid"]) for item in rows if item["split"] == "held_out"
        ),
        "projection_send_forbidden_rate": statistics.fmean(
            int(item["projection_send_forbidden"])
            for item in rows
            if item["split"] == "held_out"
        ),
        "hash_check_rate": 1.0,
    }
    thresholds = config["gates"]["state_machine_held_out"]
    integrity_keys = (
        "determinism_rate",
        "future_suffix_independence_rate",
        "archive_valid_rate",
        "protocol_valid_rate",
        "projection_send_forbidden_rate",
        "hash_check_rate",
    )
    checks = {
        "safe_to_evict_precision": (
            heldout_metrics["safe_to_evict_precision"] >= thresholds["safe_precision_min"]
        ),
        "live_critical_recall": (
            heldout_metrics["live_critical_recall"] >= thresholds["live_critical_recall_min"]
        ),
        "severe_false_dead": (
            heldout_metrics["severe_false_dead"] <= thresholds["severe_false_dead_max"]
        ),
        "consensus_safe_identified": (
            heldout_metrics["consensus_safe_identified"]
            >= thresholds["consensus_safe_identified_min"]
        ),
        "exceeds_phase51_grade_a": (
            heldout_metrics["consensus_safe_identified"]
            > heldout_metrics["phase51_grade_a_same_test"]
        ),
        "integrity": all(
            heldout_metrics[key] >= thresholds["integrity_rate_min"]
            for key in integrity_keys
        ),
    }
    opportunity_rows = [item for item in rows if item["opportunity_positive"]]
    cost = {
        "opportunity_positive_prefixes": len(opportunity_rows),
        "opportunity_positive_paired_token_delta_mean": _mean(
            opportunity_rows, "serialized_token_delta"
        ),
        "opportunity_positive_paired_token_delta_median": _median(
            opportunity_rows, "serialized_token_delta"
        ),
        "all_261_raw_total": sum(int(item["raw_serialized_tokens"]) for item in rows),
        "all_261_projected_total": sum(
            int(item["projected_serialized_tokens"]) for item in rows
        ),
        "all_261_paired_delta_total": sum(
            int(item["serialized_token_delta"]) for item in rows
        ),
        "all_261_paired_delta_mean": _mean(rows, "serialized_token_delta"),
        "all_261_paired_delta_median": _median(rows, "serialized_token_delta"),
        "all_261_benefit_ratio": sum(int(item["reduced"]) for item in rows) / len(rows),
        "fixed_policy_tool_schema_tokens_mean": _mean(
            rows, "fixed_policy_tool_schema_token_estimate"
        ),
        "state_machine_artifact_tokens_mean": _mean(
            rows, "state_machine_artifact_token_estimate"
        ),
        "state_machine_runtime_ns_mean": _mean(rows, "state_machine_runtime_ns"),
        "historical_93_used_as_gate": False,
    }
    state_root.mkdir(parents=True, exist_ok=False)
    _write_new_jsonl(state_root / "prefix_rows.jsonl", rows)
    _write_new_jsonl(state_root / "predictions.jsonl", prediction_rows)
    summary: dict[str, Any] = {
        "schema_version": "phase52_state_machine_summary_v1",
        "counts": {
            "prefixes": len(rows),
            "predictions": len(prediction_rows),
            "prediction_dispositions": dict(
                sorted(Counter(item["disposition"] for item in prediction_rows).items())
            ),
        },
        "held_out_metrics": heldout_metrics,
        "cost": cost,
        "machine_consensus_is_development_evidence": True,
        "construct_formally_validated": False,
        "external_model_completions": 0,
    }
    summary["summary_sha256"] = sha256_json(summary)
    _write_new_json(state_root / "summary.json", summary)
    gate: dict[str, Any] = {
        "schema_version": "phase52_state_machine_gate_v1",
        "decision": "pass_for_small_human_review" if all(checks.values()) else "stop_phase52",
        "checks": checks,
        "metrics": heldout_metrics,
        "thresholds": thresholds,
        "failure_action": "no_scheme_b_and_no_external_behavior_experiment",
        "success_scope": "worth_small_human_review_only",
        "formal_construct_validation": False,
    }
    gate["gate_report_sha256"] = sha256_json(gate)
    _write_new_json(state_root / "gate.json", gate)
    _verify_protected()
    files = [
        {
            "path": path.relative_to(output_root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    final_manifest: dict[str, Any] = {
        "schema_version": "phase52_artifact_manifest_v1",
        "status": "complete",
        "decision": gate["decision"],
        "files": files,
        "protected_artifact_hashes": dict(PROTECTED_ROOT_HASHES),
        "pseudolabel_summary_sha256": _load_json(output_root / "pseudolabel_summary.json")[
            "summary_sha256"
        ],
        "state_machine_summary_sha256": summary["summary_sha256"],
        "external_behavior_model_sessions": 0,
    }
    final_manifest["manifest_sha256"] = sha256_json(final_manifest)
    _write_new_json(output_root / "manifest.json", final_manifest)
    print(json.dumps({"gate": gate, "cost": cost}, ensure_ascii=False, sort_keys=True))
    return 0 if gate["decision"] == "pass_for_small_human_review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
