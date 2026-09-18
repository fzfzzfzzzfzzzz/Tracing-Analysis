#!/usr/bin/env python3
"""Merge AI A/B agreement and adjudication into one development review file."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

from tracegraph.benchmark.compression_audit.io import file_sha256, load_jsonl
from tracegraph.plain_cli import PlainArgumentParser


ANNOTATOR = "ai-adjudicated-development-260917-r1"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def build(
    cases_path: Path,
    review_a_path: Path,
    review_b_path: Path,
    decisions_path: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    cases = load_jsonl(cases_path)
    review_a = {str(row["candidate_id"]): row for row in load_jsonl(review_a_path)}
    review_b = {str(row["candidate_id"]): row for row in load_jsonl(review_b_path)}
    decisions = {
        str(row["candidate_id"]): row for row in load_jsonl(decisions_path)
    }
    ids = {str(row["candidate_id"]) for row in cases}
    if set(review_a) != ids or set(review_b) != ids:
        raise ValueError("review populations differ from the frozen cases")

    rows: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for case in cases:
        candidate_id = str(case["candidate_id"])
        left, right = review_a[candidate_id], review_b[candidate_id]
        row = copy.deepcopy(left)
        row["annotator"] = ANNOTATOR
        row["annotator_kind"] = "ai"
        if left["annotation_status"] == right["annotation_status"] == "rejected":
            row["annotation_status"] = "rejected"
            row["rejection_reason"] = (
                "A/B consensus rejection. A: "
                + str(left["rejection_reason"])
                + " B: "
                + str(right["rejection_reason"])
            )
            source = "consensus_reject"
        elif (
            left["annotation_status"] == right["annotation_status"] == "annotated"
            and left["failure_episode"] == right["failure_episode"]
        ):
            row["annotation_status"] = "annotated"
            row["rejection_reason"] = ""
            source = "exact_consensus"
        else:
            decision = decisions.get(candidate_id)
            if decision is None:
                raise ValueError(f"missing adjudication for {candidate_id}")
            if decision["selected_source"] == "reject":
                row["annotation_status"] = "rejected"
                row["rejection_reason"] = str(decision["rationale"])
            else:
                row["annotation_status"] = "annotated"
                row["failure_episode"] = copy.deepcopy(decision["failure_episode"])
                row["rejection_reason"] = ""
            source = f"adjudicated:{decision['selected_source']}"
        rows.append(row)
        provenance.append(
            {
                "candidate_id": candidate_id,
                "final_status": row["annotation_status"],
                "decision_source": source,
                "split": row["split"],
                "source": row["source"],
            }
        )

    output.mkdir(parents=True)
    reviews_path = output / "reviews.adjudicated.development_ai.jsonl"
    provenance_path = output / "decision_provenance.jsonl"
    _write_rows(reviews_path, rows)
    _write_rows(provenance_path, provenance)

    accepted = [row for row in rows if row["annotation_status"] == "annotated"]
    report = {
        "schema_version": "compression_audit_failure_episode_development_reviews_v1",
        "development_only": True,
        "formal_ready": False,
        "independent_human_review": False,
        "candidate_count": len(rows),
        "status_counts": dict(Counter(row["annotation_status"] for row in rows)),
        "accepted_split_counts": dict(Counter(row["split"] for row in accepted)),
        "accepted_source_counts": dict(Counter(row["source"] for row in accepted)),
        "accepted_failure_family_counts": dict(
            Counter(row["failure_episode"]["failure_family"] for row in accepted)
        ),
        "accepted_recoverability_counts": dict(
            Counter(row["failure_episode"]["recoverability"] for row in accepted)
        ),
        "decision_source_counts": dict(
            Counter(row["decision_source"] for row in provenance)
        ),
        "cases_sha256": file_sha256(cases_path),
        "review_a_sha256": file_sha256(review_a_path),
        "review_b_sha256": file_sha256(review_b_path),
        "adjudication_sha256": file_sha256(decisions_path),
        "reviews_sha256": file_sha256(reviews_path),
        "provenance_sha256": file_sha256(provenance_path),
        "interpretation": (
            "Fast development gold from two non-independent GLM-5.2 passes and one AI "
            "adjudication. It may be used for exploratory comparisons only. Any surviving "
            "paper claim requires independent human review on a frozen confirmatory set."
        ),
    }
    _write_json(output / "population_report.json", report)
    return report


def main() -> int:
    parser = PlainArgumentParser(
        description="合并 failure_episode A/B 与 AI 裁决为开发标注。"
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--review-a", type=Path, required=True)
    parser.add_argument("--review-b", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(
        args.cases, args.review_a, args.review_b, args.adjudication, args.output
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
