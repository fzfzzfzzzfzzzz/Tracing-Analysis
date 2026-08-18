#!/usr/bin/env python3
"""Verify frozen Phase 5.2 requests, partial responses, budgets, and hashes."""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tracegraph.graph import TraceGraph  # noqa: E402
from tracegraph.lifecycle_annotation import (  # noqa: E402
    AnnotationBudget,
    file_sha256,
    load_phase52_config,
    prepare_annotation_request,
    prepare_validation_feedback_request,
)
from tracegraph.phase5_offline import build_strict_prefix  # noqa: E402
from tracegraph.trajectory_artifacts import sha256_json  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _embedded(path: Path, field: str) -> None:
    value = _load_json(path)
    declared = value.pop(field)
    if sha256_json(value) != declared:
        raise RuntimeError(f"embedded hash mismatch: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs/phase52_lifecycle_modeling.json",
    )
    args = parser.parse_args()
    config = load_phase52_config(args.config)
    output_root = REPO_ROOT / config["output_root"]
    preparation = _load_json(output_root / "preparation_manifest.json")
    declared_preparation = preparation.pop("manifest_sha256")
    if sha256_json(preparation) != declared_preparation:
        raise RuntimeError("preparation manifest embedded hash mismatch")
    for record in preparation["files"]:
        path = output_root / record["path"]
        if file_sha256(path) != record["sha256"] or path.stat().st_size != record["size_bytes"]:
            raise RuntimeError(f"prepared artifact drift: {path}")

    manifest = _load_json(REPO_ROOT / config["inputs"]["development_manifest"])
    schemas = _load_json(REPO_ROOT / config["inputs"]["tool_schemas"])
    index = _load_jsonl(output_root / "request_index.jsonl")
    index_map = {str(item["request_id"]): item for item in index}
    source_rows = {str(item["session_id"]): item for item in manifest["sources"]}
    graphs = {
        session_id: TraceGraph.load(REPO_ROOT / str(source["source_path"]))
        for session_id, source in source_rows.items()
    }
    regenerated = 0
    for row in manifest["prefixes"]:
        if not row["structural_features"]["complete_tool_span_count"]:
            continue
        ordinal = row.get("source_message_ordinal")
        prefix = build_strict_prefix(
            graphs[str(row["session_id"])],
            cutoff_step=int(row["cutoff_step"]),
            source_message_ordinal=int(ordinal) if ordinal is not None else None,
            prefix_id=str(row["prefix_id"]),
        )
        for pass_id in config["annotation"]["passes"]:
            request = prepare_annotation_request(
                prefix=prefix,
                prefix_row=row,
                tool_schemas=schemas["domains"][row["domain"]],
                pass_id=str(pass_id),
                config=config,
            )
            frozen = index_map[request.request_id]
            if request.request_sha256 != frozen["request_sha256"]:
                raise RuntimeError(f"request regeneration drift: {request.request_id}")
            mapping_path = output_root / frozen["mapping_file"]
            if request.mapping != _load_json(mapping_path):
                raise RuntimeError(f"opaque mapping regeneration drift: {request.request_id}")
            regenerated += 1
    if regenerated != 370:
        raise RuntimeError("request regeneration population drift")

    ledger = _load_jsonl(output_root / "usage_ledger.jsonl")
    budget = AnnotationBudget.from_ledger(ledger, limits=config["annotation"])
    if budget.request_count > budget.request_count_max:
        raise RuntimeError("request budget was exceeded")
    for position, item in enumerate(ledger):
        repair_file = item.get("repair_request_file")
        if not repair_file:
            provider_hash = item.get("provider_request_sha256")
            if provider_hash is not None:
                frozen = index_map[str(item["request_id"])]
                if provider_hash != frozen["request_sha256"]:
                    raise RuntimeError("base provider request hash mismatch")
            continue
        prior_invalid = [
            prior
            for prior in ledger[:position]
            if prior["request_id"] == item["request_id"]
            and 200 <= int(prior["http_status"]) < 300
            and not prior["valid"]
        ]
        if not prior_invalid:
            raise RuntimeError("repair request has no prior validation failure")
        frozen = index_map[str(item["request_id"])]
        expected = prepare_validation_feedback_request(
            _load_json(output_root / frozen["request_file"]),
            validation_error=str(prior_invalid[-1]["validation_error"]),
        )
        repair_path = output_root / str(repair_file)
        if _load_json(repair_path) != expected:
            raise RuntimeError(f"repair request regeneration drift: {repair_path}")
        if sha256_json(expected) != item.get("provider_request_sha256"):
            raise RuntimeError(f"repair request hash mismatch: {repair_path}")
    label_files = sorted((output_root / "labels").glob("*.json"))
    for path in label_files:
        value = _load_json(path)
        declared = value.pop("labels_sha256")
        if sha256_json(value) != declared:
            raise RuntimeError(f"label artifact embedded hash mismatch: {path}")
        raw_path = output_root / value["raw_response_file"]
        if file_sha256(raw_path) != value["raw_response_sha256"]:
            raise RuntimeError(f"label/raw response hash mismatch: {path}")
    _embedded(output_root / "state_machine_preflight.json", "report_sha256")
    for path in sorted((output_root / "pause_reports").glob("*.json")):
        _embedded(path, "report_sha256")
    for path in sorted((output_root / "debug_reports").glob("*.json")):
        _embedded(path, "report_sha256")
    print(
        json.dumps(
            {
                "status": "valid",
                "regenerated_requests": regenerated,
                "ledger_attempts": len(ledger),
                "valid_label_files": len(label_files),
                "prompt_tokens": budget.prompt_tokens,
                "output_tokens": budget.output_tokens,
                "pseudolabel_gate_available": (output_root / "pseudolabel_gate.json").exists(),
                "final_manifest_available": (output_root / "manifest.json").exists(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
