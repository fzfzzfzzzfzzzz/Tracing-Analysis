#!/usr/bin/env python3
"""Audit deterministic lifecycle evidence on the frozen 261 Phase 5 prefixes."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from tracegraph.archive import ArchiveStore
from tracegraph.decision_state import StateAtomType
from tracegraph.graph import TraceGraph
from tracegraph.integrations.lifecycle_graph_context import LifecycleGraphContextManager
from tracegraph.lifecycle_context import ContextView, project_context
from tracegraph.lifecycle_evidence import (
    LifecycleEvidenceReport,
    apply_grade_a_overlay,
    evidence_config_sha256,
    extract_lifecycle_evidence,
    load_evidence_config,
)
from tracegraph.liveness import LiveSubgraph
from tracegraph.phase5_offline import (
    build_strict_prefix,
    file_sha256,
    policy_text,
    prefix_messages,
    strict_predecision_nodes,
    structural_features,
)
from tracegraph.provider_cost import ProviderProtocol, request_sha256, serialized_request_cost
from tracegraph.schema import NodeType
from tracegraph.trajectory_artifacts import sha256_json


_POLICY_ATOM_TYPES = {
    StateAtomType.APPLICABLE_POLICY_RULE,
    StateAtomType.GLOBAL_POLICY_RULE,
}
_CONFIRMATION_ATOM_TYPES = {StateAtomType.CONFIRMATION_REQUIREMENT}
_RECEIPT_ATOM_TYPES = {StateAtomType.SIDE_EFFECT_RECEIPT}


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _verify_embedded_hash(
    value: Mapping[str, Any],
    *,
    field: str,
    label: str,
) -> None:
    declared = value.get(field)
    body = dict(value)
    body.pop(field, None)
    if declared != sha256_json(body):
        raise ValueError(f"{label} embedded hash mismatch")


def _raw_messages(
    system_rules: Sequence[str],
    messages: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    system = (
        [{"role": "system", "content": "\n\n".join(system_rules)}]
        if system_rules
        else []
    )
    return tuple(system + [dict(item) for item in messages])


def _compile_hashes(compilation: Any) -> dict[str, str]:
    return {
        "state": compilation.state.lifecycle_hash,
        "query": compilation.query.query_hash,
        "roots": compilation.roots.roots_hash,
        "live_subgraph": compilation.live_subgraph.live_subgraph_hash,
        "context_view": compilation.context_view.context_view_hash,
        "request": compilation.context_view.request_hash,
    }


def _source_event_ids(
    compilation: Any,
    *,
    atom_types: set[StateAtomType] | None = None,
    hard_only: bool = False,
) -> set[str]:
    result: set[str] = set()
    for atom in compilation.state.decision_state.atoms:
        if atom_types is not None and atom.atom_type not in atom_types:
            continue
        if hard_only and not atom.hard:
            continue
        result.update(atom.source_event_ids)
    return result


def _critical_event_ids(compilation: Any, graph: TraceGraph) -> dict[str, set[str]]:
    root_ids = set(compilation.roots.root_event_ids)
    hard_ids = _source_event_ids(compilation, hard_only=True)
    policy_ids = _source_event_ids(compilation, atom_types=_POLICY_ATOM_TYPES)
    policy_ids.update(
        node.node_id
        for node in graph.find_nodes(node_types={NodeType.CONSTRAINT})
        if node.step_id <= compilation.state.cutoff_step
    )
    confirmation_ids = _source_event_ids(
        compilation,
        atom_types=_CONFIRMATION_ATOM_TYPES,
    )
    receipt_ids = _source_event_ids(compilation, atom_types=_RECEIPT_ATOM_TYPES)
    return {
        "root": root_ids,
        "hard": hard_ids,
        "policy": policy_ids,
        "confirmation": confirmation_ids,
        "receipt": receipt_ids,
        "all": root_ids | hard_ids | policy_ids | confirmation_ids | receipt_ids,
    }


def _assert_view_request(view: ContextView) -> bool:
    try:
        view.assert_sent_request(view.request)
    except ValueError:
        return False
    return request_sha256(view.request) == view.request_hash


def _archive_verified_candidate_spans(
    graph: TraceGraph,
    live_subgraph: LiveSubgraph,
    report: LifecycleEvidenceReport,
    archive: ArchiveStore,
) -> tuple[set[str], list[dict[str, Any]]]:
    source_ids = {
        record.source_event_id
        for record in report.grade_b_records
        if record.relation
        in {"exact_entity_flow_candidate", "mutation_invalidation_candidate"}
    }
    selected: set[str] = set()
    provenance: list[dict[str, Any]] = []
    for span in live_subgraph.spans:
        matched = sorted(source_ids.intersection(span.node_ids))
        if not matched or span.span_type != "tool_exchange":
            continue
        nodes = [graph.nodes[node_id] for node_id in span.node_ids]
        if any(node.side_effect for node in nodes):
            continue
        tool_nodes = [
            node
            for node in nodes
            if node.node_type
            in {NodeType.TOOL_CALL, NodeType.MCP_CALL, NodeType.OBSERVATION, NodeType.ERROR}
        ]
        if not tool_nodes or any(not node.raw_ref for node in tool_nodes):
            continue
        for reference in sorted({str(node.raw_ref) for node in tool_nodes}):
            archive.get(reference, verify=True)
        selected.add(span.span_id)
        provenance.append(
            {
                "span_id": span.span_id,
                "reason": "grade_b_optimistic_evidence_ceiling_only",
                "source_event_ids": matched,
                "unsafe_for_provider_emission": True,
            }
        )
    return selected, provenance


def _optimistic_ceiling_subgraph(
    graph: TraceGraph,
    live_subgraph: LiveSubgraph,
    report: LifecycleEvidenceReport,
    archive: ArchiveStore,
) -> tuple[LiveSubgraph, int]:
    candidate_spans, provenance = _archive_verified_candidate_spans(
        graph,
        live_subgraph,
        report,
        archive,
    )
    evicted_spans = set(live_subgraph.evicted_span_ids) | candidate_spans
    live_spans = set(live_subgraph.live_span_ids).difference(evicted_spans)
    span_map = live_subgraph.span_map()
    evicted_nodes = {
        node_id
        for span_id in evicted_spans
        for node_id in span_map[span_id].node_ids
    }
    visible_nodes = {
        node_id for span in live_subgraph.spans for node_id in span.node_ids
    }
    return (
        replace(
            live_subgraph,
            live_node_ids=tuple(visible_nodes.difference(evicted_nodes)),
            evicted_node_ids=tuple(evicted_nodes),
            live_span_ids=tuple(live_spans),
            evicted_span_ids=tuple(evicted_spans),
            closure_provenance=tuple(
                [*live_subgraph.closure_provenance, *provenance]
            ),
            uncertainty_records=tuple(
                [
                    *live_subgraph.uncertainty_records,
                    *(
                        {
                            **item,
                            "action": "ceiling_only_not_send_eligible",
                        }
                        for item in provenance
                    ),
                ]
            ),
            analyzer_version="live_subgraph_phase51_ceiling_v1",
        ),
        len(candidate_spans),
    )


def _future_suffix_independent(
    prefix: TraceGraph,
    report: LifecycleEvidenceReport,
    *,
    config: Mapping[str, Any],
) -> bool:
    future = TraceGraph.from_dict(prefix.to_dict())
    future.create_node(
        NodeType.SUMMARY,
        {"phase51_future_suffix_sentinel": True},
        report.cutoff_step + 100,
        node_id=f"phase51_future_{report.report_hash[:20]}",
        metadata={"source_message_ordinal": report.cutoff_step + 100},
    )
    future_report = extract_lifecycle_evidence(
        future,
        cutoff_step=report.cutoff_step,
        config=config,
    )
    return future_report.to_dict() == report.to_dict()


def _evaluate_prefix(
    prefix_row: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    graph: TraceGraph,
    schemas: Sequence[Mapping[str, Any]],
    frozen_phase5_row: Mapping[str, Any],
    config: Mapping[str, Any],
    model: str,
    hard_context_limit: int,
) -> dict[str, Any]:
    prefix_id = str(prefix_row["prefix_id"])
    cutoff_step = int(prefix_row["cutoff_step"])
    ordinal_value = prefix_row.get("source_message_ordinal")
    source_ordinal = int(ordinal_value) if ordinal_value is not None else None
    prefix_hash_match = (
        sha256_json(
            [
                node.to_dict()
                for node in strict_predecision_nodes(
                    graph,
                    cutoff_step=cutoff_step,
                    source_message_ordinal=source_ordinal,
                )
            ]
        )
        == prefix_row["prefix_sha256"]
    )
    if not prefix_hash_match:
        raise ValueError(f"frozen prefix hash mismatch: {prefix_id}")
    prefix = build_strict_prefix(
        graph,
        cutoff_step=cutoff_step,
        source_message_ordinal=source_ordinal,
        prefix_id=prefix_id,
    )
    source_path = Path(str(source["source_path"]))
    archive = ArchiveStore(source_path.parent / "archive")
    messages = prefix_messages(prefix)
    system_rules = policy_text(prefix)
    if sha256_json(system_rules) != prefix_row["policy_sha256"]:
        raise ValueError(f"policy hash mismatch: {prefix_id}")
    if sha256_json(schemas) != prefix_row["tool_schema_sha256"]:
        raise ValueError(f"tool schema hash mismatch: {prefix_id}")
    if structural_features(prefix, archive=archive) != prefix_row["structural_features"]:
        raise ValueError(f"structural-feature drift: {prefix_id}")

    manager = LifecycleGraphContextManager(
        model=model,
        hard_context_limit=hard_context_limit,
    )
    baseline = manager.compile(
        prefix,
        messages=messages,
        system_rules=system_rules,
        tool_schemas=schemas,
        archive_reader=archive.get,
    )
    frozen_baseline_match = bool(
        baseline.context_view.context_view_hash == frozen_phase5_row["context_view_hash"]
        and baseline.context_view.request_hash == frozen_phase5_row["prune_request_hash"]
        and baseline.context_view.costs.serialized_request
        == frozen_phase5_row["prune_serialized_tokens"]
    )
    if not frozen_baseline_match:
        raise ValueError(f"Phase 5 replay baseline drift: {prefix_id}")

    first_report = extract_lifecycle_evidence(
        prefix,
        cutoff_step=cutoff_step,
        config=config,
    )
    second_report = extract_lifecycle_evidence(
        prefix,
        cutoff_step=cutoff_step,
        config=config,
    )
    first_overlay = apply_grade_a_overlay(prefix, first_report)
    second_overlay = apply_grade_a_overlay(prefix, second_report)
    grade_a_first = manager.compile(
        first_overlay,
        messages=messages,
        system_rules=system_rules,
        tool_schemas=schemas,
        cutoff=cutoff_step,
        archive_reader=archive.get,
    )
    grade_a_second = manager.compile(
        second_overlay,
        messages=messages,
        system_rules=system_rules,
        tool_schemas=schemas,
        cutoff=cutoff_step,
        archive_reader=archive.get,
    )
    deterministic = bool(
        first_report.to_dict() == second_report.to_dict()
        and first_overlay.to_dict() == second_overlay.to_dict()
        and _compile_hashes(grade_a_first) == _compile_hashes(grade_a_second)
        and grade_a_first.to_dict() == grade_a_second.to_dict()
    )
    future_independent = _future_suffix_independent(
        prefix,
        first_report,
        config=config,
    )

    protocol = ProviderProtocol(
        model=model,
        system_rules=tuple(system_rules),
        base_messages=messages,
        tools=tuple(dict(item) for item in schemas),
        hard_context_limit=hard_context_limit,
    )
    raw_messages = _raw_messages(system_rules, messages)
    raw_tokens = serialized_request_cost(protocol, raw_messages)
    grade_a_view = grade_a_first.context_view
    ceiling_subgraph, candidate_span_count = _optimistic_ceiling_subgraph(
        first_overlay,
        grade_a_first.live_subgraph,
        first_report,
        archive,
    )
    ceiling_view = project_context(
        first_overlay,
        ceiling_subgraph,
        "gdsc_prune_v1",
        protocol,
    )
    critical = _critical_event_ids(grade_a_first, first_overlay)
    grade_a_evicted = set(grade_a_view.evicted_node_ids)
    grade_a_false_dead = critical["all"].intersection(grade_a_evicted)
    phase5_tokens = baseline.context_view.costs.serialized_request
    grade_a_tokens = grade_a_view.costs.serialized_request
    ceiling_tokens = ceiling_view.costs.serialized_request
    return {
        "prefix_id": prefix_id,
        "session_id": str(prefix_row["session_id"]),
        "domain": str(prefix_row["domain"]),
        "task_id": str(prefix_row["task_id"]),
        "trial": prefix_row.get("trial"),
        "cost_analysis_eligible": bool(
            prefix_row["structural_features"]["cost_analysis_eligible"]
        ),
        "prefix_hash_match": prefix_hash_match,
        "frozen_phase5_baseline_match": frozen_baseline_match,
        "deterministic_artifacts": deterministic,
        "future_suffix_independent": future_independent,
        "evidence_report_hash": first_report.report_hash,
        "grade_a_record_count": len(first_report.grade_a_records),
        "grade_a_hard_dead_record_count": len(first_report.hard_dead_records),
        "grade_b_record_count": len(first_report.grade_b_records),
        "grade_b_candidate_source_count": len(
            {item.source_event_id for item in first_report.grade_b_records}
        ),
        "grade_b_candidate_span_count": candidate_span_count,
        "evidence_records": [item.to_dict() for item in first_report.records],
        "grade_a_protocol_valid": grade_a_view.protocol_valid,
        "ceiling_protocol_valid": ceiling_view.protocol_valid,
        "grade_a_request_hash_match": _assert_view_request(grade_a_view),
        "ceiling_request_hash_match": _assert_view_request(ceiling_view),
        "grade_a_false_dead": len(grade_a_false_dead),
        "grade_a_policy_false_dead": len(critical["policy"].intersection(grade_a_evicted)),
        "grade_a_confirmation_false_dead": len(
            critical["confirmation"].intersection(grade_a_evicted)
        ),
        "grade_a_receipt_false_dead": len(critical["receipt"].intersection(grade_a_evicted)),
        "raw_serialized_tokens": raw_tokens,
        "phase5_serialized_tokens": phase5_tokens,
        "grade_a_serialized_tokens": grade_a_tokens,
        "ceiling_serialized_tokens": ceiling_tokens,
        "phase5_delta_from_raw": phase5_tokens - raw_tokens,
        "grade_a_delta_from_raw": grade_a_tokens - raw_tokens,
        "ceiling_delta_from_raw": ceiling_tokens - raw_tokens,
        "grade_a_incremental_delta_from_phase5": grade_a_tokens - phase5_tokens,
        "ceiling_incremental_delta_from_phase5": ceiling_tokens - phase5_tokens,
        "phase5_reduced": phase5_tokens < raw_tokens,
        "grade_a_reduced": grade_a_tokens < raw_tokens,
        "ceiling_reduced": ceiling_tokens < raw_tokens,
        "phase5_evicted_span_count": len(baseline.context_view.evicted_span_ids),
        "grade_a_evicted_span_count": len(grade_a_view.evicted_span_ids),
        "ceiling_evicted_span_count": len(ceiling_view.evicted_span_ids),
        "ceiling_projection_emitted_to_provider": False,
        "external_provider_generations": 0,
    }


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return statistics.fmean(float(bool(row[key])) for row in rows)


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows]
    return statistics.median(values) if values else 0.0


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_new_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_new_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    flattened = [
        {
            **row,
            "evidence_records": json.dumps(
                row["evidence_records"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        for row in rows
    ]
    fieldnames = sorted({key for row in flattened for key in row})
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flattened)


def _adjudicate(
    metrics: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    if int(metrics["prefix_count"]) != int(gates["all_prefixes_included"]):
        blockers.append("not_all_frozen_prefixes_included")
    if int(metrics["eligible_prefix_count"]) != int(gates["eligible_prefixes"]):
        blockers.append("eligible_population_drift")
    for metric, threshold in (
        ("deterministic_rate", "deterministic_rate_min"),
        ("future_suffix_independence_rate", "future_suffix_independence_rate_min"),
        ("grade_a_protocol_valid_rate", "protocol_valid_rate_min"),
        ("ceiling_protocol_valid_rate", "protocol_valid_rate_min"),
        ("grade_a_request_hash_match_rate", "request_hash_match_rate_min"),
        ("ceiling_request_hash_match_rate", "request_hash_match_rate_min"),
    ):
        if float(metrics[metric]) < float(gates[threshold]):
            blockers.append(f"{metric}_below_threshold")
    if int(metrics["grade_a_false_dead"]) > int(gates["grade_a_false_dead_max"]):
        blockers.append("grade_a_false_dead")
    if int(metrics["ceiling_reduced_prefix_count_eligible"]) < int(
        gates["optimistic_reduced_prefix_count_min"]
    ):
        blockers.append("optimistic_coverage_below_median_requirement")
    if float(metrics["ceiling_paired_median_delta_eligible"]) >= float(
        gates["optimistic_paired_median_serialized_token_delta_max_exclusive"]
    ):
        blockers.append("optimistic_paired_median_not_negative")
    if int(metrics["external_provider_generations"]) > int(
        gates["external_provider_generations_max"]
    ):
        blockers.append("external_provider_generation_detected")
    return {
        "schema_version": "phase51_evidence_ceiling_gate_v1",
        "gate": "p51_g0",
        "decision": (
            "pass_ceiling_supports_new_telemetry"
            if not blockers
            else str(gates["failure_action"])
        ),
        "passed": not blockers,
        "blockers": blockers,
        "phase5_f5_g1_decision_unchanged": "no_go",
        "structured_or_external_execution_authorized": False,
    }


def main() -> int:
    _configure_utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/phase51_evidence.json"),
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--model", default="zai/glm-4.7-flash")
    parser.add_argument("--hard-context-limit", type=int, default=128_000)
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = load_evidence_config(config_path)
    output_root = (args.output_root or Path(config["outputs"]["audit_root"])).resolve()
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite Phase 5.1 output: {output_root}")

    manifest_path = Path(config["inputs"]["development_manifest"])
    schemas_path = Path(config["inputs"]["tool_schemas"])
    replay_rows_path = Path(config["inputs"]["phase5_replay_rows"])
    manifest = _load_json(manifest_path)
    schemas_artifact = _load_json(schemas_path)
    _verify_embedded_hash(
        manifest,
        field="manifest_sha256",
        label="development-prefix manifest",
    )
    _verify_embedded_hash(
        schemas_artifact,
        field="artifact_sha256",
        label="tool-schema artifact",
    )
    baseline_config = config["baseline"]
    if manifest["manifest_sha256"] != baseline_config["phase5_manifest_sha256"]:
        raise ValueError("frozen Phase 5 manifest hash mismatch")
    if schemas_artifact["artifact_sha256"] != baseline_config["phase5_tool_schema_sha256"]:
        raise ValueError("frozen Phase 5 tool-schema hash mismatch")
    replay_manifest = _load_json(replay_rows_path.parent / "run_manifest.json")
    _verify_embedded_hash(
        replay_manifest,
        field="run_manifest_sha256",
        label="Phase 5 replay run manifest",
    )
    if (
        replay_manifest["run_manifest_sha256"]
        != baseline_config["phase5_replay_manifest_sha256"]
    ):
        raise ValueError("frozen Phase 5 replay manifest hash mismatch")
    phase5_gate = _load_json(replay_rows_path.parent / "f5_g1_gate.json")
    _verify_embedded_hash(
        phase5_gate,
        field="gate_report_sha256",
        label="Phase 5 gate",
    )
    if phase5_gate["gate_report_sha256"] != baseline_config["phase5_gate_sha256"]:
        raise ValueError("frozen Phase 5 gate hash mismatch")
    if phase5_gate["decision"] != "fail":
        raise ValueError("Phase 5.1 requires the frozen F5-G1 No-Go baseline")

    frozen_rows = {
        str(row["prefix_id"]): row for row in _load_jsonl(replay_rows_path)
    }
    source_rows = {str(item["session_id"]): item for item in manifest["sources"]}
    graphs: dict[str, TraceGraph] = {}
    for session_id, source in source_rows.items():
        source_path = Path(str(source["source_path"]))
        if file_sha256(source_path) != source["source_file_sha256"]:
            raise ValueError(f"source file hash mismatch: {source_path}")
        first = TraceGraph.load(source_path)
        second = TraceGraph.load(source_path)
        first_hash = sha256_json(first.to_dict())
        if first_hash != sha256_json(second.to_dict()):
            raise ValueError(f"source load is nondeterministic: {source_path}")
        if first_hash != source["event_graph_sha256"]:
            raise ValueError(f"normalized source graph hash mismatch: {source_path}")
        archive = ArchiveStore(source_path.parent / "archive")
        failures = archive.verify_all()
        if failures:
            raise ValueError(f"archive integrity failures: {source_path}")
        graphs[session_id] = first

    rows: list[dict[str, Any]] = []
    prefixes = manifest["prefixes"]
    for index, prefix_row in enumerate(prefixes, start=1):
        prefix_id = str(prefix_row["prefix_id"])
        session_id = str(prefix_row["session_id"])
        schemas = tuple(
            dict(item)
            for item in schemas_artifact["domains"][prefix_row["domain"]]
        )
        rows.append(
            _evaluate_prefix(
                prefix_row,
                source=source_rows[session_id],
                graph=graphs[session_id],
                schemas=schemas,
                frozen_phase5_row=frozen_rows[prefix_id],
                config=config,
                model=args.model,
                hard_context_limit=args.hard_context_limit,
            )
        )
        if index % 25 == 0 or index == len(prefixes):
            print(f"audited {index}/{len(prefixes)} frozen prefixes", flush=True)

    eligible = [row for row in rows if row["cost_analysis_eligible"]]
    metrics = {
        "prefix_count": len(rows),
        "eligible_prefix_count": len(eligible),
        "frozen_prefix_hash_match_rate": _rate(rows, "prefix_hash_match"),
        "frozen_phase5_baseline_match_rate": _rate(
            rows,
            "frozen_phase5_baseline_match",
        ),
        "deterministic_rate": _rate(rows, "deterministic_artifacts"),
        "future_suffix_independence_rate": _rate(
            rows,
            "future_suffix_independent",
        ),
        "grade_a_protocol_valid_rate": _rate(rows, "grade_a_protocol_valid"),
        "ceiling_protocol_valid_rate": _rate(rows, "ceiling_protocol_valid"),
        "grade_a_request_hash_match_rate": _rate(
            rows,
            "grade_a_request_hash_match",
        ),
        "ceiling_request_hash_match_rate": _rate(
            rows,
            "ceiling_request_hash_match",
        ),
        "grade_a_false_dead": sum(int(row["grade_a_false_dead"]) for row in rows),
        "grade_a_policy_false_dead": sum(
            int(row["grade_a_policy_false_dead"]) for row in rows
        ),
        "grade_a_confirmation_false_dead": sum(
            int(row["grade_a_confirmation_false_dead"]) for row in rows
        ),
        "grade_a_receipt_false_dead": sum(
            int(row["grade_a_receipt_false_dead"]) for row in rows
        ),
        "phase5_reduced_prefix_count_eligible": sum(
            int(row["phase5_reduced"]) for row in eligible
        ),
        "grade_a_reduced_prefix_count_eligible": sum(
            int(row["grade_a_reduced"]) for row in eligible
        ),
        "ceiling_reduced_prefix_count_eligible": sum(
            int(row["ceiling_reduced"]) for row in eligible
        ),
        "phase5_paired_median_delta_eligible": _median(
            eligible,
            "phase5_delta_from_raw",
        ),
        "grade_a_paired_median_delta_eligible": _median(
            eligible,
            "grade_a_delta_from_raw",
        ),
        "ceiling_paired_median_delta_eligible": _median(
            eligible,
            "ceiling_delta_from_raw",
        ),
        "external_provider_generations": 0,
    }
    summary: dict[str, Any] = {
        "schema_version": "phase51_evidence_ceiling_summary_v1",
        "phase5_f5_g1_decision_unchanged": "no_go",
        "development_manifest_sha256": manifest["manifest_sha256"],
        "tool_schema_artifact_sha256": schemas_artifact["artifact_sha256"],
        "phase5_replay_manifest_sha256": replay_manifest["run_manifest_sha256"],
        "phase5_gate_sha256": phase5_gate["gate_report_sha256"],
        "config_file_sha256": file_sha256(config_path),
        "config_sha256": evidence_config_sha256(config),
        "model_identity_for_serialization_only": args.model,
        "hard_context_limit": args.hard_context_limit,
        "counts": {
            "prefixes": len(rows),
            "sources": len(graphs),
            "eligible_prefixes": len(eligible),
            "grade_a_records": sum(int(row["grade_a_record_count"]) for row in rows),
            "grade_a_hard_dead_records": sum(
                int(row["grade_a_hard_dead_record_count"]) for row in rows
            ),
            "grade_b_records": sum(int(row["grade_b_record_count"]) for row in rows),
            "prefixes_with_grade_a_hard_dead": sum(
                int(row["grade_a_hard_dead_record_count"] > 0) for row in rows
            ),
            "prefixes_with_grade_b_candidates": sum(
                int(row["grade_b_record_count"] > 0) for row in rows
            ),
        },
        "cost": {
            "median_raw_serialized_tokens_eligible": _median(
                eligible,
                "raw_serialized_tokens",
            ),
            "median_phase5_serialized_tokens_eligible": _median(
                eligible,
                "phase5_serialized_tokens",
            ),
            "median_grade_a_serialized_tokens_eligible": _median(
                eligible,
                "grade_a_serialized_tokens",
            ),
            "median_ceiling_serialized_tokens_eligible": _median(
                eligible,
                "ceiling_serialized_tokens",
            ),
        },
        "metrics": metrics,
        "ceiling_projection_emitted_to_provider": False,
        "task_reward_accessed": False,
        "treatment_outcomes_accessed": False,
        "external_provider_generations": 0,
    }
    summary["summary_sha256"] = sha256_json(summary)
    gate = _adjudicate(metrics, config["gates"])
    gate.update(
        {
            "summary_sha256": summary["summary_sha256"],
            "config_sha256": evidence_config_sha256(config),
            "frozen_gates": dict(config["gates"]),
            "external_provider_generations": 0,
        }
    )
    gate["gate_report_sha256"] = sha256_json(gate)

    output_root.mkdir(parents=True, exist_ok=False)
    rows_jsonl = output_root / "prefix_rows.jsonl"
    rows_csv = output_root / "prefix_rows.csv"
    summary_path = output_root / "summary.json"
    gate_path = output_root / "p51_g0_gate.json"
    _write_new_jsonl(rows_jsonl, rows)
    _write_new_csv(rows_csv, rows)
    _write_new_json(summary_path, summary)
    _write_new_json(gate_path, gate)
    run_manifest: dict[str, Any] = {
        "schema_version": "phase51_evidence_ceiling_run_manifest_v1",
        "config_path": config_path.as_posix(),
        "config_file_sha256": file_sha256(config_path),
        "files": [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in (rows_jsonl, rows_csv, summary_path, gate_path)
        ],
        "phase5_f5_g1_decision_unchanged": "no_go",
        "external_provider_generations": 0,
    }
    run_manifest["run_manifest_sha256"] = sha256_json(run_manifest)
    _write_new_json(output_root / "run_manifest.json", run_manifest)
    print(
        json.dumps(
            {
                "decision": gate["decision"],
                "metrics": metrics,
                "output_root": output_root.as_posix(),
                "run_manifest_sha256": run_manifest["run_manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
