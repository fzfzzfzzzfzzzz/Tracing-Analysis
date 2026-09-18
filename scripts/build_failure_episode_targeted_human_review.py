"""Build blind A/B packets for the three r10 capability-attribution cases."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.io import file_sha256


TARGETS = (
    "real:swe_gym:32a4b0e8f3c5b3c0",
    "real:swe_gym:fc45587d5bac8c04",
    "real:swe_gym:a81eeafd60eb5788",
)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _zip(directory: Path, output: Path) -> str:
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory.parent).as_posix())
    return file_sha256(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output must be new")

    prefixes, queries, _ = load_dataset(args.dataset, legacy=False)
    prefix_by_id = {row.prefix_id: row for row in prefixes}
    query_by_prefix: dict[str, list[dict[str, Any]]] = {key: [] for key in TARGETS}
    for query in queries:
        if query.prefix_id in query_by_prefix:
            query_by_prefix[query.prefix_id].append(query.to_dict())
    if set(TARGETS) - set(prefix_by_id) or any(
        not query_by_prefix[prefix_id] for prefix_id in TARGETS
    ):
        raise ValueError("dataset lacks a targeted prefix or its public queries")

    cases = [
        {
            "schema_version": "failure_episode_targeted_human_case_v1",
            "prefix_id": prefix_id,
            "source_ref": dict(prefix_by_id[prefix_id].source_ref),
            "recoverability": prefix_by_id[prefix_id].recoverability,
            "events": [dict(event) for event in prefix_by_id[prefix_id].events],
            "queries": sorted(
                query_by_prefix[prefix_id], key=lambda row: str(row["query_type"])
            ),
        }
        for prefix_id in TARGETS
    ]
    templates = [
        {
            "schema_version": "failure_episode_targeted_human_review_v1",
            "prefix_id": prefix_id,
            "review_status": "pending",
            "reviewer": "",
            "task_level_anchor_event_id": "",
            "failed_action_event_id": "",
            "failed_action": "",
            "failed_arguments": {},
            "error_result_event_ids": [],
            "error_signature": "",
            "audit_chain_required_core_event_ids": [],
            "audit_chain_optional_relevant_event_ids": [],
            "audit_chain_forbidden_event_ids": [],
            "anchor_level_rationale": "",
            "evidence_policy_rationale": "",
            "notes": "",
        }
        for prefix_id in TARGETS
    ]
    instructions = """# r10 三案例独立人工复核

请只依据 `cases.jsonl` 的公开事件和问题作答；不要查看模型答案、AI 标注或另一位审核者的结果。

每条都要确认：

1. 任务级失败从哪个公开事件开始；`failed_action` 应严格采用工具调用记录的工具名和参数，还是该问题必须允许内部概念动作。请在 rationale 解释。
2. 哪个直接结果承载稳定错误签名；错误签名应是能唯一识别该失败、但不依赖外层包装的最小文本。
3. 完整 audit-chain 的必需核心事件、允许作为有效支持的可选事件，以及虽相邻但不应视为证据的事件。
4. 特别注意：工具编辑后的 observation、最终验证后的 assistant 总结是否可作为有效额外引用，必须逐条判断，不能按事件类型一刀切。

将 `reviews.template.jsonl` 复制为 `reviews.completed.jsonl` 后填写。每条
`review_status` 改为 `completed`；事件 ID 必须逐字来自对应 case；不要删减或新增 prefix。
"""

    args.output.mkdir(parents=True)
    packet_hashes: dict[str, str] = {}
    for reviewer in ("reviewer_a", "reviewer_b"):
        directory = args.output / reviewer
        directory.mkdir()
        _write_jsonl(directory / "cases.jsonl", cases)
        _write_jsonl(directory / "reviews.template.jsonl", templates)
        (directory / "README.md").write_text(instructions, encoding="utf-8")
        _write_json(
            directory / "manifest.json",
            {
                "schema_version": "failure_episode_targeted_human_packet_v1",
                "reviewer_slot": reviewer,
                "case_count": len(cases),
                "prefix_ids": list(TARGETS),
                "contains_model_answers": False,
                "contains_ai_gold": False,
                "development_only": True,
                "dataset_manifest_sha256": file_sha256(args.dataset / "manifest.json"),
                "cases_sha256": file_sha256(directory / "cases.jsonl"),
                "template_sha256": file_sha256(directory / "reviews.template.jsonl"),
            },
        )
        packet_hashes[reviewer] = _zip(
            directory, args.output / f"failure_episode_r10_{reviewer}_260917.zip"
        )
    _write_json(
        args.output / "packet_receipt.json",
        {
            "schema_version": "failure_episode_targeted_human_packet_receipt_v1",
            "packet_sha256": packet_hashes,
            "same_cases": (
                file_sha256(args.output / "reviewer_a" / "cases.jsonl")
                == file_sha256(args.output / "reviewer_b" / "cases.jsonl")
            ),
            "same_blank_template": (
                file_sha256(args.output / "reviewer_a" / "reviews.template.jsonl")
                == file_sha256(args.output / "reviewer_b" / "reviews.template.jsonl")
            ),
            "new_provider_requests": 0,
            "development_only": True,
        },
    )


if __name__ == "__main__":
    main()
