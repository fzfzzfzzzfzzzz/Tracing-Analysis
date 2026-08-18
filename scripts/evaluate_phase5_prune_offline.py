#!/usr/bin/env python3
"""Run outcome-blind FullRaw/GDSC-Prune reconstruction for F5-E0/F5-G1."""

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
from tracegraph.integrations.lifecycle_graph_context import (
    LifecycleGraphContextManager,
)
from tracegraph.lifecycle_context import ContextView
from tracegraph.liveness import (
    DecisionLifecycleGraph,
    LivenessRoots,
    LiveSubgraph,
)
from tracegraph.phase5_offline import (
    adjudicate_f5_g1,
    build_strict_prefix,
    file_sha256,
    policy_text,
    prefix_messages,
    strict_predecision_nodes,
    structural_features,
)
from tracegraph.provider_cost import (
    ProviderProtocol,
    request_sha256,
    serialized_request_cost,
)
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


def _artifact_round_trip(compilation: Any) -> bool:
    state = DecisionLifecycleGraph.from_dict(compilation.state.to_dict())
    roots = LivenessRoots.from_dict(compilation.roots.to_dict())
    live = LiveSubgraph.from_dict(compilation.live_subgraph.to_dict())
    view = ContextView.from_dict(compilation.context_view.to_dict())
    return (
        state.lifecycle_hash == compilation.state.lifecycle_hash
        and roots.roots_hash == compilation.roots.roots_hash
        and live.live_subgraph_hash
        == compilation.live_subgraph.live_subgraph_hash
        and view.context_view_hash == compilation.context_view.context_view_hash
    )


def _compile_hashes(compilation: Any) -> dict[str, str]:
    return {
        "state": compilation.state.lifecycle_hash,
        "query": compilation.query.query_hash,
        "roots": compilation.roots.roots_hash,
        "live_subgraph": compilation.live_subgraph.live_subgraph_hash,
        "context_view": compilation.context_view.context_view_hash,
        "request": compilation.context_view.request_hash,
    }


def _future_suffix_independent(
    prefix: TraceGraph,
    compilation: Any,
    *,
    manager: LifecycleGraphContextManager,
    messages: Sequence[Mapping[str, Any]],
    system_rules: Sequence[str],
    schemas: Sequence[Mapping[str, Any]],
    archive: ArchiveStore,
) -> bool:
    future = TraceGraph.from_dict(prefix.to_dict())
    future.create_node(
        NodeType.SUMMARY,
        {"phase5_future_suffix_sentinel": True},
        compilation.state.cutoff_step + 100,
        node_id=f"f5_future_{compilation.query.query_hash[:20]}",
        metadata={"source_message_ordinal": len(messages) + 100},
    )
    future_compilation = manager.compile(
        future,
        messages=messages,
        system_rules=system_rules,
        tool_schemas=schemas,
        cutoff=compilation.state.cutoff_step,
        query=compilation.query,
        archive_reader=archive.get,
    )
    return _compile_hashes(future_compilation) == _compile_hashes(compilation)


def _reactivation_audit(
    prefix: TraceGraph,
    compilation: Any,
    *,
    manager: LifecycleGraphContextManager,
    messages: Sequence[Mapping[str, Any]],
    system_rules: Sequence[str],
    schemas: Sequence[Mapping[str, Any]],
    archive: ArchiveStore,
) -> tuple[int, int]:
    span_map = compilation.live_subgraph.span_map()
    passed = 0
    total = 0
    for span_id in compilation.context_view.evicted_span_ids:
        total += 1
        span = span_map[span_id]
        reference_event = span.node_ids[0]
        query = replace(
            compilation.query,
            referenced_event_ids=tuple(
                sorted(
                    {
                        *compilation.query.referenced_event_ids,
                        reference_event,
                    }
                )
            ),
        )
        reactivated = manager.compile(
            prefix,
            messages=messages,
            system_rules=system_rules,
            tool_schemas=schemas,
            query=query,
            archive_reader=archive.get,
        )
        live_nodes = set(reactivated.context_view.live_node_ids)
        archive_ok = True
        for node_id in span.node_ids:
            node = prefix.nodes[node_id]
            if node.raw_ref:
                # The archive retains the raw provider envelope while graph
                # nodes intentionally contain normalized call/result content.
                # ArchiveStore.get verifies the recovered payload against the
                # SHA-256 handle; semantic equality to node.content is neither
                # expected nor a valid integrity invariant.
                archive.get(node.raw_ref, verify=True)
        if (
            set(span.node_ids).issubset(live_nodes)
            and span_id not in reactivated.context_view.evicted_span_ids
            and archive_ok
        ):
            passed += 1
    return passed, total


def _evaluate_prefix(
    prefix_row: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    graph: TraceGraph,
    schemas: Sequence[Mapping[str, Any]],
    model: str,
    hard_context_limit: int,
) -> dict[str, Any]:
    prefix_id = str(prefix_row["prefix_id"])
    cutoff_step = int(prefix_row["cutoff_step"])
    ordinal_value = prefix_row.get("source_message_ordinal")
    source_ordinal = int(ordinal_value) if ordinal_value is not None else None
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
    prefix_hash_match = recomputed_prefix_hash == prefix_row["prefix_sha256"]
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
    if structural_features(prefix, archive=archive) != prefix_row[
        "structural_features"
    ]:
        raise ValueError(f"structural-feature drift: {prefix_id}")

    manager = LifecycleGraphContextManager(
        model=model,
        hard_context_limit=hard_context_limit,
    )
    first = manager.compile(
        prefix,
        messages=messages,
        system_rules=system_rules,
        tool_schemas=schemas,
        archive_reader=archive.get,
    )
    second = manager.compile(
        prefix,
        messages=messages,
        system_rules=system_rules,
        tool_schemas=schemas,
        archive_reader=archive.get,
    )
    deterministic = (
        _compile_hashes(first) == _compile_hashes(second)
        and first.to_dict() == second.to_dict()
        and _artifact_round_trip(first)
    )
    future_independent = _future_suffix_independent(
        prefix,
        first,
        manager=manager,
        messages=messages,
        system_rules=system_rules,
        schemas=schemas,
        archive=archive,
    )

    raw_protocol = ProviderProtocol(
        model=model,
        system_rules=tuple(system_rules),
        base_messages=messages,
        tools=tuple(dict(item) for item in schemas),
        hard_context_limit=hard_context_limit,
    )
    raw_messages = _raw_messages(system_rules, messages)
    raw_request = raw_protocol.request(raw_messages)
    raw_tokens = serialized_request_cost(raw_protocol, raw_messages)
    view = first.context_view
    request_hash_match = request_sha256(view.request) == view.request_hash
    try:
        view.assert_sent_request(view.request)
    except ValueError:
        request_hash_match = False

    live_ids = set(view.live_node_ids)
    evicted_ids = set(view.evicted_node_ids)
    root_ids = set(first.roots.root_event_ids)
    hard_ids = _source_event_ids(first, hard_only=True)
    policy_ids = _source_event_ids(first, atom_types=_POLICY_ATOM_TYPES)
    policy_ids.update(
        node.node_id
        for node in prefix.find_nodes(node_types={NodeType.CONSTRAINT})
    )
    confirmation_ids = _source_event_ids(
        first,
        atom_types=_CONFIRMATION_ATOM_TYPES,
    )
    receipt_ids = _source_event_ids(first, atom_types=_RECEIPT_ATOM_TYPES)
    critical_ids = root_ids | hard_ids | policy_ids | confirmation_ids | receipt_ids
    reactivation_passed, reactivation_total = _reactivation_audit(
        prefix,
        first,
        manager=manager,
        messages=messages,
        system_rules=system_rules,
        schemas=schemas,
        archive=archive,
    )
    prune_tokens = view.costs.serialized_request
    return {
        "prefix_id": prefix_id,
        "session_id": str(prefix_row["session_id"]),
        "domain": str(prefix_row["domain"]),
        "task_id": str(prefix_row["task_id"]),
        "trial": prefix_row.get("trial"),
        "prefix_hash_match": prefix_hash_match,
        "deterministic_artifacts": deterministic,
        "future_suffix_independent": future_independent,
        "protocol_valid": view.protocol_valid,
        "send_eligible": view.send_eligible,
        "request_hash_match": request_hash_match,
        "raw_request_hash": request_sha256(raw_request),
        "prune_request_hash": view.request_hash,
        "state_hash": first.state.lifecycle_hash,
        "roots_hash": first.roots.roots_hash,
        "live_subgraph_hash": first.live_subgraph.live_subgraph_hash,
        "context_view_hash": view.context_view_hash,
        "root_event_expected": len(root_ids),
        "root_event_live": len(root_ids & live_ids),
        "critical_event_expected": len(critical_ids),
        "critical_event_live": len(critical_ids & live_ids),
        "policy_false_dead": len(policy_ids & evicted_ids),
        "confirmation_false_dead": len(confirmation_ids & evicted_ids),
        "side_effect_receipt_false_dead": len(receipt_ids & evicted_ids),
        "reactivation_total": reactivation_total,
        "reactivation_passed": reactivation_passed,
        "message_count_raw": len(raw_messages),
        "message_count_prune": len(view.messages),
        "evicted_span_count": len(view.evicted_span_ids),
        "evicted_node_count": len(view.evicted_node_ids),
        "fallback_count": len(view.fallback_records),
        "uncertainty_count": len(view.uncertainty_records),
        "raw_serialized_tokens": raw_tokens,
        "prune_serialized_tokens": prune_tokens,
        "serialized_token_delta": prune_tokens - raw_tokens,
        "serialized_reduction_fraction": (
            (raw_tokens - prune_tokens) / raw_tokens if raw_tokens else 0.0
        ),
        "cost_analysis_eligible": bool(
            prefix_row["structural_features"]["cost_analysis_eligible"]
        ),
        "reduced": prune_tokens < raw_tokens,
        "external_provider_generations": 0,
    }


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return statistics.fmean(float(bool(row[key])) for row in rows)


def _recall(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_key: str,
    observed_key: str,
) -> float:
    expected = sum(int(row[expected_key]) for row in rows)
    observed = sum(int(row[observed_key]) for row in rows)
    return observed / expected if expected else 1.0


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows]
    return statistics.median(values) if values else 0.0


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")


def _write_new_jsonl(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )


def _write_new_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    _configure_utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", default="zai/glm-4.7-flash")
    parser.add_argument("--hard-context-limit", type=int, default=128_000)
    args = parser.parse_args()

    manifest_root = args.manifest_root.resolve()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise FileExistsError(
            f"refusing to overwrite existing Phase 5 output: {output_root}"
        )
    manifest_path = manifest_root / "development_prefix_manifest.json"
    schemas_path = manifest_root / "tool_schemas.json"
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
    if (
        schemas_artifact["artifact_sha256"]
        != manifest["inputs"]["tool_schema_artifact_sha256"]
    ):
        raise ValueError("manifest/tool-schema artifact hash mismatch")

    source_rows = {
        str(item["session_id"]): item for item in manifest["sources"]
    }
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
                model=args.model,
                hard_context_limit=args.hard_context_limit,
            )
        )
        if index % 25 == 0 or index == len(prefixes):
            print(
                f"evaluated {index}/{len(prefixes)} frozen prefixes",
                flush=True,
            )

    eligible = [row for row in rows if row["cost_analysis_eligible"]]
    reactivation_total = sum(int(row["reactivation_total"]) for row in rows)
    reactivation_passed = sum(
        int(row["reactivation_passed"]) for row in rows
    )
    expected_ids = {str(item["prefix_id"]) for item in prefixes}
    observed_ids = {str(item["prefix_id"]) for item in rows}
    metrics = {
        "all_frozen_prefixes_included": (
            len(rows) == len(prefixes) and observed_ids == expected_ids
        ),
        "source_load_determinism_rate": 1.0,
        "frozen_prefix_hash_match_rate": _rate(rows, "prefix_hash_match"),
        "deterministic_artifact_rate": _rate(
            rows,
            "deterministic_artifacts",
        ),
        "future_suffix_independence_rate": _rate(
            rows,
            "future_suffix_independent",
        ),
        "protocol_valid_rate": _rate(rows, "protocol_valid"),
        "root_event_recall": _recall(
            rows,
            expected_key="root_event_expected",
            observed_key="root_event_live",
        ),
        "critical_event_recall": _recall(
            rows,
            expected_key="critical_event_expected",
            observed_key="critical_event_live",
        ),
        "archive_reactivation_rate": (
            reactivation_passed / reactivation_total
            if reactivation_total
            else 1.0
        ),
        "request_hash_match_rate": _rate(rows, "request_hash_match"),
        "policy_false_dead": sum(
            int(row["policy_false_dead"]) for row in rows
        ),
        "confirmation_false_dead": sum(
            int(row["confirmation_false_dead"]) for row in rows
        ),
        "side_effect_receipt_false_dead": sum(
            int(row["side_effect_receipt_false_dead"]) for row in rows
        ),
        "cost_analysis_eligible": len(eligible),
        "reduced_prefix_count": sum(int(row["reduced"]) for row in eligible),
        "paired_median_serialized_token_delta": _median(
            eligible,
            "serialized_token_delta",
        ),
        "external_provider_generations": 0,
    }
    summary: dict[str, Any] = {
        "schema_version": "phase5_prune_offline_summary_v1",
        "development_manifest_sha256": manifest["manifest_sha256"],
        "tool_schema_artifact_sha256": schemas_artifact["artifact_sha256"],
        "model_identity_for_serialization": args.model,
        "hard_context_limit": args.hard_context_limit,
        "counts": {
            "prefixes": len(rows),
            "sources": len(graphs),
            "cost_analysis_eligible": len(eligible),
            "prefixes_with_eviction": sum(
                int(row["evicted_span_count"] > 0) for row in rows
            ),
            "evicted_spans": sum(
                int(row["evicted_span_count"]) for row in rows
            ),
            "protocol_fallbacks": sum(
                int(row["fallback_count"]) for row in rows
            ),
            "reactivation_checks": reactivation_total,
        },
        "cost": {
            "median_raw_serialized_tokens_eligible": _median(
                eligible,
                "raw_serialized_tokens",
            ),
            "median_prune_serialized_tokens_eligible": _median(
                eligible,
                "prune_serialized_tokens",
            ),
            "median_paired_serialized_token_delta_eligible": _median(
                eligible,
                "serialized_token_delta",
            ),
            "median_serialized_reduction_fraction_eligible": _median(
                eligible,
                "serialized_reduction_fraction",
            ),
        },
        "metrics": metrics,
        "external_provider_generations": 0,
    }
    summary["summary_sha256"] = sha256_json(summary)
    gate = adjudicate_f5_g1(metrics, manifest["f5_g1_thresholds"])
    gate.update(
        {
            "development_manifest_sha256": manifest["manifest_sha256"],
            "summary_sha256": summary["summary_sha256"],
            "frozen_thresholds": dict(manifest["f5_g1_thresholds"]),
            "external_provider_generations": 0,
        }
    )
    gate["gate_report_sha256"] = sha256_json(gate)

    output_root.mkdir(parents=True, exist_ok=False)
    rows_jsonl = output_root / "prefix_rows.jsonl"
    rows_csv = output_root / "prefix_rows.csv"
    summary_path = output_root / "summary.json"
    gate_path = output_root / "f5_g1_gate.json"
    _write_new_jsonl(rows_jsonl, rows)
    _write_new_csv(rows_csv, rows)
    _write_new_json(summary_path, summary)
    _write_new_json(gate_path, gate)
    run_manifest: dict[str, Any] = {
        "schema_version": "phase5_prune_offline_run_manifest_v1",
        "development_manifest_path": manifest_path.as_posix(),
        "development_manifest_sha256": manifest["manifest_sha256"],
        "files": [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in (rows_jsonl, rows_csv, summary_path, gate_path)
        ],
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
    return 0 if gate["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
