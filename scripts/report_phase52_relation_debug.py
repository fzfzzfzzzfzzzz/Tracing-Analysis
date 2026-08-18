#!/usr/bin/env python3
"""Report a partial relation-first Phase 5.2 debugging run without opening gates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tracegraph.lifecycle_annotation import (  # noqa: E402
    cohen_kappa_binary,
    consensus_labels,
    load_phase52_config,
)
from tracegraph.trajectory_artifacts import sha256_json  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT
        / "configs/phase52_lifecycle_modeling_qwen37plus_relation_first.json",
    )
    parser.add_argument(
        "--target-prefix-id",
        default="dp_28b030eb33461bc979ad1633",
    )
    args = parser.parse_args()
    config = load_phase52_config(args.config)
    output_root = REPO_ROOT / str(config["output_root"])
    index = _load_jsonl(output_root / "request_index.jsonl")
    ledger = _load_jsonl(output_root / "usage_ledger.jsonl")
    row_by_prefix = {str(row["prefix_id"]): row for row in index}
    labels_by_prefix: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for path in sorted((output_root / "labels").glob("*.json")):
        artifact = _load_json(path)
        labels_by_prefix[str(artifact["prefix_id"])][str(artifact["pass_id"])] = list(
            artifact["labels_original_ids"]
        )

    fields = (
        "terminal_reason",
        "required_for_current_target",
        "requirement_uncertain",
        "relation_target_ids",
        "obligations",
        "reactivation_risk",
    )
    field_agreement = {field: 0 for field in fields}
    a_safe: list[bool] = []
    b_safe: list[bool] = []
    full_consensus = 0
    consensus_safe = 0
    consensus_uncertain = 0
    per_prefix: list[dict[str, Any]] = []
    for prefix_id in sorted(labels_by_prefix):
        passes = labels_by_prefix[prefix_id]
        if set(passes) != {"pass_a", "pass_b"}:
            continue
        a_map = {str(item["span_id"]): item for item in passes["pass_a"]}
        b_map = {str(item["span_id"]): item for item in passes["pass_b"]}
        consensus = consensus_labels(passes["pass_a"], passes["pass_b"])
        safe_agreements = 0
        for span_id in sorted(a_map):
            a_item = a_map[span_id]
            b_item = b_map[span_id]
            a_is_safe = a_item["disposition"] == "safe_to_evict"
            b_is_safe = b_item["disposition"] == "safe_to_evict"
            a_safe.append(a_is_safe)
            b_safe.append(b_is_safe)
            safe_agreements += int(a_is_safe == b_is_safe)
            for field in fields:
                field_agreement[field] += int(a_item[field] == b_item[field])
        row = row_by_prefix[prefix_id]
        prefix_full = sum(int(item["machine_consensus"]) for item in consensus)
        prefix_safe = sum(
            int(item["disposition"] == "safe_to_evict") for item in consensus
        )
        full_consensus += prefix_full
        consensus_safe += prefix_safe
        consensus_uncertain += sum(
            int(item["disposition"] == "uncertain") for item in consensus
        )
        per_prefix.append(
            {
                "prefix_id": prefix_id,
                "domain": row["domain"],
                "task_id": row["task_id"],
                "span_count": len(a_map),
                "safe_binary_agreement": safe_agreements / len(a_map),
                "full_field_consensus": prefix_full,
                "consensus_safe": prefix_safe,
            }
        )

    span_units = len(a_safe)
    pricing = _load_json(output_root / "pricing_snapshot.json")
    prompt_tokens = sum(int(row["usage"]["prompt_tokens"]) for row in ledger)
    completion_tokens = sum(
        int(row["usage"]["completion_tokens"]) for row in ledger
    )
    valid_requests = sum(int(row["valid"]) for row in ledger)
    report: dict[str, Any] = {
        "schema_version": "phase52_relation_first_debug_report_v1",
        "condition_id": config["model"]["condition_id"],
        "model": config["model"]["report_identity"],
        "status": "format_fixed_semantic_stability_insufficient",
        "counts": {
            "attempts": len(ledger),
            "valid_requests": valid_requests,
            "invalid_attempts": len(ledger) - valid_requests,
            "repair_requests": sum(
                int(bool(row.get("repair_request_file"))) for row in ledger
            ),
            "complete_double_pass_prefixes": len(per_prefix),
            "span_units": span_units,
        },
        "diagnostic_metrics": {
            "safe_binary_agreement": (
                sum(a == b for a, b in zip(a_safe, b_safe, strict=True))
                / span_units
            ),
            "cohen_kappa": cohen_kappa_binary(a_safe, b_safe),
            "pass_a_safe": sum(a_safe),
            "pass_b_safe": sum(b_safe),
            "full_field_consensus": full_consensus,
            "consensus_safe": consensus_safe,
            "consensus_uncertain": consensus_uncertain,
            "field_agreement": {
                field: field_agreement[field] / span_units for field in fields
            },
            "per_prefix": per_prefix,
        },
        "targeted_regression": {
            "prefix_id": args.target_prefix_id,
            "both_passes_valid": set(labels_by_prefix.get(args.target_prefix_id, {}))
            == {"pass_a", "pass_b"},
        },
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "list_price_cost_usd": (
                prompt_tokens / 1_000_000 * float(pricing["input_usd_per_mtok"])
                + completion_tokens
                / 1_000_000
                * float(pricing["output_usd_per_mtok"])
            ),
        },
        "formal_pseudolabel_gate_computable": False,
        "quality_claim_authorized": False,
        "external_behavior_experiment_authorized": False,
        "recommended_next_step": (
            "new_create_only_chunked_labeling_debug_condition_requires_approval"
        ),
        "old_no_go_preserved": True,
    }
    report["report_sha256"] = sha256_json(report)
    reports = output_root / "debug_reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / "relation_first_debug_0001.json"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"path": path.as_posix(), **report}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
