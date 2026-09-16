#!/usr/bin/env python3
"""Validate core-chain reviews and emit auditable external rubric overlays."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.build import verify_file_manifest
from tracegraph.benchmark.compression_audit.development_experiment import write_json, write_rows
from tracegraph.benchmark.compression_audit.development_scoring import make_rubric
from tracegraph.benchmark.compression_audit.io import file_sha256, load_jsonl, stable_digest
from tracegraph.benchmark.server_eval.rubrics import validate_rubric
from tracegraph.plain_cli import PlainArgumentParser


CORE_ROLES = (
    "failed_action",
    "failure_result",
    "replacement_action",
    "resolution_evidence",
)


def parse_args() -> argparse.Namespace:
    parser = PlainArgumentParser(description="校验失败链复核表并生成可追溯的评分覆盖文件。")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def require_id_list(row: dict, key: str, visible: set[str], *, minimum: int = 0) -> list[str]:
    values = row.get(key)
    if not isinstance(values, list) or len(values) < minimum:
        raise ValueError(f"{row.get('prefix_id')} has invalid {key}")
    if any(not isinstance(value, str) or value not in visible for value in values):
        raise ValueError(f"{row.get('prefix_id')} {key} references unknown events")
    if len(values) != len(set(values)):
        raise ValueError(f"{row.get('prefix_id')} {key} contains duplicates")
    return values


def acyclic(nodes: set[str], edges: list[list[str]]) -> bool:
    pending = set(nodes)
    while pending:
        roots = {node for node in pending if not any(
            right == node and left in pending for left, right in edges
        )}
        if not roots:
            return False
        pending -= roots
    return True


def validate_review(row: dict, prefix, query) -> dict:
    if row.get("schema_version") != "compression_audit_core_chain_review_v1":
        raise ValueError("unsupported core-chain review schema")
    if row.get("prefix_id") != prefix.prefix_id or row.get("query_id") != query.query_id:
        raise ValueError("review identity differs from packet")
    if row.get("review_status") != "reviewed":
        raise ValueError(f"review is not complete: {prefix.prefix_id}")
    if not isinstance(row.get("reviewer"), str) or not row["reviewer"].strip():
        raise ValueError("reviewer must be identified")
    if row.get("reviewer_kind") not in {"human", "ai"}:
        raise ValueError("reviewer_kind must be human or ai")
    if not isinstance(row.get("question_unambiguous_for_anchor"), bool):
        raise ValueError("question ambiguity decision must be boolean")
    if not row["question_unambiguous_for_anchor"] and not str(row.get("ambiguity_note") or "").strip():
        raise ValueError("ambiguous questions require a note")

    visible = {event["event_id"] for event in prefix.events}
    order = {event["event_id"]: index for index, event in enumerate(prefix.events)}
    core = require_id_list(row, "required_core_event_ids", visible, minimum=4)
    optional = require_id_list(row, "optional_support_event_ids", visible)
    relevant = require_id_list(row, "relevant_evidence_ids", visible, minimum=4)
    anchor = row.get("failure_anchor_event_id")
    if anchor not in core:
        raise ValueError("failure anchor must be part of the required core")
    if set(core) & set(optional):
        raise ValueError("required core and optional support must be disjoint")
    if not set(core + optional) <= set(relevant):
        raise ValueError("all core and optional events must be relevant")
    if [order[value] for value in core] != sorted(order[value] for value in core):
        raise ValueError("required core must follow public event order")

    alternatives = row.get("alternative_evidence_sets")
    if not isinstance(alternatives, list) or not alternatives:
        raise ValueError("at least one evidence alternative is required")
    for alternative in alternatives:
        if (not isinstance(alternative, list) or len(alternative) < 4
                or len(alternative) != len(set(alternative))
                or not set(alternative) <= set(relevant)
                or anchor not in alternative):
            raise ValueError("invalid alternative evidence set")
        if [order[value] for value in alternative] != sorted(order[value] for value in alternative):
            raise ValueError("evidence alternatives must follow public event order")
    if core not in alternatives:
        raise ValueError("required core must be one of the evidence alternatives")

    paths = row.get("causal_paths")
    if not isinstance(paths, list) or len(paths) != len(alternatives):
        raise ValueError("each evidence alternative requires one causal path")
    if ({frozenset(path.get("evidence_ids", ())) for path in paths}
            != {frozenset(values) for values in alternatives}):
        raise ValueError("causal paths and evidence alternatives differ")
    for path in paths:
        nodes = set(path["evidence_ids"])
        edges = path.get("constraints")
        if (not isinstance(edges, list)
                or any(not isinstance(edge, list) or len(edge) != 2
                       or edge[0] == edge[1] or not set(edge) <= nodes for edge in edges)
                or not acyclic(nodes, edges)):
            raise ValueError("invalid causal path")
    if row.get("causal_constraints") != paths[0]["constraints"]:
        raise ValueError("top-level causal constraints must match the first path")
    return row


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    verify_file_manifest(args.dataset)
    packet_manifest = json.loads((args.packet / "manifest.json").read_text(encoding="utf-8"))
    if file_sha256(args.dataset / "manifest.json") != packet_manifest["dataset_manifest_sha256"]:
        raise ValueError("review packet and dataset identities differ")
    cases = load_jsonl(args.packet / "reviewer_packet" / "cases.jsonl")
    reviews = load_jsonl(args.reviews)
    if len(reviews) != len({row.get("prefix_id") for row in reviews}):
        raise ValueError("duplicate review prefix")
    if {row["prefix_id"] for row in cases} != {row.get("prefix_id") for row in reviews}:
        raise ValueError("review coverage differs from the packet")

    prefixes, queries, gold_rows = load_dataset(args.dataset, legacy=False)
    prefix_map = {row.prefix_id: row for row in prefixes}
    query_map = {row.query_id: row for row in queries}
    gold_map = {row.prefix_id: row for row in gold_rows}
    review_map = {row["prefix_id"]: row for row in reviews}
    validated: list[dict] = []
    drift: list[dict] = []
    overlays: list[dict] = []
    compatible: list[dict] = []
    readjudication_cases: list[dict] = []
    readjudication_templates: list[dict] = []
    for case in cases:
        prefix_id = case["prefix_id"]
        query = query_map[case["query_id"]]
        prefix = prefix_map[prefix_id]
        gold = gold_map[prefix_id]
        review = validate_review(review_map[prefix_id], prefix, query)
        validated.append(review)

        old_anchor = gold.evidence_by_field["failed_action"][0]
        event_order = {event["event_id"]: index for index, event in enumerate(prefix.events)}
        old_core_set = {
            event_id
            for role in CORE_ROLES
            for event_id in gold.evidence_by_field.get(role, ())
        }
        old_core = sorted(old_core_set, key=event_order.__getitem__)
        anchor_changed = review["failure_anchor_event_id"] != old_anchor
        strict_gold_compatible = not anchor_changed
        drift.append({
            "prefix_id": prefix_id,
            "query_id": query.query_id,
            "old_failure_anchor_event_id": old_anchor,
            "reviewed_failure_anchor_event_id": review["failure_anchor_event_id"],
            "anchor_changed": anchor_changed,
            "old_required_core_event_ids": old_core,
            "reviewed_required_core_event_ids": review["required_core_event_ids"],
            "required_core_changed": old_core != review["required_core_event_ids"],
            "old_relevant_evidence_ids": list(gold.ordered_event_ids),
            "reviewed_relevant_evidence_ids": review["relevant_evidence_ids"],
            "strict_gold_compatible": strict_gold_compatible,
            "requires_semantic_gold_readjudication": not strict_gold_compatible,
        })
        if not strict_gold_compatible:
            readjudication_cases.append({
                "schema_version": "compression_audit_chain_scope_readjudication_case_v1",
                "prefix_id": prefix_id,
                "query_id": query.query_id,
                "public_case": case,
                "current_gold": gold.to_dict(),
                "completed_core_review": review,
                "model_answers_in_packet": False,
            })
            readjudication_templates.append({
                "schema_version": "compression_audit_chain_scope_readjudication_v1",
                "prefix_id": prefix_id,
                "query_id": query.query_id,
                "adjudicator": "",
                "adjudicator_kind": "",
                "adjudication_status": "pending",
                "selected_chain_scope": "",
                "failure_anchor_event_id": "",
                "required_core_event_ids": [],
                "optional_support_event_ids": [],
                "alternative_evidence_sets": [],
                "relevant_evidence_ids": [],
                "causal_paths": [],
                "question_requires_explicit_anchor": None,
                "affects_other_query_gold": None,
                "rationale": "",
            })

        rubric = make_rubric(query, gold)
        rubric.update({
            "schema_version": "compression_audit_rubric_core_review_v1",
            "alternative_evidence_sets": review["alternative_evidence_sets"],
            "relevant_evidence_ids": review["relevant_evidence_ids"],
            "causal_mode": "partial_order",
            "causal_constraints": review["causal_constraints"],
            "causal_paths": review["causal_paths"],
            "human_validated": review["reviewer_kind"] == "human",
            "rubric_source": "independent_core_chain_review",
            "annotation_receipt": {
                "reviewer": review["reviewer"],
                "reviewer_kind": review["reviewer_kind"],
                "reviews_sha256": file_sha256(args.reviews),
            },
        })
        rubric.pop("rubric_hash", None)
        rubric["rubric_hash"] = stable_digest(rubric)
        rubric = validate_rubric(rubric, query, prefix, gold)
        overlays.append(rubric)
        if strict_gold_compatible:
            compatible.append(rubric)

    args.output.mkdir(parents=True)
    write_rows(args.output / "reviews.validated.jsonl", validated)
    write_rows(args.output / "gold_drift.jsonl", drift)
    write_rows(args.output / "rubrics.all_reviewed.jsonl", overlays)
    write_rows(args.output / "rubrics.frozen_gold_compatible.jsonl", compatible)
    write_rows(args.output / "chain_scope_readjudication.cases.jsonl", readjudication_cases)
    write_rows(
        args.output / "chain_scope_readjudication.template.jsonl",
        readjudication_templates,
    )
    (args.output / "CHAIN_SCOPE_READJUDICATION.md").write_text(
        "# 两条失败链范围裁决\n\n"
        "这两条中，冻结金标选择了局部修复过程中的一次失败；新的核心链复核选择了"
        "任务级失败到最终成功的完整 episode。本包不含任何 Qwen 答案或分数。\n\n"
        "请逐条比较 cases 中的 current_gold、completed_core_review 与完整公开事件。"
        "selected_chain_scope 填 local_subfailure 或 task_level_failure_episode；其余证据字段"
        "按最终选择填写。若题目文字不足以唯一限定范围，question_requires_explicit_anchor=true。"
        "若选择会改变失败动作、错误、替代动作或其他查询的事实金标，"
        "affects_other_query_gold=true。adjudicator_kind 只能填 human 或 ai。\n",
        encoding="utf-8",
        newline="\n",
    )
    with zipfile.ZipFile(
        args.output / "chain_scope_readjudication_packet.zip",
        "x",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for name in (
            "CHAIN_SCOPE_READJUDICATION.md",
            "chain_scope_readjudication.cases.jsonl",
            "chain_scope_readjudication.template.jsonl",
        ):
            archive.write(args.output / name, arcname=name)
    review_kinds = sorted({row["reviewer_kind"] for row in validated})
    result = {
        "schema_version": "compression_audit_core_chain_review_import_v1",
        "development_only": review_kinds != ["human"],
        "human_validated": review_kinds == ["human"],
        "independent_validation": False,
        "review_count": len(validated),
        "reviewers": sorted({row["reviewer"] for row in validated}),
        "reviewer_kinds": review_kinds,
        "question_unambiguous_count": sum(
            row["question_unambiguous_for_anchor"] for row in validated
        ),
        "frozen_gold_compatible_count": len(compatible),
        "semantic_gold_readjudication_count": len(validated) - len(compatible),
        "dataset_manifest_sha256": file_sha256(args.dataset / "manifest.json"),
        "packet_manifest_sha256": file_sha256(args.packet / "manifest.json"),
        "reviews_sha256": file_sha256(args.reviews),
        "artifacts": {
            path.name: file_sha256(path)
            for path in sorted(args.output.iterdir()) if path.is_file()
        },
    }
    write_json(args.output / "import_report.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
