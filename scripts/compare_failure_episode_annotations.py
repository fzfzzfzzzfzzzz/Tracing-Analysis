#!/usr/bin/env python3
"""Compare A/B failure-episode reviews and build a development adjudication packet."""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from tracegraph.benchmark.compression_audit.io import (
    canonical_json,
    file_sha256,
    load_jsonl,
)
from tracegraph.benchmark.compression_audit.validation_helpers import (
    _cohen_kappa,
    _event_f1,
)
from tracegraph.plain_cli import PlainArgumentParser

from build_failure_episode_annotation_packets import blank_annotation, write_rows
from validate_failure_episode_annotations import validate_reviews


def episode_event_ids(episode: Mapping[str, Any]) -> list[str]:
    values = [
        episode.get("anchor_source_event_id"),
        episode.get("initial_action_source_event_id"),
        *episode.get("initial_result_source_event_ids", ()),
        *episode.get("resolution_source_event_ids", ()),
        *episode.get("required_core_source_event_ids", ()),
        *episode.get("optional_support_source_event_ids", ()),
    ]
    for step in episode.get("repair_steps", ()):
        values.extend(step.get("decision_source_event_ids", ()))
        values.append(step.get("action_source_event_id"))
        values.extend(step.get("result_source_event_ids", ()))
    policies = [episode.get("chain_policy", {})]
    policies.extend(episode.get("query_policies", {}).values())
    for policy in policies:
        values.extend(policy.get("relevant_evidence_ids", ()))
    return sorted({str(value) for value in values if value})


def difference_fields(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    return sorted(
        key for key in set(left) | set(right)
        if canonical_json(left.get(key)) != canonical_json(right.get(key))
    )


def compare(
    cases_path: Path, a_path: Path, b_path: Path, output: Path
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    validation_a = validate_reviews(cases_path, a_path, reviewer_kind="ai")
    validation_b = validate_reviews(cases_path, b_path, reviewer_kind="ai")
    if not validation_a["ready"] or not validation_b["ready"]:
        raise ValueError("A/B annotations must pass development-AI validation")
    cases = load_jsonl(cases_path)
    case_map = {row["candidate_id"]: row for row in cases}
    a = {row["candidate_id"]: row for row in load_jsonl(a_path)}
    b = {row["candidate_id"]: row for row in load_jsonl(b_path)}
    if set(case_map) != set(a) or set(a) != set(b):
        raise ValueError("A/B/case populations differ")
    ids = sorted(a)
    shared = [
        candidate_id for candidate_id in ids
        if a[candidate_id]["annotation_status"] == "annotated"
        and b[candidate_id]["annotation_status"] == "annotated"
    ]
    status_pairs = [
        (a[candidate_id]["annotation_status"], b[candidate_id]["annotation_status"])
        for candidate_id in ids
    ]
    exact_annotations = [
        candidate_id for candidate_id in shared
        if canonical_json(a[candidate_id]["failure_episode"])
        == canonical_json(b[candidate_id]["failure_episode"])
    ]
    consensus_rejections = [
        candidate_id for candidate_id in ids
        if a[candidate_id]["annotation_status"] == "rejected"
        and b[candidate_id]["annotation_status"] == "rejected"
    ]
    adjudication_ids = [
        candidate_id for candidate_id in ids
        if a[candidate_id]["annotation_status"] != b[candidate_id]["annotation_status"]
        or (
            candidate_id in shared
            and candidate_id not in exact_annotations
        )
    ]
    core_f1 = [
        _event_f1(
            a[candidate_id]["failure_episode"]["required_core_source_event_ids"],
            b[candidate_id]["failure_episode"]["required_core_source_event_ids"],
        )
        for candidate_id in shared
    ]
    referenced_f1 = [
        _event_f1(
            episode_event_ids(a[candidate_id]["failure_episode"]),
            episode_event_ids(b[candidate_id]["failure_episode"]),
        )
        for candidate_id in shared
    ]
    category_kappas = {
        field: _cohen_kappa(
            [str(a[candidate_id]["failure_episode"][field]) for candidate_id in shared],
            [str(b[candidate_id]["failure_episode"][field]) for candidate_id in shared],
        )
        for field in ("failure_family", "recoverability")
    }
    cases_out = []
    templates = []
    for candidate_id in adjudication_ids:
        left = a[candidate_id]
        right = b[candidate_id]
        fields = (
            ["annotation_status"]
            if left["annotation_status"] != right["annotation_status"]
            else []
        )
        if left["annotation_status"] == right["annotation_status"] == "annotated":
            fields.extend(difference_fields(
                left["failure_episode"], right["failure_episode"]
            ))
        cases_out.append({
            "schema_version": "compression_audit_failure_episode_adjudication_case_v1",
            "candidate_id": candidate_id,
            "public_case": case_map[candidate_id],
            "review_a": left,
            "review_b": right,
            "difference_fields": sorted(set(fields)),
            "evaluated_model_answers_in_packet": False,
            "development_only": True,
        })
        template = blank_annotation(case_map[candidate_id])
        template.update({
            "schema_version": "compression_audit_failure_episode_adjudication_v1",
            "adjudicator": "",
            "adjudicator_kind": "ai",
            "adjudication_status": "pending",
            "selected_source": "",
            "rationale": "",
        })
        template.pop("annotator")
        template.pop("annotator_kind")
        template.pop("annotation_status")
        templates.append(template)

    output.mkdir(parents=True)
    write_rows(output / "adjudication.cases.jsonl", cases_out)
    write_rows(output / "adjudication.template.jsonl", templates)
    write_rows(
        output / "consensus_rejections.jsonl",
        ({"candidate_id": value, "review_a": a[value], "review_b": b[value]}
         for value in consensus_rejections),
    )
    write_rows(
        output / "exact_consensus_annotations.jsonl",
        ({"candidate_id": value, "review": a[value]}
         for value in exact_annotations),
    )
    readme = (
        "# failure_episode 开发裁决包\n\n"
        "本包只含 A/B 不一致案例及公开轨迹，不含被测 Qwen 答案。"
        "逐条选择 review_a、review_b、synthesized 或 reject，并在 failure_episode 中"
        "写入最终完整结果。adjudicator_kind 保持 ai；本轮结果只能用于开发。\n"
    )
    (output / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    report = {
        "schema_version": "compression_audit_failure_episode_agreement_v1",
        "development_only": True,
        "formal_ready": False,
        "review_count": len(ids),
        "reviewer_a": validation_a["annotators"],
        "reviewer_b": validation_b["annotators"],
        "reviewer_a_sha256": file_sha256(a_path),
        "reviewer_b_sha256": file_sha256(b_path),
        "status_confusion": {
            f"{left}/{right}": count
            for (left, right), count in sorted(Counter(status_pairs).items())
        },
        "status_raw_agreement": sum(left == right for left, right in status_pairs) / len(ids),
        "status_kappa": _cohen_kappa(
            [left for left, _ in status_pairs], [right for _, right in status_pairs]
        ),
        "both_annotated_count": len(shared),
        "exact_episode_agreement_count": len(exact_annotations),
        "consensus_rejection_count": len(consensus_rejections),
        "adjudication_count": len(adjudication_ids),
        "category_kappas": category_kappas,
        "core_evidence_f1_mean": sum(core_f1) / len(core_f1) if core_f1 else None,
        "referenced_evidence_f1_mean": (
            sum(referenced_f1) / len(referenced_f1) if referenced_f1 else None
        ),
        "adjudication_split_counts": dict(Counter(
            str(a[value]["split"]) for value in adjudication_ids
        )),
        "known_nonindependence": (
            "Both passes used GLM-5.2; one worked exemplar was shared. Agreement is a "
            "development diagnostic, not independent human reliability."
        ),
    }
    (output / "agreement_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with zipfile.ZipFile(
        output / "failure_episode_ai_adjudication_packet.zip",
        "x",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for name in ("README.md", "adjudication.cases.jsonl", "adjudication.template.jsonl"):
            archive.write(output / name, arcname=name)
    return report


def main() -> int:
    parser = PlainArgumentParser(
        description="比较 failure_episode A/B 开发标注并生成第三方 AI 裁决包。"
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--review-a", type=Path, required=True)
    parser.add_argument("--review-b", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.cases, args.review_a, args.review_b, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
