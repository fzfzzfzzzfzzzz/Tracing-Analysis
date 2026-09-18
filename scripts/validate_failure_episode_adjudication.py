#!/usr/bin/env python3
"""Validate a completed development failure-episode adjudication file."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from tracegraph.benchmark.compression_audit.io import canonical_json, file_sha256, load_jsonl
from tracegraph.plain_cli import PlainArgumentParser

from validate_failure_episode_annotations import validate_annotated_episode


def validate_adjudication(cases_path: Path, decisions_path: Path) -> dict[str, Any]:
    cases = load_jsonl(cases_path)
    decisions = load_jsonl(decisions_path)
    case_map = {str(row.get("candidate_id")): row for row in cases}
    decision_map = {str(row.get("candidate_id")): row for row in decisions}
    errors: list[str] = []
    if len(case_map) != len(cases) or len(decision_map) != len(decisions):
        errors.append("duplicate candidate IDs")
    if set(case_map) != set(decision_map):
        errors.append("adjudication population differs from its packet")
    adjudicators: set[str] = set()
    source_counts: Counter[str] = Counter()
    for candidate_id in sorted(set(case_map) & set(decision_map)):
        case = case_map[candidate_id]
        row = decision_map[candidate_id]
        try:
            if row.get("schema_version") != "compression_audit_failure_episode_adjudication_v1":
                raise ValueError("unsupported adjudication schema")
            public = case["public_case"]
            for field in ("source", "repository", "task_id", "split", "trajectory_revision"):
                if row.get(field) != public.get(field):
                    raise ValueError(f"frozen {field} differs")
            if row.get("adjudication_status") != "completed":
                raise ValueError("adjudication_status must be completed")
            if row.get("adjudicator_kind") not in {"ai", "human"}:
                raise ValueError("adjudicator_kind must be ai or human")
            adjudicator = str(row.get("adjudicator") or "").strip()
            if not adjudicator:
                raise ValueError("adjudicator is required")
            adjudicators.add(adjudicator)
            rationale = str(row.get("rationale") or "").strip()
            if not rationale:
                raise ValueError("adjudication rationale is required")
            selected = str(row.get("selected_source") or "")
            if selected not in {"review_a", "review_b", "synthesized", "reject"}:
                raise ValueError("selected_source is invalid")
            source_counts[selected] += 1
            if selected == "reject":
                continue
            episode = row.get("failure_episode")
            validate_annotated_episode(episode, public)
            if selected in {"review_a", "review_b"}:
                expected = case[selected]["failure_episode"]
                if canonical_json(episode) != canonical_json(expected):
                    raise ValueError(f"{selected} selection must copy that episode exactly")
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{candidate_id}: {error}")
    kinds = {
        str(row.get("adjudicator_kind"))
        for row in decisions
        if row.get("adjudicator_kind")
    }
    valid = not errors
    return {
        "schema_version": "compression_audit_failure_episode_adjudication_validation_v1",
        "ready": valid,
        "formal_ready": valid and kinds == {"human"},
        "development_only": kinds != {"human"},
        "case_count": len(cases),
        "decision_count": len(decisions),
        "selected_source_counts": dict(source_counts),
        "adjudicators": sorted(adjudicators),
        "adjudicator_kinds": sorted(kinds),
        "decisions_sha256": file_sha256(decisions_path),
        "errors": errors,
    }


def main() -> int:
    parser = PlainArgumentParser(
        description="校验完成的 failure_episode 开发裁决表。"
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = validate_adjudication(args.cases, args.decisions)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        if args.report.exists():
            raise FileExistsError(args.report)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
