#!/usr/bin/env python3
"""Build a model-answer-blind review packet for failure-chain rubric structure."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from tracegraph.benchmark.compression_audit.build import verify_file_manifest
from tracegraph.benchmark.compression_audit.io import (
    canonical_json,
    file_sha256,
    load_jsonl,
    stable_digest,
)
from tracegraph.benchmark.compression_audit.models import (
    FailureChainGold,
    PrefixRecord,
    QueryRecord,
)
from tracegraph.benchmark.compression_audit.development_scoring import make_rubric
from tracegraph.plain_cli import PlainArgumentParser


CORE_ROLES = (
    "failed_action",
    "failure_result",
    "replacement_action",
    "resolution_evidence",
)


def write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(canonical_json(row) + "\n")


def ordered_subset(event_order: dict[str, int], values) -> list[str]:
    unique = set(values)
    if not unique <= set(event_order):
        raise ValueError("gold evidence references an event outside the public prefix")
    return sorted(unique, key=event_order.__getitem__)


def parse_args() -> argparse.Namespace:
    parser = PlainArgumentParser(description="生成不含模型答案的失败链评分结构复核包。")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    verify_file_manifest(args.dataset)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    selected = list(config["data"]["prefix_ids"])
    if len(selected) != len(set(selected)):
        raise ValueError("config contains duplicate prefix IDs")

    prefix_rows = load_jsonl(args.dataset / "public" / "real_prefixes.jsonl")
    query_rows = load_jsonl(args.dataset / "public" / "real_queries.jsonl")
    gold_rows = load_jsonl(args.dataset / "private" / "real_all_gold.jsonl")
    prefixes = {
        row["prefix_id"]: PrefixRecord.from_dict(row)
        for row in prefix_rows if row["prefix_id"] in selected
    }
    gold = {
        row["prefix_id"]: FailureChainGold.from_dict(row)
        for row in gold_rows if row["prefix_id"] in selected
    }
    audit_queries = {
        row["prefix_id"]: QueryRecord.from_dict(row)
        for row in query_rows
        if row["prefix_id"] in selected and row["query_type"] == "audit_chain"
    }
    if set(selected) != set(prefixes) or set(selected) != set(gold):
        raise ValueError("selected prefix/gold coverage differs")
    if set(selected) != set(audit_queries):
        raise ValueError("each selected prefix needs exactly one audit_chain query")

    cases: list[dict] = []
    templates: list[dict] = []
    internal_proposals: list[dict] = []
    proposed_rubrics: list[dict] = []
    for prefix_id in selected:
        prefix = prefixes[prefix_id]
        query = audit_queries[prefix_id]
        item = gold[prefix_id]
        event_order = {event["event_id"]: index for index, event in enumerate(prefix.events)}
        role_ids = [
            event_id
            for role in CORE_ROLES
            for event_id in item.evidence_by_field.get(role, ())
        ]
        core = ordered_subset(event_order, role_ids)
        if len(core) < 4:
            raise ValueError(f"derived core has fewer than four events: {prefix_id}")
        ordered = list(item.ordered_event_ids)
        optional = [event_id for event_id in ordered if event_id not in set(core)]
        constraints = [[left, right] for left, right in zip(core, core[1:])]

        # The reviewer sees public history and the public question, never model answers or scores.
        cases.append({
            "schema_version": "compression_audit_core_chain_case_v1",
            "case_id": stable_digest({"prefix_id": prefix_id, "query_id": query.query_id})[:16],
            "prefix_id": prefix_id,
            "query_id": query.query_id,
            "query_text": query.text,
            "task_domain": prefix.task_domain,
            "failure_family": prefix.failure_family,
            "recoverability": prefix.recoverability,
            "events": list(prefix.events),
        })
        templates.append({
            "schema_version": "compression_audit_core_chain_review_v1",
            "prefix_id": prefix_id,
            "query_id": query.query_id,
            "reviewer": "",
            "reviewer_kind": "",
            "review_status": "pending",
            "failure_anchor_event_id": "",
            "required_core_event_ids": [],
            "optional_support_event_ids": [],
            "alternative_evidence_sets": [],
            "relevant_evidence_ids": [],
            "causal_constraints": [],
            "causal_paths": [],
            "question_unambiguous_for_anchor": None,
            "ambiguity_note": "",
            "review_notes": "",
        })
        internal_proposals.append({
            "schema_version": "compression_audit_core_chain_proposal_v1",
            "prefix_id": prefix_id,
            "query_id": query.query_id,
            "gold_hash": item.gold_hash,
            "derivation": "ordered union of failed_action, failure_result, replacement_action, resolution_evidence roles",
            "failure_anchor_event_id": item.evidence_by_field["failed_action"][0],
            "required_core_event_ids": core,
            "optional_support_event_ids": optional,
            "alternative_evidence_sets": [core],
            "relevant_evidence_ids": ordered,
            "causal_constraints": constraints,
            "causal_paths": [{"evidence_ids": core, "constraints": constraints}],
            "proposal_only": True,
            "human_validated": False,
        })

        rubric = make_rubric(query, item)
        rubric.update({
            "schema_version": "compression_audit_rubric_core_sensitivity_v1",
            "alternative_evidence_sets": [core],
            "relevant_evidence_ids": ordered,
            "causal_mode": "partial_order",
            "causal_constraints": constraints,
            "causal_paths": [{"evidence_ids": core, "constraints": constraints}],
            "human_validated": False,
            "rubric_source": "post_hoc_core_role_projection",
        })
        rubric.pop("rubric_hash", None)
        rubric["rubric_hash"] = stable_digest(rubric)
        proposed_rubrics.append(rubric)

    reviewer = args.output / "reviewer_packet"
    reviewer.mkdir(parents=True)
    write_rows(reviewer / "cases.jsonl", cases)
    write_rows(reviewer / "reviews.template.jsonl", templates)
    readme = (
        "# 失败链评分结构独立复核包\n\n"
        "本包只复核 7 条已暴露开发案例的证据结构，不是冻结测试集，也不含模型答案、"
        "模型分数或自动生成的建议答案。请从干净副本分别交给两位复核者。\n\n"
        "将 reviews.template.jsonl 复制为 reviews.completed.jsonl，逐条填写 reviewer、"
        "reviewer_kind（human 或 ai）。"
        "review_status 只能是 reviewed 或 rejected。required_core_event_ids 应是回答失败链"
        "不可缺少的最小闭环，通常依次覆盖：失败动作、直接失败结果、替代动作、成功/解决证据，"
        "因此允许合法的 4 事件链。optional_support_event_ids 是有帮助但不应成为硬门槛的诊断、"
        "决策或前置上下文。alternative_evidence_sets 用于多个同样充分的证据组合；"
        "relevant_evidence_ids 是允许附带引用而不扣分的全部相关事件。causal_constraints 使用"
        " [前事件ID, 后事件ID]；若有多个 alternative_evidence_sets，还需在 causal_paths 中为"
        "每个集合分别填写 evidence_ids 与 constraints。\n\n"
        "failure_anchor_event_id 必须唯一指向题目所问的失败尝试；若当前问题无法唯一定位，"
        "question_unambiguous_for_anchor=false 并说明歧义。不得参考任何模型作答表现来改标。\n"
    )
    (reviewer / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    write_rows(args.output / "internal_proposals.jsonl", internal_proposals)
    write_rows(args.output / "rubrics.proposed.jsonl", proposed_rubrics)

    packet_zip = args.output / "core_chain_reviewer_packet.zip"
    with zipfile.ZipFile(packet_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(reviewer.iterdir()):
            archive.write(path, arcname=path.name)
    manifest = {
        "schema_version": "compression_audit_core_chain_packet_manifest_v1",
        "development_only": True,
        "post_hoc_sensitivity": True,
        "independent_validation": False,
        "model_answers_in_reviewer_packet": False,
        "case_count": len(cases),
        "prefix_ids": selected,
        "dataset_manifest_sha256": file_sha256(args.dataset / "manifest.json"),
        "dataset_file_manifest_sha256": file_sha256(args.dataset / "file_manifest.jsonl"),
        "config_sha256": file_sha256(args.config),
        "artifacts": {
            str(path.relative_to(args.output)).replace("\\", "/"): file_sha256(path)
            for path in sorted(args.output.rglob("*")) if path.is_file()
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
