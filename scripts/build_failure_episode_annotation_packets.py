#!/usr/bin/env python3
"""Build answer-blind A/B packets for ``failure_episode_gold_v1`` annotation."""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.io import (
    canonical_json,
    file_sha256,
    load_jsonl,
    stable_digest,
)
from tracegraph.plain_cli import PlainArgumentParser


ANNOTATION_SCHEMA_VERSION = "compression_audit_failure_episode_annotation_v1"
PACKET_SCHEMA_VERSION = "compression_audit_failure_episode_packet_v1"


def write_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def blank_policy() -> dict[str, list]:
    return {
        "alternative_evidence_sets": [],
        "relevant_evidence_ids": [],
        "causal_paths": [],
    }


def blank_annotation(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "candidate_id": row["candidate_id"],
        "source": row["source"],
        "repository": row["repository"],
        "task_id": row["task_id"],
        "split": row["split"],
        "trajectory_revision": row["trajectory_revision"],
        "failure_episode": {
            "scope": "task_level_failure_episode",
            "failure_family": "",
            "recoverability": "",
            "error_signature": "",
            "diagnostic_evidence": "",
            "recovery_sequence": "",
            "resolution_evidence": "",
            "anchor_source_event_id": "",
            "initial_action_source_event_id": "",
            "initial_result_source_event_ids": [],
            "repair_steps": [],
            "resolution_source_event_ids": [],
            "required_core_source_event_ids": [],
            "optional_support_source_event_ids": [],
            "chain_policy": blank_policy(),
            "query_policies": {
                "audit_recovery": blank_policy(),
                "interactive_reacquisition": blank_policy(),
            },
        },
        "annotator": "",
        "annotator_kind": "human",
        "annotation_status": "blank",
        "rejection_reason": "",
    }


def reviewer_case(row: Mapping[str, Any]) -> dict[str, Any]:
    case = {
        key: value
        for key, value in dict(row).items()
        if key not in {
            "candidate_hints", "failure_chain", "annotator", "adjudicator",
            "annotation_status",
        }
    }
    case["annotation_protocol"] = ANNOTATION_SCHEMA_VERSION
    case["model_answers_in_packet"] = False
    case["heuristic_candidate_hints_in_packet"] = False
    return case


def annotation_schema() -> dict[str, Any]:
    event_ids = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
    policy = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "alternative_evidence_sets": {"type": "array", "items": event_ids},
            "relevant_evidence_ids": event_ids,
            "causal_paths": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "evidence_ids": event_ids,
                        "constraints": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                        },
                    },
                    "required": ["evidence_ids", "constraints"],
                },
            },
        },
        "required": [
            "alternative_evidence_sets", "relevant_evidence_ids", "causal_paths"
        ],
    }
    repair_step = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "step_id": {"type": "string"},
            "decision_source_event_ids": event_ids,
            "action_source_event_id": {"type": "string"},
            "result_source_event_ids": event_ids,
            "outcome": {"enum": ["intermediate_failure", "resolved"]},
            "semantic_change": {"type": "string"},
        },
        "required": [
            "step_id", "decision_source_event_ids", "action_source_event_id",
            "result_source_event_ids", "outcome", "semantic_change",
        ],
    }
    episode_properties = {
        "scope": {"const": "task_level_failure_episode"},
        "failure_family": {"type": "string"},
        "recoverability": {"enum": ["", "R0", "R1", "R2", "R3"]},
        "error_signature": {"type": "string"},
        "diagnostic_evidence": {"type": "string"},
        "recovery_sequence": {"type": "string"},
        "resolution_evidence": {"type": "string"},
        "anchor_source_event_id": {"type": "string"},
        "initial_action_source_event_id": {"type": "string"},
        "initial_result_source_event_ids": event_ids,
        "repair_steps": {"type": "array", "items": repair_step},
        "resolution_source_event_ids": event_ids,
        "required_core_source_event_ids": event_ids,
        "optional_support_source_event_ids": event_ids,
        "chain_policy": policy,
        "query_policies": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "audit_recovery": policy,
                "interactive_reacquisition": policy,
            },
            "required": ["audit_recovery", "interactive_reacquisition"],
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": ANNOTATION_SCHEMA_VERSION,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": ANNOTATION_SCHEMA_VERSION},
            "candidate_id": {"type": "string"},
            "source": {"type": "string"},
            "repository": {"type": "string"},
            "task_id": {"type": "string"},
            "split": {"enum": ["dev", "validation", "test"]},
            "trajectory_revision": {"type": "string"},
            "failure_episode": {
                "type": "object",
                "additionalProperties": False,
                "properties": episode_properties,
                "required": list(episode_properties),
            },
            "annotator": {"type": "string"},
            "annotator_kind": {"const": "human"},
            "annotation_status": {"enum": ["blank", "annotated", "rejected"]},
            "rejection_reason": {"type": "string"},
        },
        "required": [
            "schema_version", "candidate_id", "source", "repository", "task_id",
            "split", "trajectory_revision", "failure_episode", "annotator",
            "annotator_kind", "annotation_status", "rejection_reason",
        ],
    }


README = """# failure_episode_gold_v1 独立人工标注包

本包用于任务级失败 episode 的正式 A/B 独立标注。包内没有模型答案，也没有候选挖掘提示。

请复制 `reviews.template.jsonl` 为 `reviews.completed.jsonl` 后填写。A、B 两位标注者必须由不同的人独立完成，期间不得交换答案；不要参考旧版 GLM/ZCode 标注或另一份包。

每条 `source_event_id` 必须逐字来自该案例 `prefix.events`：

1. 先确定任务级初始失败动作与直接结果，不要用后续修复过程中产生的局部子失败替代它。
2. `repair_steps` 按时间填写。`action_source_event_id` 必须是 tool_call；每步结果必须晚于动作。只有最后一步可标为 `resolved`。
3. `error_signature` 必须逐字来自初始失败结果；动作名称和参数将在导入时从 tool_call 自动提取，不要手抄。
4. `required_core_source_event_ids` 是首选最小闭环；`optional_support_source_event_ids` 仅放有帮助但非必需的记录。
5. `chain_policy`、`audit_recovery` 和 `interactive_reacquisition` 均需列出合法最小证据集合、全部相关证据以及各集合内部的因果边。每条因果边必须遵守公开时间顺序。
6. `recovery_sequence` 要覆盖所有必要修复阶段，`resolution_evidence` 要说明最终成功是如何被观察到的。

能完整标注时设置 `annotation_status=annotated`，填写非空 `annotator`。若轨迹无法支持完整 episode，设置为 `rejected` 并填写 `rejection_reason`，不要猜测。

SWE-Gym 轨迹数据卡未声明数据许可；本包只用于受控研究，不得公开转发。
"""


def write_deterministic_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in source.iterdir() if item.is_file()):
            info = zipfile.ZipInfo(path.name, date_time=(2026, 9, 16, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def exposure_audit(dataset: Path, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    prefixes, _, _ = load_dataset(dataset, legacy=False)
    exposed = {
        (str(prefix.source_ref.get("source")), str(prefix.source_ref.get("task_id"))):
        prefix.prefix_id
        for prefix in prefixes
        if prefix.source_ref.get("label_protocol")
        == "third_ai_adjudication_development_only"
    }
    overlaps = []
    for row in candidates:
        key = (str(row["source"]), str(row["task_id"]))
        if key in exposed:
            overlaps.append({
                "prefix_id": exposed[key],
                "candidate_id": row["candidate_id"],
                "source": row["source"],
                "task_id": row["task_id"],
                "split": row["split"],
            })
    test_overlap = [row for row in overlaps if row["split"] == "test"]
    if test_overlap:
        raise ValueError("exposed development tasks overlap the formal test split")
    return {
        "exposure_dataset": str(dataset.resolve()),
        "development_exposed_prefix_count": len(exposed),
        "selected_population_overlap_count": len(overlaps),
        "overlaps": overlaps,
        "formal_test_overlap_count": 0,
    }


def build_packets(source_root: Path, exposure_dataset: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    candidates_path = source_root / "candidates.jsonl"
    selection_path = source_root / "selection_manifest.json"
    candidates = load_jsonl(candidates_path)
    if len(candidates) != 100 or len({row["candidate_id"] for row in candidates}) != 100:
        raise ValueError("failure episode packets require the frozen 100-case population")
    audit = exposure_audit(exposure_dataset, candidates)
    cases = [reviewer_case(row) for row in candidates]
    templates = [blank_annotation(row) for row in candidates]
    schema = annotation_schema()
    output.mkdir(parents=True)
    packet_files: dict[str, dict[str, str]] = {}
    for reviewer in ("reviewer_a", "reviewer_b"):
        root = output / reviewer
        root.mkdir()
        write_rows(root / "cases.jsonl", cases)
        write_rows(root / "reviews.template.jsonl", templates)
        (root / "annotation.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text(README, encoding="utf-8", newline="\n")
        archive = output / f"failure_episode_v1_{reviewer}_260916.zip"
        write_deterministic_zip(root, archive)
        packet_files[reviewer] = {
            "directory": str(root.resolve()),
            "zip": str(archive.resolve()),
            "zip_sha256": file_sha256(archive),
        }
    if packet_files["reviewer_a"]["zip_sha256"] != packet_files["reviewer_b"]["zip_sha256"]:
        raise AssertionError("A/B blind packet contents differ")
    manifest = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "annotation_schema_version": ANNOTATION_SCHEMA_VERSION,
        "source_root": str(source_root.resolve()),
        "source_candidates_sha256": file_sha256(candidates_path),
        "source_selection_manifest_sha256": file_sha256(selection_path),
        "case_count": len(candidates),
        "split_counts": dict(Counter(str(row["split"]) for row in candidates)),
        "model_answers_in_packet": False,
        "heuristic_candidate_hints_in_packet": False,
        "old_ai_annotations_in_packet": False,
        "requires_two_independent_human_annotators": True,
        "exposure_audit": audit,
        "packet_files": packet_files,
    }
    manifest["packet_identity"] = stable_digest(manifest)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = PlainArgumentParser(
        description="基于冻结的 100 条真实轨迹生成 failure_episode_gold_v1 双人独立盲标包。"
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--exposure-dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_packets(args.source_root, args.exposure_dataset, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
