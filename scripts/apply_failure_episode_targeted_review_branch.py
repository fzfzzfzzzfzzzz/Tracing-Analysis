#!/usr/bin/env python3
"""Apply the targeted failure-episode review to a development-only gold child."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.build import (
    artifact_manifest,
    verify_file_manifest,
    write_file_manifest,
)
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
    FailureEpisodeEvidencePolicy,
)
from tracegraph.plain_cli import PlainArgumentParser


SCHEMA_VERSION = "failure_episode_targeted_review_gold_migration_v1"


def parse_args() -> argparse.Namespace:
    parser = PlainArgumentParser(
        description="把定向失败链复核落成开发金标子分支，不改父数据集。"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--agreement-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _normalized_text(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", text).strip()


def _tool_identity(event: dict) -> tuple[str, dict]:
    content = event.get("content")
    if event.get("kind") != "tool_call" or not isinstance(content, dict):
        raise ValueError("reviewed failure anchor must be a public tool_call")
    action = event.get("tool_name") or content.get("name")
    arguments = content.get("arguments")
    if not isinstance(action, str) or not action or not isinstance(arguments, dict):
        raise ValueError("reviewed failure anchor lacks exact action or arguments")
    return action, dict(arguments)


def _validate_review(row: dict, prefix) -> None:
    if (
        row.get("schema_version") != "failure_episode_targeted_review_adjudication_v1"
        or row.get("adjudication_status") != "completed"
        or row.get("development_only") is not True
        or row.get("human_validated") is not False
    ):
        raise ValueError("targeted adjudication is not a completed development record")
    events = {str(event["event_id"]): event for event in prefix.events}
    order = {event_id: index for index, event_id in enumerate(events)}
    anchor = str(row.get("failed_action_event_id") or "")
    core = list(map(str, row.get("audit_chain_required_core_event_ids") or ()))
    optional = list(map(str, row.get("audit_chain_optional_relevant_event_ids") or ()))
    relevant = list(map(str, row.get("relevant_evidence_ids") or ()))
    error_results = list(map(str, row.get("error_result_event_ids") or ()))
    if (
        anchor != row.get("task_level_anchor_event_id")
        or not core
        or core[0] != anchor
        or len(core) != len(set(core))
        or len(optional) != len(set(optional))
        or set(core) & set(optional)
        or set(core) | set(optional) != set(relevant)
        or not set(relevant) <= set(events)
        or not error_results
        or not set(error_results) <= set(events)
        or [order[event_id] for event_id in core]
        != sorted(order[event_id] for event_id in core)
    ):
        raise ValueError(f"invalid targeted evidence partition: {prefix.prefix_id}")
    action, arguments = _tool_identity(events[anchor])
    if action != row.get("failed_action") or arguments != row.get("failed_arguments"):
        raise ValueError(f"strict tool identity differs from public event: {prefix.prefix_id}")
    signature = _normalized_text(row.get("error_signature"))
    if not signature or not any(
        signature in _normalized_text(events[event_id].get("content"))
        for event_id in error_results
    ):
        raise ValueError(f"error signature is absent from reviewed result: {prefix.prefix_id}")
    expected_paths = row.get("causal_paths") or ()
    if row.get("alternative_evidence_sets") != [core] or len(expected_paths) != 1:
        raise ValueError(f"review must bind exactly one minimal chain: {prefix.prefix_id}")
    if expected_paths[0].get("evidence_ids") != core:
        raise ValueError(f"causal path differs from minimal chain: {prefix.prefix_id}")


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    verify_file_manifest(args.source)
    source_manifest_sha = file_sha256(args.source / "manifest.json")
    source_file_manifest_sha = file_sha256(args.source / "file_manifest.jsonl")
    source_manifest = json.loads(
        (args.source / "manifest.json").read_text(encoding="utf-8")
    )
    agreement = json.loads(args.agreement_report.read_text(encoding="utf-8"))
    adjudication_sha = file_sha256(args.adjudication)
    if agreement.get("adjudication_sha256") != adjudication_sha:
        raise ValueError("agreement report is not bound to the adjudication file")
    if (
        agreement.get("development_only") is not True
        or agreement.get("human_validated") is not False
        or agreement.get("reviewer_independence")
        != "same_model_two_passes_not_independent"
    ):
        raise ValueError("review provenance must remain AI-only and non-independent")

    for path in sorted(item for item in args.source.rglob("*") if item.is_file()):
        relative = path.relative_to(args.source)
        if relative.as_posix() in {"manifest.json", "file_manifest.jsonl"}:
            continue
        destination = args.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)

    prefixes, queries, gold_rows = load_dataset(args.source, legacy=False)
    prefix_map = {row.prefix_id: row for row in prefixes}
    old_gold = {row.prefix_id: row for row in gold_rows}
    adjudications = {
        row["prefix_id"]: row for row in load_jsonl(args.adjudication)
    }
    agreement_ids = {
        str(row.get("prefix_id")) for row in agreement.get("cases", ())
    }
    if set(adjudications) != agreement_ids:
        raise ValueError("agreement report and adjudication populations differ")
    if len(adjudications) != 3:
        raise ValueError("targeted migration must cover exactly three prefixes")

    migrated = dict(old_gold)
    receipts: list[dict] = []
    for prefix_id, review in sorted(adjudications.items()):
        prefix = prefix_map[prefix_id]
        gold = old_gold[prefix_id]
        if gold.failure_episode is None:
            raise ValueError(f"missing source failure episode: {prefix_id}")
        _validate_review(review, prefix)
        anchor = str(review["failed_action_event_id"])
        error_results = tuple(map(str, review["error_result_event_ids"]))
        core = tuple(map(str, review["audit_chain_required_core_event_ids"]))
        optional = tuple(map(str, review["audit_chain_optional_relevant_event_ids"]))
        action, arguments = _tool_identity({
            event["event_id"]: event for event in prefix.events
        }[anchor])
        chain_policy = FailureEpisodeEvidencePolicy.from_dict({
            "alternative_evidence_sets": review["alternative_evidence_sets"],
            "relevant_evidence_ids": review["relevant_evidence_ids"],
            "causal_paths": review["causal_paths"],
        })
        episode = replace(
            gold.failure_episode,
            anchor_event_id=anchor,
            initial_action_event_id=anchor,
            initial_result_event_ids=error_results,
            required_core_event_ids=core,
            optional_support_event_ids=optional,
            chain_policy=chain_policy,
        )
        episode.validate_against_prefix(prefix)
        evidence = dict(gold.evidence_by_field)
        evidence.update({
            "failed_action": (anchor,),
            "failed_arguments": (anchor,),
            "error_signature": error_results,
            "failure_result": error_results,
            "failure_cause": error_results,
            "diagnostic_evidence": error_results,
            "ordered_event_ids": core,
        })
        annotation = {
            **gold.annotation,
            "gold_source": "failure_episode_targeted_ai_review_migration",
            "development_only": True,
            "human_annotated": False,
            "independent_reviewers": False,
            "parent_gold_hash": gold.gold_hash,
            "targeted_review_adjudication_sha256": adjudication_sha,
            "targeted_review_agreement_sha256": file_sha256(args.agreement_report),
            "reviewer_a_sha256": agreement["reviews_a_sha256"],
            "reviewer_b_sha256": agreement["reviews_b_sha256"],
            "reviewer_independence": agreement["reviewer_independence"],
            "adjudicator": review["adjudicator"],
            "adjudicator_kind": review["adjudicator_kind"],
        }
        updated = replace(
            gold,
            failed_action=action,
            failed_arguments=arguments,
            error_signature=str(review["error_signature"]),
            ordered_event_ids=core,
            evidence_by_field=evidence,
            failure_episode=episode,
            annotation=annotation,
        )
        migrated[prefix_id] = updated
        receipts.append({
            "schema_version": SCHEMA_VERSION,
            "prefix_id": prefix_id,
            "old_gold_hash": gold.gold_hash,
            "new_gold_hash": updated.gold_hash,
            "failed_action_event_id": anchor,
            "error_result_event_ids": list(error_results),
            "old_error_signature": gold.error_signature,
            "new_error_signature": updated.error_signature,
            "old_ordered_event_ids": list(gold.ordered_event_ids),
            "new_ordered_event_ids": list(core),
            "adjudication_sha256": adjudication_sha,
            "development_only": True,
            "human_validated": False,
            "independent_validation": False,
        })

    for query in queries:
        if query.prefix_id in adjudications and query.query_type != "distractor_current":
            anchor = adjudications[query.prefix_id]["failed_action_event_id"]
            if anchor not in query.text:
                raise ValueError(f"public query is not already anchored: {query.query_id}")

    source_real_gold = load_jsonl(args.source / "private" / "real_all_gold.jsonl")
    write_rows(args.output / "private" / "real_all_gold.jsonl", [
        migrated[row["prefix_id"]].to_dict() for row in source_real_gold
    ])
    write_rows(args.output / "audit" / "gold_migration.jsonl", receipts)

    manifest = dict(source_manifest)
    migrated_ids = sorted(adjudications)
    manifest.update({
        "schema_version": "compression_audit_failure_episode_ai_development_v2",
        "release": "failure-episode-ai-targeted-review-development-260917-r3",
        "development_only": True,
        "formal_v1_ready": False,
        "human_validation_claim": False,
        "independent_validation": False,
        "parent_dataset_manifest_sha256": source_manifest_sha,
        "parent_dataset_file_manifest_sha256": source_file_manifest_sha,
        "gold_migration_receipt_sha256": file_sha256(
            args.output / "audit" / "gold_migration.jsonl"
        ),
        "targeted_review_adjudication_sha256": adjudication_sha,
        "targeted_review_agreement_sha256": file_sha256(args.agreement_report),
        "targeted_review_reviewer_a_sha256": agreement["reviews_a_sha256"],
        "targeted_review_reviewer_b_sha256": agreement["reviews_b_sha256"],
        "targeted_review_reviewer_independence": agreement["reviewer_independence"],
        "migrated_prefix_ids": migrated_ids,
        "migrated_gold_hashes": {
            prefix_id: migrated[prefix_id].gold_hash for prefix_id in migrated_ids
        },
        "reanchored_query_ids": [],
        "interpretation": (
            "Development-only child dataset from two non-independent GLM-5.2 review "
            "passes plus deterministic minimal-core adjudication. Public prefixes and "
            "question text are unchanged; this is not human validation."
        ),
    })
    manifest["data_revision"] = stable_digest({
        "parent": source_manifest_sha,
        "gold": manifest["migrated_gold_hashes"],
        "adjudication": adjudication_sha,
        "agreement": manifest["targeted_review_agreement_sha256"],
    })
    manifest["artifacts"] = artifact_manifest(args.output)
    write_json(args.output / "manifest.json", manifest)
    write_file_manifest(args.output)
    verify_file_manifest(args.output)

    if (
        file_sha256(args.source / "manifest.json") != source_manifest_sha
        or file_sha256(args.source / "file_manifest.jsonl")
        != source_file_manifest_sha
    ):
        raise ValueError("source dataset changed during child construction")
    new_prefixes, new_queries, new_gold_rows = load_dataset(args.output, legacy=False)
    if [row.prefix_hash for row in new_prefixes] != [row.prefix_hash for row in prefixes]:
        raise ValueError("gold migration changed public prefixes")
    if [row.query_hash for row in new_queries] != [row.query_hash for row in queries]:
        raise ValueError("gold migration changed public queries")
    new_gold = {row.prefix_id: row for row in new_gold_rows}
    changed = {
        prefix_id for prefix_id in old_gold
        if old_gold[prefix_id].gold_hash != new_gold[prefix_id].gold_hash
    }
    if changed != set(migrated_ids):
        raise ValueError("gold migration changed an undeclared population")
    result = {
        "output": str(args.output),
        "parent_dataset_manifest_sha256": source_manifest_sha,
        "dataset_manifest_sha256": file_sha256(args.output / "manifest.json"),
        "dataset_file_manifest_sha256": file_sha256(
            args.output / "file_manifest.jsonl"
        ),
        "migrated_prefix_count": len(migrated_ids),
        "reanchored_query_count": 0,
        "provider_requests": 0,
        "development_only": True,
        "human_validated": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
