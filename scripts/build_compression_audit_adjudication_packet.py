#!/usr/bin/env python3
"""Build a reviewable A/B disagreement packet for exposed development cases."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from tracegraph.benchmark.compression_audit.io import (
    canonical_json,
    file_sha256,
    load_jsonl,
)


def write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(canonical_json(row) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--reviewer-a", type=Path, required=True)
    parser.add_argument("--reviewer-b", type=Path, required=True)
    parser.add_argument("--accepted-receipts", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)

    candidates = {row["candidate_id"]: row for row in load_jsonl(args.candidates)}
    reviewer_a = {row["candidate_id"]: row for row in load_jsonl(args.reviewer_a)}
    reviewer_b = {row["candidate_id"]: row for row in load_jsonl(args.reviewer_b)}
    receipts = load_jsonl(args.accepted_receipts)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    prefix_ids = list(config["data"]["prefix_ids"])
    candidate_by_prefix = {row["prefix_id"]: row["candidate_id"] for row in receipts}
    if any(prefix_id not in candidate_by_prefix for prefix_id in prefix_ids):
        raise ValueError("configured prefix is absent from accepted receipts")
    candidate_ids = [candidate_by_prefix[prefix_id] for prefix_id in prefix_ids]
    if any(value not in candidates or value not in reviewer_a or value not in reviewer_b
           for value in candidate_ids):
        raise ValueError("A/B/candidate coverage differs for selected cases")

    cases = []
    templates = []
    counters: Counter[str] = Counter()
    for prefix_id, candidate_id in zip(prefix_ids, candidate_ids, strict=True):
        a = reviewer_a[candidate_id]
        b = reviewer_b[candidate_id]
        a_chain = a.get("failure_chain") or {}
        b_chain = b.get("failure_chain") or {}
        differences = {
            "annotation_status": a.get("annotation_status") != b.get("annotation_status"),
            "recoverability": a_chain.get("recoverability") != b_chain.get("recoverability"),
            "ordered_source_event_ids": (
                a_chain.get("ordered_source_event_ids")
                != b_chain.get("ordered_source_event_ids")
            ),
            "error_signature": a_chain.get("error_signature") != b_chain.get("error_signature"),
            "failure_family": a_chain.get("failure_family") != b_chain.get("failure_family"),
        }
        counters.update(key for key, differs in differences.items() if differs)
        cases.append({
            "prefix_id": prefix_id,
            "candidate_id": candidate_id,
            "candidate": candidates[candidate_id],
            "reviewer_a": a,
            "reviewer_b": b,
            "differences": differences,
            "development_only": True,
        })
        templates.append({
            "prefix_id": prefix_id,
            "candidate_id": candidate_id,
            "adjudicator": "",
            "adjudication_status": "blank",
            "selected_failed_action_source_event_id": "",
            "selected_replacement_action_source_event_id": "",
            "failure_family": "",
            "error_signature": "",
            "diagnostic_evidence": "",
            "switch_decision": "",
            "resolution_evidence": "",
            "ordered_source_event_ids": [],
            "evidence_source_event_ids_by_field": {},
            "recoverability": "",
            "reject_reason": "",
        })

    args.output.mkdir(parents=True)
    write_rows(args.output / "cases.jsonl", cases)
    write_rows(args.output / "adjudication.template.jsonl", templates)
    readme = (
        "# compression_audit_v1 开发集 A/B 分歧裁决包\n\n"
        "本包只包含已暴露的 7 个开发前缀，不是冻结测试集，也不能产生正式 v1 结论。"
        "逐条阅读 candidate.prefix.events，再比较 A/B；不得按多数或按模型成绩选答案。\n\n"
        "请将 adjudication.template.jsonl 复制为 adjudication.completed.jsonl。"
        "若轨迹不能唯一支持完整的失败动作→失败结果→诊断→切换→替代动作→成功结果链，"
        "设 adjudication_status=rejected 并填写 reject_reason；否则设为 adjudicated。\n\n"
        "failed/replacement 的动作名和 arguments 不再手写：只填写唯一的 tool_call "
        "source_event_id，导入器将从该事件自动复制 tool_name 和 arguments。"
        "error_signature 必须是失败结果中的原文；ordered_source_event_ids 必须按因果顺序、"
        "无重复，并包含每个独立必要事件。recoverability 必须根据公开历史能否恢复证据判定。\n"
    )
    (args.output / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": "compression_audit_development_adjudication_packet_v1",
        "development_only": True,
        "independent_validation": False,
        "case_count": len(cases),
        "prefix_ids": prefix_ids,
        "difference_counts": dict(counters),
        "inputs": {
            "candidates_sha256": file_sha256(args.candidates),
            "reviewer_a_sha256": file_sha256(args.reviewer_a),
            "reviewer_b_sha256": file_sha256(args.reviewer_b),
            "accepted_receipts_sha256": file_sha256(args.accepted_receipts),
            "config_sha256": file_sha256(args.config),
        },
        "artifacts": {
            name: file_sha256(args.output / name)
            for name in ("README.md", "cases.jsonl", "adjudication.template.jsonl")
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
