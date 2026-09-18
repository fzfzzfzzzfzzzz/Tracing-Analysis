"""Build a blinded AI-development review packet for error-signature granularity."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.development_experiment import write_json, write_rows
from tracegraph.benchmark.compression_audit.io import file_sha256, stable_digest


TARGETS = (
    "real:swe_gym:4b66e74f8d1830e3",
    "real:swe_gym:6f4e03fe44805b6e",
)


def _labelled_signature(answer: dict) -> str:
    text = str(answer["a"])
    matches = re.findall(r'(?m)^error_signature:\s*"(.*)"$', text)
    if len(matches) != 1:
        raise ValueError("saved answer lacks one quoted error_signature label")
    return matches[0]


def build(dataset: Path, run: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("use a new signature-review output directory")
    prefixes, _, gold_rows = load_dataset(dataset)
    prefix_by_id = {row.prefix_id: row for row in prefixes}
    gold_by_id = {row.prefix_id: row for row in gold_rows}
    episodes = [json.loads(line) for line in (run / "episodes.jsonl").read_text(
        encoding="utf-8").splitlines()]
    episode_by_id = {row["prefix_id"]: row for row in episodes
                     if row.get("phase") == "diagnostic"}

    cases = []
    reviews = []
    private_mapping = []
    for index, prefix_id in enumerate(TARGETS, 1):
        prefix = prefix_by_id[prefix_id]
        gold = gold_by_id[prefix_id]
        episode = episode_by_id[prefix_id]
        source_ids = list(gold.evidence_by_field["error_signature"])
        events = {event["event_id"]: event for event in prefix.events}
        source_records = [events[event_id] for event_id in source_ids]
        gold_value = str(gold.error_signature)
        answer_value = _labelled_signature(episode["answer"])
        candidates = (
            {"A": gold_value, "B": answer_value}
            if index % 2 else {"A": answer_value, "B": gold_value}
        )
        blind_id = f"signature-boundary-{index:03d}"
        cases.append({
            "schema_version": "failure_episode_signature_boundary_case_v1",
            "blind_id": blind_id,
            "task": (
                "Using only the failure record, decide what text is a stable error signature "
                "and whether each blinded candidate unambiguously identifies the same failure."
            ),
            "failure_records": source_records,
            "candidate_a": candidates["A"],
            "candidate_b": candidates["B"],
            "decision_definitions": {
                "identity_equivalent": (
                    "The candidate unambiguously identifies the observed failure even if it "
                    "omits volatile wrapper or environment-specific text."
                ),
                "canonical_stable_signature": (
                    "A literal substring of the failure record that retains the exception type "
                    "when needed for disambiguation and excludes volatile paths/addresses."
                ),
            },
        })
        reviews.append({
            "schema_version": "failure_episode_signature_boundary_review_v1",
            "blind_id": blind_id,
            "status": "pending",
            "candidate_a_identity_equivalent": None,
            "candidate_b_identity_equivalent": None,
            "canonical_stable_signature": "",
            "volatile_segments": [],
            "exception_class_required": None,
            "recommended_policy": "",
            "rationale": "",
            "reviewer_id": "",
            "reviewer_kind": "ai",
            "human_reviewed": False,
        })
        private_mapping.append({
            "blind_id": blind_id,
            "prefix_id": prefix_id,
            "candidate_a_source": "gold" if candidates["A"] == gold_value else "model_answer",
            "candidate_b_source": "gold" if candidates["B"] == gold_value else "model_answer",
            "gold_hash": gold.gold_hash,
            "episode_id": episode["episode_id"],
        })

    output.mkdir(parents=True)
    write_rows(output / "cases.jsonl", cases)
    write_rows(output / "reviews.template.jsonl", reviews)
    write_rows(output / "blind_mapping.private.jsonl", private_mapping)
    readme = """# GLM 开发期错误签名边界复核

本包只复核 2 个 `error_signature` 粒度分歧。请只把 `cases.jsonl` 和
`reviews.template.jsonl` 交给 GLM，不要提供 `blind_mapping.private.jsonl`、模型总成绩或
当前金标来源。

逐行填写模板并另存为 `reviews.completed.jsonl`：

- `status` 改为 `reviewed`；
- 判断 A/B 是否在失败记录内无歧义地标识同一失败；
- `canonical_stable_signature` 必须逐字来自失败记录；
- `volatile_segments` 只列环境路径、地址等不稳定片段；
- `recommended_policy` 只能填写 `keep_exact_current`、
  `normalize_gold_then_one_way` 或 `semantic_signature_match`；
- 保持 `reviewer_kind=ai`、`human_reviewed=false`。

这是同一研究流程内的 GLM 开发审计，不是独立人工复核，不能用于正式 v1 声明。
"""
    (output / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": "failure_episode_signature_boundary_packet_v1",
        "development_only": True,
        "independent_validation": False,
        "human_validated": False,
        "case_count": len(cases),
        "dataset_manifest_sha256": file_sha256(dataset / "manifest.json"),
        "run_report_sha256": file_sha256(run / "report.json"),
        "case_digest": stable_digest(cases),
        "files": {},
    }
    for name in ("README.md", "cases.jsonl", "reviews.template.jsonl",
                 "blind_mapping.private.jsonl"):
        manifest["files"][name] = file_sha256(output / name)
    write_json(output / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.dataset, args.run, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
