#!/usr/bin/env python3
"""Validate one completed human ``failure_episode_gold_v1`` review packet."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from tracegraph.benchmark.compression_audit.io import file_sha256, load_jsonl
from tracegraph.plain_cli import PlainArgumentParser

from build_failure_episode_annotation_packets import ANNOTATION_SCHEMA_VERSION


POLICY_KEYS = {
    "alternative_evidence_sets", "relevant_evidence_ids", "causal_paths"
}
EPISODE_KEYS = {
    "scope", "failure_family", "recoverability", "error_signature",
    "diagnostic_evidence", "recovery_sequence", "resolution_evidence",
    "anchor_source_event_id", "initial_action_source_event_id",
    "initial_result_source_event_ids", "repair_steps", "resolution_source_event_ids",
    "required_core_source_event_ids", "optional_support_source_event_ids",
    "chain_policy", "query_policies",
}


def event_id(event: Mapping[str, Any]) -> str:
    return str(event.get("source_event_id") or event.get("event_id") or "")


def validate_ids(values: Any, visible: set[str], name: str, *, nonempty: bool = True
                 ) -> list[str]:
    if not isinstance(values, list) or (nonempty and not values):
        raise ValueError(f"{name} must be a{' nonempty' if nonempty else ''} list")
    result = list(map(str, values))
    if any(not value for value in result) or len(result) != len(set(result)):
        raise ValueError(f"{name} contains blank or duplicate event IDs")
    if not set(result) <= visible:
        raise ValueError(f"{name} references an unknown event")
    return result


def validate_policy(value: Any, visible: set[str], order: Mapping[str, int], name: str
                    ) -> None:
    if not isinstance(value, Mapping) or set(value) != POLICY_KEYS:
        raise ValueError(f"{name} has invalid fields")
    relevant = set(validate_ids(value["relevant_evidence_ids"], visible, f"{name}.relevant"))
    raw_alternatives = value["alternative_evidence_sets"]
    if not isinstance(raw_alternatives, list) or not raw_alternatives:
        raise ValueError(f"{name} needs an evidence alternative")
    alternatives = [
        validate_ids(values, relevant, f"{name}.alternative")
        for values in raw_alternatives
    ]
    if len({frozenset(values) for values in alternatives}) != len(alternatives):
        raise ValueError(f"{name} has duplicate evidence alternatives")
    raw_paths = value["causal_paths"]
    if not isinstance(raw_paths, list) or len(raw_paths) != len(alternatives):
        raise ValueError(f"{name} needs one causal path per alternative")
    path_sets = []
    for path in raw_paths:
        if not isinstance(path, Mapping) or set(path) != {"evidence_ids", "constraints"}:
            raise ValueError(f"{name} has an invalid causal path")
        nodes = set(validate_ids(path["evidence_ids"], relevant, f"{name}.path"))
        path_sets.append(frozenset(nodes))
        constraints = path["constraints"]
        if not isinstance(constraints, list):
            raise ValueError(f"{name} causal constraints must be a list")
        edges: list[tuple[str, str]] = []
        for edge in constraints:
            if not isinstance(edge, list) or len(edge) != 2:
                raise ValueError(f"{name} causal edge must contain two event IDs")
            left, right = map(str, edge)
            if left == right or left not in nodes or right not in nodes:
                raise ValueError(f"{name} causal edge is outside its path")
            if order[left] >= order[right]:
                raise ValueError(f"{name} causal edge contradicts public chronology")
            edges.append((left, right))
        if len(edges) != len(set(edges)):
            raise ValueError(f"{name} has duplicate causal edges")
    if set(path_sets) != {frozenset(values) for values in alternatives}:
        raise ValueError(f"{name} paths do not match evidence alternatives")


def validate_annotated_episode(episode: Any, case: Mapping[str, Any]) -> None:
    if not isinstance(episode, Mapping) or set(episode) != EPISODE_KEYS:
        raise ValueError("failure_episode fields differ from the v1 template")
    if episode["scope"] != "task_level_failure_episode":
        raise ValueError("failure_episode scope must be task_level_failure_episode")
    for field in (
        "failure_family", "error_signature", "diagnostic_evidence",
        "recovery_sequence", "resolution_evidence",
    ):
        if not isinstance(episode[field], str) or not episode[field].strip():
            raise ValueError(f"failure_episode.{field} is required")
    if episode["recoverability"] not in {"R0", "R1", "R2", "R3"}:
        raise ValueError("failure_episode.recoverability is invalid")
    prefix = case.get("prefix")
    events = prefix.get("events") if isinstance(prefix, Mapping) else None
    if not isinstance(events, list) or not events:
        raise ValueError("case has no public events")
    by_id = {event_id(event): event for event in events if isinstance(event, Mapping)}
    if "" in by_id or len(by_id) != len(events):
        raise ValueError("case public event IDs are invalid")
    visible = set(by_id)
    order = {event_id(event): index for index, event in enumerate(events)}
    anchor = str(episode["anchor_source_event_id"])
    initial = str(episode["initial_action_source_event_id"])
    if not anchor or anchor != initial or initial not in visible:
        raise ValueError("episode anchor must equal its known initial action")
    if by_id[initial].get("kind") != "tool_call":
        raise ValueError("episode initial action must be a tool_call")
    initial_results = validate_ids(
        episode["initial_result_source_event_ids"], visible, "initial results"
    )
    if any(order[value] <= order[initial] for value in initial_results):
        raise ValueError("initial results must follow the initial action")
    error_text = episode["error_signature"]
    serialized_results = "\n".join(
        json.dumps(by_id[value].get("content"), ensure_ascii=False, default=str)
        for value in initial_results
    )
    if error_text not in serialized_results:
        raise ValueError("error_signature is not copied from an initial failure result")
    steps = episode["repair_steps"]
    if not isinstance(steps, list) or not steps:
        raise ValueError("failure_episode needs at least one repair step")
    step_ids: list[str] = []
    previous = max(order[value] for value in initial_results)
    final_results: list[str] = []
    referenced = {initial, *initial_results}
    for index, step in enumerate(steps):
        expected = {
            "step_id", "decision_source_event_ids", "action_source_event_id",
            "result_source_event_ids", "outcome", "semantic_change",
        }
        if not isinstance(step, Mapping) or set(step) != expected:
            raise ValueError("repair step fields differ from the v1 template")
        step_id = str(step["step_id"])
        if not step_id or step_id in step_ids:
            raise ValueError("repair step_id must be nonempty and unique")
        step_ids.append(step_id)
        decisions = validate_ids(
            step["decision_source_event_ids"], visible,
            f"repair_steps[{index}].decisions", nonempty=False,
        )
        action = str(step["action_source_event_id"])
        if action not in visible or by_id[action].get("kind") != "tool_call":
            raise ValueError("repair action must be a known tool_call")
        results = validate_ids(
            step["result_source_event_ids"], visible, f"repair_steps[{index}].results"
        )
        if order[action] <= previous or any(order[value] >= order[action] for value in decisions):
            raise ValueError("repair decision/action order is invalid")
        if any(order[value] <= order[action] for value in results):
            raise ValueError("repair results must follow their action")
        expected_outcome = "resolved" if index == len(steps) - 1 else "intermediate_failure"
        if step["outcome"] != expected_outcome:
            raise ValueError("only the final repair step may resolve the episode")
        if not isinstance(step["semantic_change"], str) or not step["semantic_change"].strip():
            raise ValueError("repair semantic_change is required")
        previous = max(order[value] for value in results)
        final_results = results
        referenced.update([*decisions, action, *results])
    resolution = validate_ids(
        episode["resolution_source_event_ids"], visible, "resolution evidence"
    )
    if not set(resolution) <= set(final_results):
        raise ValueError("resolution evidence must come from the final repair result")
    core = validate_ids(
        episode["required_core_source_event_ids"], visible, "required core"
    )
    if len(core) < 4 or [order[value] for value in core] != sorted(order[value] for value in core):
        raise ValueError("required core must contain at least four chronological events")
    optional = validate_ids(
        episode["optional_support_source_event_ids"], visible, "optional support",
        nonempty=False,
    )
    if set(core) & set(optional):
        raise ValueError("required core and optional support must be disjoint")
    validate_policy(episode["chain_policy"], visible, order, "chain_policy")
    query_policies = episode["query_policies"]
    if not isinstance(query_policies, Mapping) or set(query_policies) != {
        "audit_recovery", "interactive_reacquisition"
    }:
        raise ValueError("failure_episode query policies are incomplete")
    for query_type, policy in query_policies.items():
        validate_policy(policy, visible, order, f"query_policies.{query_type}")
    referenced.update([*resolution, *core, *optional])
    relevant_union = set(episode["chain_policy"]["relevant_evidence_ids"])
    for policy in query_policies.values():
        relevant_union.update(policy["relevant_evidence_ids"])
    if not referenced <= relevant_union:
        raise ValueError("episode stages include events excluded from every relevance policy")


def validate_reviews(
    cases_path: Path, reviews_path: Path, *, reviewer_kind: str = "human"
) -> dict[str, Any]:
    if reviewer_kind not in {"human", "ai"}:
        raise ValueError("reviewer_kind must be human or ai")
    cases = load_jsonl(cases_path)
    reviews = load_jsonl(reviews_path)
    case_map = {str(row.get("candidate_id")): row for row in cases}
    review_map = {str(row.get("candidate_id")): row for row in reviews}
    errors: list[str] = []
    if len(case_map) != len(cases):
        errors.append("cases contain duplicate candidate IDs")
    if len(review_map) != len(reviews):
        errors.append("reviews contain duplicate candidate IDs")
    if set(case_map) != set(review_map):
        errors.append("review candidate IDs differ from the packet")
    annotators: set[str] = set()
    status_counts: Counter[str] = Counter()
    metadata = ("source", "repository", "task_id", "split", "trajectory_revision")
    for candidate_id in sorted(set(case_map) & set(review_map)):
        case, row = case_map[candidate_id], review_map[candidate_id]
        try:
            if row.get("schema_version") != ANNOTATION_SCHEMA_VERSION:
                raise ValueError("annotation schema_version differs")
            if any(row.get(field) != case.get(field) for field in metadata):
                raise ValueError("frozen candidate metadata changed")
            if row.get("annotator_kind") != reviewer_kind:
                raise ValueError(f"expected annotator_kind={reviewer_kind}")
            annotator = str(row.get("annotator") or "").strip()
            if not annotator:
                raise ValueError("annotator is required")
            annotators.add(annotator)
            status = str(row.get("annotation_status") or "")
            status_counts[status] += 1
            if status == "annotated":
                if str(row.get("rejection_reason") or "").strip():
                    raise ValueError("annotated row must not contain a rejection reason")
                validate_annotated_episode(row.get("failure_episode"), case)
            elif status == "rejected":
                if not str(row.get("rejection_reason") or "").strip():
                    raise ValueError("rejected row needs a reason")
            else:
                raise ValueError("annotation_status must be annotated or rejected")
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{candidate_id}: {error}")
    if len(annotators) != 1:
        errors.append("one packet must be completed by exactly one human annotator identity")
    valid = not errors
    return {
        "schema_version": "compression_audit_failure_episode_validation_v1",
        "ready": valid,
        "formal_ready": valid and reviewer_kind == "human",
        "development_only": reviewer_kind == "ai",
        "reviewer_kind": reviewer_kind,
        "case_count": len(cases),
        "review_count": len(reviews),
        "status_counts": dict(status_counts),
        "annotators": sorted(annotators),
        "reviews_sha256": file_sha256(reviews_path),
        "errors": errors,
    }


def main() -> int:
    parser = PlainArgumentParser(
        description="校验一位人工填写的 failure_episode_gold_v1 完整审阅表。"
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--development-ai",
        action="store_true",
        help="允许 annotator_kind=ai，但结果只能用于开发，不能作为正式人工标注。",
    )
    args = parser.parse_args()
    result = validate_reviews(
        args.cases,
        args.reviews,
        reviewer_kind="ai" if args.development_ai else "human",
    )
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
