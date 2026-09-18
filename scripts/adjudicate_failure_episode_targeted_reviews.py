"""Validate and deterministically adjudicate the targeted r10 A/B reviews."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from tracegraph.benchmark.compression_audit.io import file_sha256, load_jsonl


SCHEMA = "failure_episode_targeted_human_review_v1"
FIELDS = {
    "schema_version", "prefix_id", "review_status", "reviewer",
    "task_level_anchor_event_id", "failed_action_event_id", "failed_action",
    "failed_arguments", "error_result_event_ids", "error_signature",
    "audit_chain_required_core_event_ids",
    "audit_chain_optional_relevant_event_ids",
    "audit_chain_forbidden_event_ids", "anchor_level_rationale",
    "evidence_policy_rationale", "notes",
}


def _event_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _tool_identity(event: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    content = event.get("content")
    if event.get("kind") != "tool_call" or not isinstance(content, Mapping):
        raise ValueError("failed action must name a structured tool_call")
    action = event.get("tool_name") or content.get("name")
    arguments = content.get("arguments")
    if not isinstance(action, str) or not action or not isinstance(arguments, Mapping):
        raise ValueError("failed action tool_call lacks exact action or arguments")
    return action, dict(arguments)


def _validate_review(row: dict[str, Any], case: dict[str, Any]) -> None:
    if set(row) != FIELDS:
        raise ValueError(f"review fields differ: {row.get('prefix_id')}")
    if (
        row["schema_version"] != SCHEMA
        or row["review_status"] != "completed"
        or not isinstance(row["reviewer"], str)
        or not row["reviewer"].strip()
        or row["prefix_id"] != case["prefix_id"]
    ):
        raise ValueError(f"review identity/status invalid: {case['prefix_id']}")
    events = {str(event["event_id"]): event for event in case["events"]}
    order = {event_id: index for index, event_id in enumerate(events)}
    anchor = str(row["task_level_anchor_event_id"])
    action_id = str(row["failed_action_event_id"])
    if anchor != action_id or anchor not in events:
        raise ValueError(f"anchor/action mismatch: {case['prefix_id']}")
    action, arguments = _tool_identity(events[action_id])
    if row["failed_action"] != action or row["failed_arguments"] != arguments:
        raise ValueError(f"strict action differs from public record: {case['prefix_id']}")

    result_ids = row["error_result_event_ids"]
    signature = row["error_signature"]
    if (
        not isinstance(result_ids, list)
        or not result_ids
        or len(result_ids) != len(set(result_ids))
        or any(event_id not in events for event_id in result_ids)
        or any(order[event_id] <= order[action_id] for event_id in result_ids)
        or not isinstance(signature, str)
        or not signature.strip()
        or not any(signature in _event_text(events[event_id].get("content"))
                   for event_id in result_ids)
    ):
        raise ValueError(f"error signature/result invalid: {case['prefix_id']}")

    groups = [
        row["audit_chain_required_core_event_ids"],
        row["audit_chain_optional_relevant_event_ids"],
        row["audit_chain_forbidden_event_ids"],
    ]
    if any(
        not isinstance(group, list)
        or len(group) != len(set(group))
        or any(event_id not in events for event_id in group)
        for group in groups
    ):
        raise ValueError(f"evidence partition invalid: {case['prefix_id']}")
    core, optional, forbidden = map(set, groups)
    if (
        len(groups[0]) < 5
        or groups[0][0] != anchor
        or not set(result_ids) <= core
        or core & optional
        or core & forbidden
        or optional & forbidden
        or core | optional | forbidden != set(events)
        or [order[event_id] for event_id in groups[0]]
        != sorted(order[event_id] for event_id in groups[0])
    ):
        raise ValueError(f"evidence groups do not form a valid partition: {case['prefix_id']}")
    for key in ("anchor_level_rationale", "evidence_policy_rationale"):
        if not isinstance(row[key], str) or not row[key].strip():
            raise ValueError(f"missing rationale: {case['prefix_id']}/{key}")


def _f1(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return 2 * len(left & right) / (len(left) + len(right))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-a", type=Path, required=True)
    parser.add_argument("--reviews-a", type=Path, required=True)
    parser.add_argument("--cases-b", type=Path, required=True)
    parser.add_argument("--reviews-b", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output must be new")
    if file_sha256(args.cases_a) != file_sha256(args.cases_b):
        raise ValueError("A/B case packets differ")

    cases = {row["prefix_id"]: row for row in load_jsonl(args.cases_a)}
    a_rows = {row["prefix_id"]: row for row in load_jsonl(args.reviews_a)}
    b_rows = {row["prefix_id"]: row for row in load_jsonl(args.reviews_b)}
    if not cases or set(a_rows) != set(cases) or set(b_rows) != set(cases):
        raise ValueError("A/B reviews must cover every case exactly once")
    if len(a_rows) != len(load_jsonl(args.reviews_a)) or len(b_rows) != len(
        load_jsonl(args.reviews_b)
    ):
        raise ValueError("duplicate review prefix_id")

    adjudicated: list[dict[str, Any]] = []
    per_case: list[dict[str, Any]] = []
    exact_fields = (
        "task_level_anchor_event_id", "failed_action_event_id", "failed_action",
        "failed_arguments", "error_result_event_ids", "error_signature",
        "audit_chain_forbidden_event_ids",
    )
    for prefix_id in sorted(cases):
        case, left, right = cases[prefix_id], a_rows[prefix_id], b_rows[prefix_id]
        _validate_review(left, case)
        _validate_review(right, case)
        if left["reviewer"] == right["reviewer"]:
            raise ValueError("A/B reviewer identifiers must differ")
        disagreements = [name for name in exact_fields if left[name] != right[name]]
        if disagreements:
            raise ValueError(
                f"non-evidence-policy disagreement needs adjudication: {prefix_id}/{disagreements}"
            )
        core_a = set(left["audit_chain_required_core_event_ids"])
        core_b = set(right["audit_chain_required_core_event_ids"])
        relevant_a = core_a | set(left["audit_chain_optional_relevant_event_ids"])
        relevant_b = core_b | set(right["audit_chain_optional_relevant_event_ids"])
        if relevant_a != relevant_b or not (core_a <= core_b or core_b <= core_a):
            raise ValueError(
                f"evidence disagreement is not a minimal-core subset choice: {prefix_id}"
            )
        order = {
            str(event["event_id"]): index for index, event in enumerate(case["events"])
        }
        core_set = core_a & core_b
        relevant_set = relevant_a
        core = sorted(core_set, key=order.__getitem__)
        optional = sorted(relevant_set - core_set, key=order.__getitem__)
        constraints = [[left_id, right_id] for left_id, right_id in zip(core, core[1:])]
        adjudicated.append({
            "schema_version": "failure_episode_targeted_review_adjudication_v1",
            "adjudication_status": "completed",
            "prefix_id": prefix_id,
            "adjudicator": "deterministic-consensus-minimal-core",
            "adjudicator_kind": "rule",
            "reviewer_a": left["reviewer"],
            "reviewer_b": right["reviewer"],
            "reviewer_independence": "same_model_two_passes_not_independent",
            "task_level_anchor_event_id": left["task_level_anchor_event_id"],
            "failed_action_event_id": left["failed_action_event_id"],
            "failed_action": left["failed_action"],
            "failed_arguments": left["failed_arguments"],
            "error_result_event_ids": left["error_result_event_ids"],
            "error_signature": left["error_signature"],
            "audit_chain_required_core_event_ids": core,
            "audit_chain_optional_relevant_event_ids": optional,
            "audit_chain_forbidden_event_ids": left[
                "audit_chain_forbidden_event_ids"
            ],
            "alternative_evidence_sets": [core],
            "relevant_evidence_ids": sorted(relevant_set, key=order.__getitem__),
            "causal_paths": [{"evidence_ids": core, "constraints": constraints}],
            "resolution_rule": (
                "A/B agreed on the relevant/forbidden partition. When one pass made the "
                "final verification action core and the other called it optional, retain "
                "the smaller sufficient core and keep the action as valid optional evidence."
            ),
            "human_validated": False,
            "development_only": True,
        })
        per_case.append({
            "prefix_id": prefix_id,
            "anchor_exact": True,
            "strict_action_exact": True,
            "error_result_exact": True,
            "error_signature_exact": True,
            "relevant_partition_exact": relevant_a == relevant_b,
            "core_exact": core_a == core_b,
            "core_f1": _f1(core_a, core_b),
            "optional_f1": _f1(
                set(left["audit_chain_optional_relevant_event_ids"]),
                set(right["audit_chain_optional_relevant_event_ids"]),
            ),
            "core_only_a": sorted(core_a - core_b),
            "core_only_b": sorted(core_b - core_a),
        })

    args.output.mkdir(parents=True)
    adjudication_path = args.output / "adjudication.completed.jsonl"
    _write_jsonl(adjudication_path, adjudicated)
    report = {
        "schema_version": "failure_episode_targeted_review_agreement_v1",
        "case_count": len(per_case),
        "reviewer_a_ids": sorted({row["reviewer"] for row in a_rows.values()}),
        "reviewer_b_ids": sorted({row["reviewer"] for row in b_rows.values()}),
        "reviewer_independence": "same_model_two_passes_not_independent",
        "human_validated": False,
        "anchor_exact_count": sum(row["anchor_exact"] for row in per_case),
        "strict_action_exact_count": sum(row["strict_action_exact"] for row in per_case),
        "error_signature_exact_count": sum(row["error_signature_exact"] for row in per_case),
        "relevant_partition_exact_count": sum(
            row["relevant_partition_exact"] for row in per_case
        ),
        "core_exact_count": sum(row["core_exact"] for row in per_case),
        "mean_core_f1": sum(row["core_f1"] for row in per_case) / len(per_case),
        "mean_optional_f1": sum(row["optional_f1"] for row in per_case) / len(per_case),
        "cases": per_case,
        "cases_sha256": file_sha256(args.cases_a),
        "reviews_a_sha256": file_sha256(args.reviews_a),
        "reviews_b_sha256": file_sha256(args.reviews_b),
        "adjudication_sha256": file_sha256(adjudication_path),
        "new_provider_requests": 0,
        "development_only": True,
    }
    _write_json(args.output / "agreement_report.json", report)


if __name__ == "__main__":
    main()
