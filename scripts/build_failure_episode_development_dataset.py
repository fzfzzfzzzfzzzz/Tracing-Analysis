#!/usr/bin/env python3
"""Import adjudicated failure episodes into a development-only benchmark dataset."""

from __future__ import annotations

import copy
import json
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.build import (
    artifact_manifest,
    verify_file_manifest,
    write_file_manifest,
)
from tracegraph.benchmark.compression_audit.controlled import build_queries
from tracegraph.benchmark.compression_audit.development_experiment import (
    write_json,
    write_rows,
)
from tracegraph.benchmark.compression_audit.io import (
    file_sha256,
    load_jsonl,
    stable_digest,
)
from tracegraph.benchmark.compression_audit.models import (
    FAILURE_EPISODE_SCHEMA_VERSION,
    FailureChainGold,
    FailureEpisodeGold,
    PrefixRecord,
    QueryRecord,
)
from tracegraph.capture import estimate_tokens
from tracegraph.plain_cli import PlainArgumentParser


def _source_event_id(event: Mapping[str, Any]) -> str:
    return str(event.get("source_event_id") or event.get("event_id") or "")


def _tool_identity(event: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    content = event.get("content")
    if event.get("kind") != "tool_call" or not isinstance(content, Mapping):
        raise ValueError("strict action must come from a structured tool_call")
    action = event.get("tool_name") or content.get("name")
    arguments = content.get("arguments")
    if not isinstance(action, str) or not action or not isinstance(arguments, Mapping):
        raise ValueError("tool_call lacks exact name or arguments")
    return action, copy.deepcopy(dict(arguments))


def _normalize_policy(value: Mapping[str, Any], ids: Mapping[str, str]) -> dict[str, Any]:
    return {
        "alternative_evidence_sets": [
            [ids[str(event_id)] for event_id in evidence]
            for evidence in value["alternative_evidence_sets"]
        ],
        "relevant_evidence_ids": [
            ids[str(event_id)] for event_id in value["relevant_evidence_ids"]
        ],
        "causal_paths": [
            {
                "evidence_ids": [ids[str(event_id)] for event_id in path["evidence_ids"]],
                "constraints": [
                    [ids[str(left)], ids[str(right)]]
                    for left, right in path["constraints"]
                ],
            }
            for path in value["causal_paths"]
        ],
    }


def _normalize_episode(raw: Mapping[str, Any], ids: Mapping[str, str]) -> FailureEpisodeGold:
    value = {
        "schema_version": FAILURE_EPISODE_SCHEMA_VERSION,
        "scope": raw["scope"],
        "anchor_event_id": ids[str(raw["anchor_source_event_id"])],
        "initial_action_event_id": ids[str(raw["initial_action_source_event_id"])],
        "initial_result_event_ids": [
            ids[str(event_id)] for event_id in raw["initial_result_source_event_ids"]
        ],
        "repair_steps": [
            {
                "step_id": step["step_id"],
                "decision_event_ids": [
                    ids[str(event_id)] for event_id in step["decision_source_event_ids"]
                ],
                "action_event_id": ids[str(step["action_source_event_id"])],
                "result_event_ids": [
                    ids[str(event_id)] for event_id in step["result_source_event_ids"]
                ],
                "outcome": step["outcome"],
                "semantic_change": step["semantic_change"],
            }
            for step in raw["repair_steps"]
        ],
        "resolution_event_ids": [
            ids[str(event_id)] for event_id in raw["resolution_source_event_ids"]
        ],
        "recovery_sequence": raw["recovery_sequence"],
        "required_core_event_ids": [
            ids[str(event_id)] for event_id in raw["required_core_source_event_ids"]
        ],
        "optional_support_event_ids": [
            ids[str(event_id)] for event_id in raw["optional_support_source_event_ids"]
        ],
        "chain_policy": _normalize_policy(raw["chain_policy"], ids),
        "query_policies": {
            query_type: _normalize_policy(policy, ids)
            for query_type, policy in raw["query_policies"].items()
        },
    }
    return FailureEpisodeGold.from_dict(value)


def _anchored_text(query_type: str, anchor: str) -> str:
    scope = (
        f"the task-level failure episode beginning at public record {anchor}; "
        "do not substitute a later repair-process subfailure"
    )
    texts = {
        "audit_failed_action": (
            f"For {scope}, which method was first attempted, with what recorded arguments? "
            "Cite the supporting records."
        ),
        "audit_failure_cause": (
            f"For {scope}, why did the anchored attempt fail? Explain the task-level cause "
            "and cite its direct evidence."
        ),
        "audit_recovery": (
            f"For {scope}, what sequence of changes led to final success, and which record "
            "proves resolution?"
        ),
        "audit_chain": (
            f"Reconstruct {scope} through final successful resolution in causal order. "
            "Include necessary intermediate repair stages and cite every necessary record."
        ),
        "interactive_reacquisition": (
            f"For {scope}, explain the failure and the changes that produced final success. "
            "If the answer is not in memory, use only the permitted tools to verify it."
        ),
    }
    return texts.get(
        query_type,
        "Report the current status only. Do not revisit unrelated historical work.",
    )


def _convert(
    case: Mapping[str, Any], review: Mapping[str, Any], reviews_sha256: str
) -> tuple[PrefixRecord, FailureChainGold, list[QueryRecord]]:
    candidate_id = str(case["candidate_id"])
    source = str(case["source"])
    prefix_id = f"real:{source}:{stable_digest(candidate_id)[:16]}"
    raw_prefix = case["prefix"]
    raw_events = raw_prefix["events"]
    source_ids = [_source_event_id(event) for event in raw_events]
    if not all(source_ids) or len(source_ids) != len(set(source_ids)):
        raise ValueError(f"invalid source event IDs: {candidate_id}")
    ids = {
        source_id: f"{prefix_id}:E{index:03d}"
        for index, source_id in enumerate(source_ids, 1)
    }
    events: list[dict[str, Any]] = []
    for index, (source_id, raw) in enumerate(zip(source_ids, raw_events, strict=True), 1):
        source_kind = str(raw["kind"])
        kind = {
            "system_message": "constraint",
            "user_message": "goal",
            "assistant_message": "decision",
            "tool_result": "observation",
        }.get(source_kind, source_kind)
        event = {
            "event_id": ids[source_id],
            "step_id": index,
            "kind": kind,
            "content": raw.get("content"),
            "causal_role": str(raw.get("causal_role") or "unclassified"),
            "token_count": int(raw.get("token_count") or estimate_tokens(raw.get("content"))),
            "side_effect": bool(raw.get("side_effect")),
        }
        if kind != source_kind:
            event["source_kind"] = source_kind
        for key in ("call_id", "tool_name"):
            if raw.get(key):
                event[key] = str(raw[key])
        if raw.get("source_message_ordinal"):
            event["source_message_ordinal"] = int(raw["source_message_ordinal"])
        events.append(event)

    raw_episode = review["failure_episode"]
    episode = _normalize_episode(raw_episode, ids)
    snapshot = copy.deepcopy(dict(raw_prefix.get("environment_snapshot") or {}))
    snapshot.update(
        {
            "source": source,
            "docker_replayable": bool(case.get("replay", {}).get("docker_replayable")),
            "snapshot_ref": str(case.get("replay", {}).get("snapshot_ref") or ""),
            "side_effects_sandboxed": True,
            "history_reconstructable": raw_episode["recoverability"] != "R0",
            "unsafe_to_repeat_failed_action": raw_episode["recoverability"] == "R3",
        }
    )
    prefix = PrefixRecord(
        prefix_id=prefix_id,
        source_kind="real_trajectory",
        source_ref={
            "source": source,
            "candidate_id": candidate_id,
            "repository": str(case["repository"]),
            "task_id": str(case["task_id"]),
            "trajectory_revision": str(case["trajectory_revision"]),
            "original_split": str(case["split"]),
            "evaluation_split_override": "dev",
            "label_protocol": "failure_episode_ai_adjudication_development_only",
        },
        split="dev",
        failure_family=str(raw_episode["failure_family"]),
        task_domain=str(raw_prefix.get("task_domain") or "software").casefold(),
        recoverability=str(raw_episode["recoverability"]),
        context_length=str(raw_prefix.get("context_length") or "natural"),
        budget_tokens=int(raw_prefix.get("budget_tokens") or 4096),
        events=tuple(events),
        messages=tuple(copy.deepcopy(dict(item)) for item in raw_prefix.get("messages", ())),
        tool_schemas=tuple(
            copy.deepcopy(dict(item)) for item in raw_prefix.get("tool_schemas", ())
        ),
        environment_snapshot=snapshot,
    )
    episode.validate_against_prefix(prefix)

    by_source = {source_id: raw for source_id, raw in zip(source_ids, raw_events, strict=True)}
    failed_action, failed_arguments = _tool_identity(
        by_source[str(raw_episode["initial_action_source_event_id"])]
    )
    last_action_source = str(raw_episode["repair_steps"][-1]["action_source_event_id"])
    replacement_action, replacement_arguments = _tool_identity(by_source[last_action_source])
    initial_results = tuple(ids[str(value)] for value in raw_episode["initial_result_source_event_ids"])
    decisions = tuple(
        ids[str(value)]
        for step in raw_episode["repair_steps"]
        for value in step["decision_source_event_ids"]
    )
    repair_actions = tuple(
        ids[str(step["action_source_event_id"])] for step in raw_episode["repair_steps"]
    )
    resolution = tuple(ids[str(value)] for value in raw_episode["resolution_source_event_ids"])
    current_ids = tuple(ids[str(value)] for value in raw_prefix.get("current_source_event_ids", ()))
    evidence = {
        "failed_action": (episode.initial_action_event_id,),
        "failed_arguments": (episode.initial_action_event_id,),
        "error_signature": initial_results,
        "failure_result": initial_results,
        "failure_cause": initial_results,
        "diagnostic_evidence": initial_results,
        "switch_decision": decisions or repair_actions,
        "replacement_action": repair_actions,
        "replacement_arguments": repair_actions,
        "resolution_evidence": resolution,
        "ordered_event_ids": episode.required_core_event_ids,
        "current_fact": current_ids,
    }
    gold = FailureChainGold(
        prefix_id=prefix_id,
        failed_action=failed_action,
        failed_arguments=failed_arguments,
        error_signature=str(raw_episode["error_signature"]),
        diagnostic_evidence=str(raw_episode["diagnostic_evidence"]),
        switch_decision=str(raw_episode["recovery_sequence"]),
        replacement_action=replacement_action,
        replacement_arguments=replacement_arguments,
        resolution_evidence=str(raw_episode["resolution_evidence"]),
        ordered_event_ids=episode.required_core_event_ids,
        evidence_by_field=evidence,
        recoverability=str(raw_episode["recoverability"]),
        current_fact=str(raw_prefix.get("current_fact") or ""),
        current_event_ids=current_ids,
        failure_episode=episode,
        source_event_ids={normalized: source_id for source_id, normalized in ids.items()},
        annotation={
            "gold_source": "failure_episode_ai_adjudication",
            "human_annotated": False,
            "independent_reviewers": False,
            "development_only": True,
            "annotator": str(review["annotator"]),
            "reviews_sha256": reviews_sha256,
            "failure_episode_schema_version": FAILURE_EPISODE_SCHEMA_VERSION,
        },
    )
    queries = [
        replace(query, text=_anchored_text(query.query_type, episode.anchor_event_id))
        for query in build_queries(prefix)
        if source != "ama_bench" or query.track == "audit_qa"
    ]
    return prefix, gold, queries


def build_dataset(
    *, cases_path: Path, reviews_path: Path, controlled: Path, output: Path
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    verify_file_manifest(controlled)
    cases = load_jsonl(cases_path)
    reviews = load_jsonl(reviews_path)
    case_map = {str(row["candidate_id"]): row for row in cases}
    reviews_sha = file_sha256(reviews_path)
    accepted = [row for row in reviews if row["annotation_status"] == "annotated"]
    prefixes: list[PrefixRecord] = []
    gold_rows: list[FailureChainGold] = []
    queries: list[QueryRecord] = []
    receipts: list[dict[str, Any]] = []
    for review in accepted:
        candidate_id = str(review["candidate_id"])
        prefix, gold, item_queries = _convert(
            case_map[candidate_id], review, reviews_sha
        )
        prefixes.append(prefix)
        gold_rows.append(gold)
        queries.extend(item_queries)
        receipts.append(
            {
                "candidate_id": candidate_id,
                "prefix_id": prefix.prefix_id,
                "original_split": prefix.source_ref["original_split"],
                "evaluation_split": "dev",
                "prefix_hash": prefix.prefix_hash,
                "gold_hash": gold.gold_hash,
            }
        )

    output.mkdir(parents=True)
    for source_path in controlled.rglob("*"):
        if not source_path.is_file():
            continue
        relative = source_path.relative_to(controlled)
        if relative.as_posix() in {"manifest.json", "file_manifest.jsonl"}:
            continue
        if relative.name in {
            "real_prefixes.jsonl", "real_queries.jsonl", "real_all_gold.jsonl"
        }:
            continue
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
    for directory in ("public", "private", "audit"):
        (output / directory).mkdir(exist_ok=True)
    write_rows(output / "public" / "real_prefixes.jsonl", (row.to_dict() for row in prefixes))
    write_rows(output / "public" / "real_queries.jsonl", (row.to_dict() for row in queries))
    write_rows(output / "private" / "real_all_gold.jsonl", (row.to_dict() for row in gold_rows))
    write_rows(output / "audit" / "accepted.jsonl", receipts)

    manifest = {
        "schema_version": "compression_audit_failure_episode_ai_development_v1",
        "benchmark_id": "compression_audit_v1",
        "release": "failure-episode-ai-development-260917-r1",
        "development_only": True,
        "formal_v1_ready": False,
        "human_validation_claim": False,
        "independent_validation": False,
        "labels_visible_to_experimenter": True,
        "all_real_prefixes_relabelled_dev": True,
        "accepted_count": len(prefixes),
        "query_count": len(queries),
        "source_counts": dict(Counter(prefix.source_ref["source"] for prefix in prefixes)),
        "original_split_counts": dict(
            Counter(prefix.source_ref["original_split"] for prefix in prefixes)
        ),
        "recoverability_counts": dict(Counter(prefix.recoverability for prefix in prefixes)),
        "candidate_set_sha256": file_sha256(cases_path),
        "development_reviews_sha256": reviews_sha,
        "controlled_dataset_manifest_sha256": file_sha256(controlled / "manifest.json"),
        "data_revision": stable_digest(
            {
                "candidate_set_sha256": file_sha256(cases_path),
                "development_reviews_sha256": reviews_sha,
                "accepted_candidate_ids": [row["candidate_id"] for row in accepted],
            }
        ),
        "interpretation": (
            "Development-only task-level failure episodes generated by non-independent AI "
            "review and AI adjudication. The real population has no held-out split and cannot "
            "support formal performance or reliability claims."
        ),
    }
    manifest["artifacts"] = artifact_manifest(output)
    write_json(output / "manifest.json", manifest)
    write_file_manifest(output)
    verify_file_manifest(output)

    loaded_prefixes, loaded_queries, loaded_gold = load_dataset(output, legacy=False)
    expected_prefixes = len(load_jsonl(controlled / "public" / "prefixes.jsonl")) + len(prefixes)
    expected_queries = len(load_jsonl(controlled / "public" / "queries.jsonl")) + len(queries)
    expected_gold = len(load_jsonl(controlled / "private" / "all_gold.jsonl")) + len(gold_rows)
    if (len(loaded_prefixes), len(loaded_queries), len(loaded_gold)) != (
        expected_prefixes,
        expected_queries,
        expected_gold,
    ):
        raise ValueError("round-trip dataset population differs")
    return {
        "output": str(output),
        "dataset_manifest_sha256": file_sha256(output / "manifest.json"),
        "dataset_file_manifest_sha256": file_sha256(output / "file_manifest.jsonl"),
        "real_prefix_count": len(prefixes),
        "real_query_count": len(queries),
        "recoverability_counts": manifest["recoverability_counts"],
        "provider_requests": 0,
        "development_only": True,
    }


def main() -> int:
    parser = PlainArgumentParser(
        description="把 failure_episode AI 开发标注导入可运行数据集。"
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--controlled", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_dataset(
        cases_path=args.cases,
        reviews_path=args.reviews,
        controlled=args.controlled,
        output=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
