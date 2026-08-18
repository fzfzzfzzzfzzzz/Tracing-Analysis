#!/usr/bin/env python3
"""Freeze all blind Phase 5.2 annotation requests without calling a provider."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tracegraph.graph import TraceGraph  # noqa: E402
from tracegraph.lifecycle_annotation import (  # noqa: E402
    annotation_response_function_schema,
    config_sha256,
    file_sha256,
    load_phase52_config,
    prepare_annotation_request,
)
from tracegraph.phase5_offline import build_strict_prefix  # noqa: E402
from tracegraph.trajectory_artifacts import sha256_json  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_new_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_new_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs/phase52_lifecycle_modeling.json",
    )
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = load_phase52_config(config_path)
    output_root = REPO_ROOT / str(config["output_root"])
    if output_root.exists():
        raise FileExistsError(f"Phase 5.2 output is create-only: {output_root}")

    manifest_path = REPO_ROOT / str(config["inputs"]["development_manifest"])
    schemas_path = REPO_ROOT / str(config["inputs"]["tool_schemas"])
    manifest = _load_json(manifest_path)
    schemas = _load_json(schemas_path)
    if manifest.get("manifest_sha256") != config["inputs"]["development_manifest_sha256"]:
        raise ValueError("development manifest hash is not the frozen Phase 5 input")
    manifest_body = dict(manifest)
    declared_manifest_hash = manifest_body.pop("manifest_sha256")
    if sha256_json(manifest_body) != declared_manifest_hash:
        raise ValueError("development manifest embedded hash mismatch")
    if schemas.get("artifact_sha256") != config["inputs"]["tool_schemas_sha256"]:
        raise ValueError("tool schema hash is not the frozen Phase 5 input")
    schema_body = dict(schemas)
    declared_schema_hash = schema_body.pop("artifact_sha256")
    if sha256_json(schema_body) != declared_schema_hash:
        raise ValueError("tool schema embedded hash mismatch")

    source_rows = {str(item["session_id"]): item for item in manifest["sources"]}
    graphs: dict[str, TraceGraph] = {}
    for session_id, source in source_rows.items():
        source_path = REPO_ROOT / str(source["source_path"])
        if file_sha256(source_path) != source["source_file_sha256"]:
            raise ValueError(f"source graph file drift: {source_path}")
        graph = TraceGraph.load(source_path)
        if sha256_json(graph.to_dict()) != source["event_graph_sha256"]:
            raise ValueError(f"normalized source graph drift: {source_path}")
        graphs[session_id] = graph

    output_root.mkdir(parents=True, exist_ok=False)
    request_rows: list[dict[str, Any]] = []
    no_opportunity: list[dict[str, Any]] = []
    span_units = 0
    splits: Counter[str] = Counter()
    observed_tools: set[str] = set()
    for prefix_row in manifest["prefixes"]:
        prefix_id = str(prefix_row["prefix_id"])
        session_id = str(prefix_row["session_id"])
        source_ordinal = prefix_row.get("source_message_ordinal")
        prefix = build_strict_prefix(
            graphs[session_id],
            cutoff_step=int(prefix_row["cutoff_step"]),
            source_message_ordinal=(int(source_ordinal) if source_ordinal is not None else None),
            prefix_id=prefix_id,
        )
        span_count = int(prefix_row["structural_features"]["complete_tool_span_count"])
        task_id = str(prefix_row["task_id"])
        if task_id in set(config["split"]["development_task_ids"]):
            split = "development"
        elif task_id in set(config["split"]["calibration_task_ids"]):
            split = "calibration"
        elif task_id in set(config["split"]["held_out_task_ids"]):
            split = "held_out"
        else:
            raise ValueError(f"unassigned task: {task_id}")
        splits[split] += 1
        if span_count == 0:
            no_opportunity.append(
                {
                    "prefix_id": prefix_id,
                    "session_id": session_id,
                    "domain": prefix_row["domain"],
                    "task_id": task_id,
                    "split": split,
                    "status": "no_opportunity_no_historical_tool_span",
                    "provider_requests": 0,
                }
            )
            continue
        domain_schemas = schemas["domains"][prefix_row["domain"]]
        for pass_id in config["annotation"]["passes"]:
            prepared = prepare_annotation_request(
                prefix=prefix,
                prefix_row=prefix_row,
                tool_schemas=domain_schemas,
                pass_id=str(pass_id),
                config=config,
            )
            if prepared.span_count != span_count:
                raise ValueError(f"tool-span count drift: {prefix_id}")
            request_path = output_root / "requests" / f"{prepared.request_id}.json"
            mapping_path = output_root / "mappings" / f"{prepared.request_id}.json"
            _write_new_json(request_path, prepared.request)
            _write_new_json(mapping_path, prepared.mapping)
            row = {
                **prepared.to_index_row(),
                "session_id": session_id,
                "domain": prefix_row["domain"],
                "task_id": task_id,
                "request_file": request_path.relative_to(output_root).as_posix(),
                "request_file_sha256": file_sha256(request_path),
                "mapping_file": mapping_path.relative_to(output_root).as_posix(),
                "mapping_file_sha256": file_sha256(mapping_path),
            }
            request_rows.append(row)
        span_units += span_count
        for node in prefix.nodes.values():
            if node.node_type.value in {"tool_call", "mcp_call"}:
                content = node.content if isinstance(node.content, dict) else {}
                name = str(node.metadata.get("tool_name") or content.get("tool_name") or "")
                if name:
                    observed_tools.add(name)

    annotation = config["annotation"]
    eligible_count = len({row["prefix_id"] for row in request_rows})
    if eligible_count != annotation["expected_eligible_prefixes"]:
        raise ValueError("eligible prefix population drift")
    if len(no_opportunity) != annotation["expected_no_opportunity_prefixes"]:
        raise ValueError("zero-span prefix population drift")
    if span_units != annotation["expected_prefix_span_units"]:
        raise ValueError("prefix-span unit population drift")
    if len(request_rows) != eligible_count * 2:
        raise ValueError("double-pass request population is incomplete")
    if sum(item["estimated_input_tokens"] for item in request_rows) > annotation[
        "estimated_input_tokens_hard_max"
    ]:
        raise RuntimeError("frozen requests exceed the input-token hard cap")
    registered = {str(item["tool_name"]) for item in config["tool_effect_specs"]}
    if observed_tools != registered:
        raise ValueError(
            f"ToolEffectSpec coverage mismatch: observed={sorted(observed_tools)}, "
            f"registered={sorted(registered)}"
        )

    frozen_config = {**config, "config_sha256": config_sha256(config)}
    _write_new_json(output_root / "frozen_config.json", frozen_config)
    pricing = dict(config["pricing_snapshot"])
    pricing["snapshot_sha256"] = sha256_json(pricing)
    _write_new_json(output_root / "pricing_snapshot.json", pricing)
    _write_new_json(
        output_root / "machine_label_function_schema.json",
        annotation_response_function_schema(["S001"], config)["function"][
            "parameters"
        ],
    )
    _write_new_jsonl(output_root / "request_index.jsonl", request_rows)
    _write_new_jsonl(output_root / "no_opportunity_prefixes.jsonl", no_opportunity)
    summary: dict[str, Any] = {
        "schema_version": "phase52_annotation_preparation_summary_v1",
        "config_sha256": frozen_config["config_sha256"],
        "development_manifest_sha256": declared_manifest_hash,
        "tool_schema_artifact_sha256": declared_schema_hash,
        "counts": {
            "all_prefixes": len(manifest["prefixes"]),
            "eligible_prefixes": eligible_count,
            "no_opportunity_prefixes": len(no_opportunity),
            "requests": len(request_rows),
            "prefix_span_units_per_pass": span_units,
            "double_pass_span_labels_expected": span_units * 2,
            "tool_effect_specs": len(registered),
        },
        "splits_all_prefixes": dict(sorted(splits.items())),
        "estimated_input_tokens": sum(
            int(item["estimated_input_tokens"]) for item in request_rows
        ),
        "limits": {
            "request_count_hard_max": annotation["request_count_hard_max"],
            "estimated_input_tokens_hard_max": annotation[
                "estimated_input_tokens_hard_max"
            ],
            "actual_output_tokens_hard_max": annotation[
                "actual_output_tokens_hard_max"
            ],
        },
        "prefix_only_validated": True,
        "provider_requests": 0,
        "external_behavior_model_sessions": 0,
    }
    summary["summary_sha256"] = sha256_json(summary)
    _write_new_json(output_root / "preparation_summary.json", summary)
    files = [path for path in sorted(output_root.rglob("*")) if path.is_file()]
    preparation_manifest: dict[str, Any] = {
        "schema_version": "phase52_annotation_preparation_manifest_v1",
        "files": [_file_record(output_root, path) for path in files],
        "provider_requests": 0,
        "create_only": True,
    }
    preparation_manifest["manifest_sha256"] = sha256_json(preparation_manifest)
    _write_new_json(output_root / "preparation_manifest.json", preparation_manifest)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
