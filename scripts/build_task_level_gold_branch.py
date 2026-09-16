#!/usr/bin/env python3
"""Build a development-only gold branch for task-level failure episodes."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace
from pathlib import Path

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.build import (
    artifact_manifest,
    verify_file_manifest,
    write_file_manifest,
)
from tracegraph.benchmark.compression_audit.development_experiment import write_json, write_rows
from tracegraph.benchmark.compression_audit.development_scoring import make_rubric
from tracegraph.benchmark.compression_audit.io import file_sha256, load_jsonl, stable_digest
from tracegraph.benchmark.compression_audit.models import (
    FailureEpisodeEvidencePath,
    FailureEpisodeEvidencePolicy,
    FailureEpisodeGold,
    FailureEpisodeRepairStep,
    QueryRecord,
)
from tracegraph.benchmark.server_eval.rubrics import validate_rubric
from tracegraph.plain_cli import PlainArgumentParser


TARGETS = {
    "real:swe_gym:f31d17c3c14ddd51": {
        "primary_replacement": "E023",
        "error_signature": "Loss requires grad: False",
        "diagnostic_evidence": (
            "The task-level reproduction run completed with exit code 0 but printed "
            "'Loss requires grad: False', proving that SSIMLoss returned a tensor detached "
            "from the required gradient path. Later IndentationError results were local "
            "regressions introduced while applying the semantic fix, not the original failure."
        ),
        "switch_decision": (
            "Modify SSIMLoss.forward so the returned loss requires gradients, then correct "
            "the indentation of the inserted requires_grad_ call until the module imports."
        ),
        "resolution_evidence": (
            "The final reproduction run prints 'Loss requires grad: True' and exits with code 0, "
            "showing that the task-level gradient failure is resolved."
        ),
        "repair_steps": [
            {
                "step_id": "apply_gradient_fix",
                "decision": ["E019", "E022"],
                "action": "E023",
                "results": ["E026"],
                "outcome": "intermediate_failure",
                "semantic_change": "Make the returned SSIM loss require gradients.",
            },
            {
                "step_id": "first_indentation_fix",
                "decision": ["E027"],
                "action": "E028",
                "results": ["E031"],
                "outcome": "intermediate_failure",
                "semantic_change": "Correct the first indentation error in the inserted line.",
            },
            {
                "step_id": "final_indentation_fix",
                "decision": ["E032"],
                "action": "E033",
                "results": ["E036"],
                "outcome": "resolved",
                "semantic_change": "Align the inserted line with the method body and rerun.",
            },
        ],
        "evidence": {
            "failed_action": ["E017"],
            "failed_arguments": ["E017"],
            "error_signature": ["E018"],
            "failure_result": ["E018"],
            "failure_cause": ["E018", "E019", "E022"],
            "diagnostic_evidence": ["E018", "E019", "E022"],
            "switch_decision": ["E019", "E022", "E027", "E032"],
            "replacement_action": ["E023", "E033"],
            "replacement_arguments": ["E023", "E033"],
            "resolution_evidence": ["E036"],
        },
    },
    "real:swe_gym:9b9e94e63c2c9e54": {
        "primary_replacement": "E054",
        "error_signature": "TypeError: missing a required argument: 'spatial_dims'",
        "diagnostic_evidence": (
            "The initial UNet instantiation fails because the configuration uses dimensions "
            "instead of required spatial_dims. Successive reruns expose two more missing "
            "required arguments, channels and strides, showing one incomplete-configuration "
            "episode rather than three independent failures."
        ),
        "switch_decision": (
            "Repair the same UNet configuration in stages: rename dimensions to spatial_dims, "
            "add channels, then add strides before the final instantiation run."
        ),
        "resolution_evidence": (
            "The final run exits with code 0, prints the created UNet instance, and runs the "
            "post-instantiation destroy_ddp_group action."
        ),
        "repair_steps": [
            {
                "step_id": "rename_spatial_dims",
                "decision": ["E043"],
                "action": "E044",
                "results": ["E047"],
                "outcome": "intermediate_failure",
                "semantic_change": "Rename dimensions to spatial_dims.",
            },
            {
                "step_id": "add_channels",
                "decision": ["E048"],
                "action": "E049",
                "results": ["E052"],
                "outcome": "intermediate_failure",
                "semantic_change": "Add the required channels argument.",
            },
            {
                "step_id": "add_strides",
                "decision": ["E053"],
                "action": "E054",
                "results": ["E057"],
                "outcome": "resolved",
                "semantic_change": "Add the required strides argument and rerun.",
            },
        ],
        "evidence": {
            "failed_action": ["E041"],
            "failed_arguments": ["E041"],
            "error_signature": ["E042"],
            "failure_result": ["E042", "E047", "E052"],
            "failure_cause": ["E039", "E042", "E043", "E047", "E048", "E052", "E053"],
            "diagnostic_evidence": ["E042", "E043", "E047", "E048", "E052", "E053"],
            "switch_decision": ["E043", "E048", "E053"],
            "replacement_action": ["E044", "E049", "E054"],
            "replacement_arguments": ["E044", "E049", "E054"],
            "resolution_evidence": ["E057"],
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = PlainArgumentParser(description="创建任务级失败链的开发金标子分支，不改写父数据集。")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def anchored_text(prefix_id: str, query_type: str, anchor: str) -> str:
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
    if query_type not in texts:
        raise ValueError(f"query type should not be re-anchored: {prefix_id}/{query_type}")
    return texts[query_type]


def normalized_event_id(prefix_id: str, suffix: str) -> str:
    return f"{prefix_id}:{suffix}"


def validate_adjudication(row: dict, prefix) -> None:
    if (row.get("schema_version") != "compression_audit_chain_scope_readjudication_v1"
            or row.get("adjudication_status") != "completed"
            or row.get("selected_chain_scope") != "task_level_failure_episode"
            or row.get("adjudicator_kind") not in {"human", "ai"}
            or row.get("question_requires_explicit_anchor") is not True
            or row.get("affects_other_query_gold") is not True):
        raise ValueError("task-level readjudication contract is incomplete")
    visible = {event["event_id"] for event in prefix.events}
    order = {event["event_id"]: index for index, event in enumerate(prefix.events)}
    core = row.get("required_core_event_ids")
    relevant = row.get("relevant_evidence_ids")
    alternatives = row.get("alternative_evidence_sets")
    paths = row.get("causal_paths")
    if (not isinstance(core, list) or len(core) < 4 or len(core) != len(set(core))
            or not set(core) <= visible
            or row.get("failure_anchor_event_id") != core[0]
            or [order[value] for value in core] != sorted(order[value] for value in core)
            or not isinstance(relevant, list) or len(relevant) != len(set(relevant))
            or not set(core) <= set(relevant)
            or not isinstance(alternatives, list) or core not in alternatives
            or not isinstance(paths, list) or len(paths) != len(alternatives)
            or {frozenset(path["evidence_ids"]) for path in paths}
            != {frozenset(values) for values in alternatives}):
        raise ValueError("task-level readjudication evidence structure is invalid")
    for path in paths:
        nodes = set(path["evidence_ids"])
        if any(len(edge) != 2 or edge[0] == edge[1] or not set(edge) <= nodes
               for edge in path["constraints"]):
            raise ValueError("task-level causal path is invalid")


def tool_identity(event: dict) -> tuple[str, dict]:
    content = event.get("content")
    if event.get("kind") != "tool_call" or not isinstance(content, dict):
        raise ValueError("strict tool facts must come from a public tool_call")
    action = event.get("tool_name") or content.get("name")
    arguments = content.get("arguments")
    if not isinstance(action, str) or not action or not isinstance(arguments, dict):
        raise ValueError("tool_call lacks exact action or arguments")
    return action, dict(arguments)


def linear_policy(
    event_ids: list[str], relevant_ids: list[str]
) -> FailureEpisodeEvidencePolicy:
    constraints = tuple(zip(event_ids, event_ids[1:]))
    return FailureEpisodeEvidencePolicy(
        alternative_evidence_sets=(tuple(event_ids),),
        relevant_evidence_ids=tuple(relevant_ids),
        causal_paths=(
            FailureEpisodeEvidencePath(
                evidence_ids=tuple(event_ids), constraints=constraints
            ),
        ),
    )


def build_failure_episode(
    prefix_id: str, spec: dict, adjudication: dict
) -> FailureEpisodeGold:
    def normalize(suffix: str) -> str:
        return normalized_event_id(prefix_id, suffix)

    steps = tuple(
        FailureEpisodeRepairStep(
            step_id=raw["step_id"],
            decision_event_ids=tuple(normalize(value) for value in raw["decision"]),
            action_event_id=normalize(raw["action"]),
            result_event_ids=tuple(normalize(value) for value in raw["results"]),
            outcome=raw["outcome"],
            semantic_change=raw["semantic_change"],
        )
        for raw in spec["repair_steps"]
    )
    relevant = list(adjudication["relevant_evidence_ids"])
    recovery_ids = [
        event_id
        for step in steps
        for event_id in (step.action_event_id, *step.result_event_ids)
    ]
    initial_result_id = normalize(spec["evidence"]["failure_result"][0])
    interactive_ids = [
        adjudication["failure_anchor_event_id"], initial_result_id, *recovery_ids
    ]
    return FailureEpisodeGold(
        anchor_event_id=adjudication["failure_anchor_event_id"],
        initial_action_event_id=adjudication["failure_anchor_event_id"],
        initial_result_event_ids=(initial_result_id,),
        repair_steps=steps,
        resolution_event_ids=(normalize(spec["evidence"]["resolution_evidence"][0]),),
        recovery_sequence=spec["switch_decision"],
        required_core_event_ids=tuple(adjudication["required_core_event_ids"]),
        optional_support_event_ids=tuple(adjudication["optional_support_event_ids"]),
        chain_policy=FailureEpisodeEvidencePolicy.from_dict(
            {
                "alternative_evidence_sets": adjudication["alternative_evidence_sets"],
                "relevant_evidence_ids": relevant,
                "causal_paths": adjudication["causal_paths"],
            }
        ),
        query_policies={
            "audit_recovery": linear_policy(recovery_ids, relevant),
            "interactive_reacquisition": linear_policy(interactive_ids, relevant),
        },
    )


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    verify_file_manifest(args.source)
    source_manifest_sha = file_sha256(args.source / "manifest.json")
    source_file_manifest_sha = file_sha256(args.source / "file_manifest.jsonl")
    source_manifest = json.loads((args.source / "manifest.json").read_text(encoding="utf-8"))
    for path in sorted(item for item in args.source.rglob("*") if item.is_file()):
        relative = path.relative_to(args.source)
        if relative.as_posix() in {"manifest.json", "file_manifest.jsonl"}:
            continue
        destination = args.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)

    prefixes, queries, gold_rows = load_dataset(args.source, legacy=False)
    prefix_map = {row.prefix_id: row for row in prefixes}
    query_map = {row.query_id: row for row in queries}
    old_gold = {row.prefix_id: row for row in gold_rows}
    reviews = {row["prefix_id"]: row for row in load_jsonl(args.reviews)}
    adjudications = {row["prefix_id"]: row for row in load_jsonl(args.adjudication)}
    if set(adjudications) != set(TARGETS):
        raise ValueError("readjudication must cover exactly the two migrated prefixes")

    new_gold = dict(old_gold)
    changed_queries: list[QueryRecord] = []
    migration_rows: list[dict] = []
    adjudication_sha = file_sha256(args.adjudication)
    reviews_sha = file_sha256(args.reviews)
    for prefix_id, spec in TARGETS.items():
        prefix = prefix_map[prefix_id]
        adjudication = adjudications[prefix_id]
        validate_adjudication(adjudication, prefix)
        event_map = {event["event_id"]: event for event in prefix.events}
        failed_id = adjudication["failure_anchor_event_id"]
        replacement_id = normalized_event_id(prefix_id, spec["primary_replacement"])
        failed_action, failed_arguments = tool_identity(event_map[failed_id])
        replacement_action, replacement_arguments = tool_identity(event_map[replacement_id])
        failure_episode = build_failure_episode(prefix_id, spec, adjudication)
        failure_episode.validate_against_prefix(prefix)
        evidence = {
            key: tuple(normalized_event_id(prefix_id, suffix) for suffix in suffixes)
            for key, suffixes in spec["evidence"].items()
        }
        evidence["ordered_event_ids"] = tuple(adjudication["required_core_event_ids"])
        evidence["current_fact"] = old_gold[prefix_id].current_event_ids
        annotation = {
            **{
                key: value
                for key, value in old_gold[prefix_id].annotation.items()
                if key != "audit_chain_rubric"
            },
            "gold_source": "task_level_failure_episode_ai_readjudication",
            "development_only": True,
            "human_annotated": False,
            "independent_reviewers": False,
            "parent_gold_hash": old_gold[prefix_id].gold_hash,
            "chain_scope": "task_level_failure_episode",
            "failure_anchor_event_id": failed_id,
            "primary_replacement_event_id": replacement_id,
            "question_requires_explicit_anchor": True,
            "affects_other_query_gold": True,
            "readjudication_sha256": adjudication_sha,
            "core_review_sha256": reviews_sha,
            "adjudicator": adjudication["adjudicator"],
            "adjudicator_kind": adjudication["adjudicator_kind"],
            "failure_episode_schema_version": failure_episode.schema_version,
        }
        migrated = replace(
            old_gold[prefix_id],
            failed_action=failed_action,
            failed_arguments=failed_arguments,
            error_signature=spec["error_signature"],
            diagnostic_evidence=spec["diagnostic_evidence"],
            switch_decision=spec["switch_decision"],
            replacement_action=replacement_action,
            replacement_arguments=replacement_arguments,
            resolution_evidence=spec["resolution_evidence"],
            ordered_event_ids=tuple(adjudication["required_core_event_ids"]),
            evidence_by_field=evidence,
            failure_episode=failure_episode,
            annotation=annotation,
        )
        new_gold[prefix_id] = migrated
        prefix_query_changes = []
        for query in queries:
            if query.prefix_id != prefix_id or query.query_type == "distractor_current":
                continue
            changed = replace(
                query,
                text=anchored_text(prefix_id, query.query_type, failed_id),
            )
            query_map[query.query_id] = changed
            changed_queries.append(changed)
            prefix_query_changes.append({
                "query_id": query.query_id,
                "old_query_hash": query.query_hash,
                "new_query_hash": changed.query_hash,
            })
        migration_rows.append({
            "schema_version": "compression_audit_gold_migration_receipt_v1",
            "prefix_id": prefix_id,
            "scope": "task_level_failure_episode",
            "old_gold_hash": old_gold[prefix_id].gold_hash,
            "new_gold_hash": migrated.gold_hash,
            "failure_anchor_event_id": failed_id,
            "primary_replacement_event_id": replacement_id,
            "changed_query_receipts": prefix_query_changes,
            "adjudication_sha256": adjudication_sha,
            "reviews_sha256": reviews_sha,
            "development_only": True,
            "human_validated": False,
        })

    real_gold_source = load_jsonl(args.source / "private" / "real_all_gold.jsonl")
    write_rows(args.output / "private" / "real_all_gold.jsonl", [
        new_gold[row["prefix_id"]].to_dict() for row in real_gold_source
    ])
    real_query_source = load_jsonl(args.source / "public" / "real_queries.jsonl")
    write_rows(args.output / "public" / "real_queries.jsonl", [
        query_map[row["query_id"]].to_dict() for row in real_query_source
    ])
    write_rows(args.output / "audit" / "gold_migration.jsonl", migration_rows)

    rubric_rows = []
    for prefix_id in reviews:
        query = query_map[f"{prefix_id}:audit_chain"]
        gold = new_gold[prefix_id]
        review = adjudications.get(prefix_id, reviews[prefix_id])
        rubric = make_rubric(query, gold)
        rubric.update({
            "schema_version": "compression_audit_rubric_task_episode_migration_v1",
            "alternative_evidence_sets": review["alternative_evidence_sets"],
            "relevant_evidence_ids": review["relevant_evidence_ids"],
            "causal_mode": "partial_order",
            "causal_constraints": review["causal_paths"][0]["constraints"],
            "causal_paths": review["causal_paths"],
            "human_validated": False,
            "source": "task_level_gold_branch_plus_ai_core_review",
            "annotation_receipt": {
                "reviewer_kind": "ai",
                "reviews_sha256": reviews_sha,
                "readjudication_sha256": adjudication_sha,
            },
        })
        rubric.pop("rubric_hash", None)
        rubric["rubric_hash"] = stable_digest(rubric)
        rubric_rows.append(validate_rubric(rubric, query, prefix_map[prefix_id], gold))
    write_rows(args.output / "private" / "rubrics.task_episode_migration.jsonl", rubric_rows)

    manifest = dict(source_manifest)
    manifest.update({
        "schema_version": "compression_audit_failure_episode_development_v1",
        "release": "failure-episode-gold-v1-development-260916-r1",
        "development_only": True,
        "formal_v1_ready": False,
        "human_validation_claim": False,
        "independent_validation": False,
        "parent_dataset_manifest_sha256": source_manifest_sha,
        "parent_dataset_file_manifest_sha256": source_file_manifest_sha,
        "gold_migration_receipt_sha256": file_sha256(
            args.output / "audit" / "gold_migration.jsonl"
        ),
        "core_reviews_sha256": reviews_sha,
        "chain_scope_readjudication_sha256": adjudication_sha,
        "migrated_prefix_ids": sorted(TARGETS),
        "migrated_gold_hashes": {
            prefix_id: new_gold[prefix_id].gold_hash for prefix_id in sorted(TARGETS)
        },
        "reanchored_query_ids": sorted(query.query_id for query in changed_queries),
        "interpretation": (
            "Development-only child dataset. Two exposed prefixes exercise the first-class "
            "failure_episode_gold_v1 schema using AI adjudication; no human or "
            "independent-validation claim."
        ),
    })
    manifest["data_revision"] = stable_digest({
        "parent": source_manifest_sha,
        "gold": manifest["migrated_gold_hashes"],
        "queries": {query.query_id: query.query_hash for query in changed_queries},
        "reviews": reviews_sha,
        "readjudication": adjudication_sha,
    })
    manifest["artifacts"] = artifact_manifest(args.output)
    write_json(args.output / "manifest.json", manifest)
    write_file_manifest(args.output)
    verify_file_manifest(args.output)

    if (file_sha256(args.source / "manifest.json") != source_manifest_sha
            or file_sha256(args.source / "file_manifest.jsonl") != source_file_manifest_sha):
        raise ValueError("source frozen dataset changed during branch construction")
    new_prefixes, new_queries, new_gold_rows = load_dataset(args.output, legacy=False)
    if [row.prefix_hash for row in new_prefixes] != [row.prefix_hash for row in prefixes]:
        raise ValueError("gold migration changed public prefixes")
    if len(new_queries) != len(queries) or len(new_gold_rows) != len(gold_rows):
        raise ValueError("gold migration changed dataset population")
    new_gold_map = {row.prefix_id: row for row in new_gold_rows}
    if any(new_gold_map[prefix_id].gold_hash == old_gold[prefix_id].gold_hash
           for prefix_id in TARGETS):
        raise ValueError("migrated gold hash did not change")
    unchanged = set(old_gold) - set(TARGETS)
    if any(new_gold_map[prefix_id].gold_hash != old_gold[prefix_id].gold_hash
           for prefix_id in unchanged):
        raise ValueError("gold migration changed an unselected prefix")
    result = {
        "output": str(args.output),
        "parent_dataset_manifest_sha256": source_manifest_sha,
        "dataset_manifest_sha256": file_sha256(args.output / "manifest.json"),
        "dataset_file_manifest_sha256": file_sha256(args.output / "file_manifest.jsonl"),
        "migrated_prefix_count": len(TARGETS),
        "reanchored_query_count": len(changed_queries),
        "provider_requests": 0,
        "development_only": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
