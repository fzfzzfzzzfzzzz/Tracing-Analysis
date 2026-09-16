"""Build an explicitly non-formal dataset from one AI review pass.

This is a development shortcut for testing an experiment idea.  It must not be
used to satisfy the formal v1 double-human annotation or held-out test gates.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .build import import_adjudicated_real, verify_file_manifest, write_file_manifest
from .development_experiment import write_json, write_rows
from .constants import FAILURE_TEMPLATES, RECOVERABILITY_LEVELS
from .io import canonical_json, file_sha256, load_jsonl, stable_digest


_IDENTITY_FIELDS = ("source", "repository", "task_id", "split", "trajectory_revision")
_FAILURE_FAMILIES = frozenset(item["id"] for item in FAILURE_TEMPLATES)
_ADJUDICATED_CHAIN_FIELDS = (
    "failure_family",
    "error_signature",
    "diagnostic_evidence",
    "switch_decision",
    "resolution_evidence",
    "ordered_source_event_ids",
    "evidence_source_event_ids_by_field",
    "recoverability",
)


def _canonicalize_exact_tool_fields(
    candidate: Mapping[str, Any], chain: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive exact action fields from reviewer-selected source events.

    Reviewers select the causal event IDs and write semantic explanations.  The
    answer protocol, however, asks the evaluated model to copy ``tool_name`` and
    ``arguments`` exactly.  Never use a reviewer's prose paraphrase as the gold
    value for those machine-exact fields.
    """

    prefix = candidate.get("prefix")
    events = prefix.get("events") if isinstance(prefix, Mapping) else None
    if not isinstance(events, list):
        raise ValueError("candidate prefix requires events for exact-field derivation")
    by_id = {str(event.get("source_event_id") or ""): event for event in events}
    if "" in by_id or len(by_id) != len(events):
        raise ValueError("candidate source event IDs must be unique and nonempty")
    evidence = chain.get("evidence_source_event_ids_by_field")
    if not isinstance(evidence, Mapping):
        raise ValueError("accepted review requires per-field evidence IDs")

    normalized = copy.deepcopy(dict(chain))
    receipt: dict[str, Any] = {"exact_fields_source": "review_selected_source_events"}
    for action_field, arguments_field in (
        ("failed_action", "failed_arguments"),
        ("replacement_action", "replacement_arguments"),
    ):
        raw_ids = evidence.get(action_field)
        if not isinstance(raw_ids, list) or not raw_ids or any(
            not isinstance(value, str) or value not in by_id for value in raw_ids
        ):
            raise ValueError(f"accepted review has invalid {action_field} evidence IDs")
        calls = [by_id[value] for value in raw_ids if by_id[value].get("kind") == "tool_call"]
        if not calls:
            raise ValueError(f"accepted review has no tool call for {action_field}")
        selected = calls[0]
        content = selected.get("content")
        if not isinstance(content, Mapping):
            raise ValueError(f"selected {action_field} event has no structured content")
        action = selected.get("tool_name") or content.get("name")
        arguments = content.get("arguments")
        if not isinstance(action, str) or not action or not isinstance(arguments, Mapping):
            raise ValueError(f"selected {action_field} event lacks exact tool fields")
        review_action = normalized.get(action_field)
        review_arguments = normalized.get(arguments_field)
        normalized[action_field] = action
        normalized[arguments_field] = copy.deepcopy(dict(arguments))
        receipt[action_field] = {
            "selected_source_event_id": str(selected["source_event_id"]),
            "review_value_changed": review_action != action,
            "review_arguments_changed": review_arguments != arguments,
            "review_exact_fields_sha256": stable_digest({
                action_field: review_action,
                arguments_field: review_arguments,
            }),
        }
    return normalized, receipt


def _event_text(value: Any) -> str:
    return value if isinstance(value, str) else canonical_json(value)


def _adjudicated_chain(
    candidate: Mapping[str, Any], adjudication: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate a development adjudication and derive its machine-exact fields."""

    candidate_id = str(candidate.get("candidate_id") or "")
    expected_prefix_id = (
        f"real:{candidate['source']}:{stable_digest(candidate_id)[:16]}"
    )
    if adjudication.get("prefix_id") != expected_prefix_id:
        raise ValueError(f"adjudication prefix differs: {candidate_id}")
    adjudicator = str(adjudication.get("adjudicator") or "")
    if not adjudicator:
        raise ValueError(f"adjudicator is missing: {candidate_id}")
    if adjudication.get("adjudication_status") != "adjudicated":
        raise ValueError(f"unsupported adjudication status: {candidate_id}")

    prefix = candidate.get("prefix")
    events = prefix.get("events") if isinstance(prefix, Mapping) else None
    if not isinstance(events, list) or not events:
        raise ValueError(f"candidate prefix requires events: {candidate_id}")
    by_id = {str(event.get("source_event_id") or ""): event for event in events}
    if "" in by_id or len(by_id) != len(events):
        raise ValueError(f"candidate event IDs differ: {candidate_id}")
    position = {event_id: index for index, event_id in enumerate(by_id)}

    chain = {field: copy.deepcopy(adjudication.get(field))
             for field in _ADJUDICATED_CHAIN_FIELDS}
    ordered = chain["ordered_source_event_ids"]
    if (not isinstance(ordered, list) or len(ordered) < 4
            or any(not isinstance(value, str) or value not in by_id for value in ordered)
            or len(set(ordered)) != len(ordered)
            or [position[value] for value in ordered]
            != sorted(position[value] for value in ordered)):
        raise ValueError(f"adjudication ordered chain is invalid: {candidate_id}")
    if chain["failure_family"] not in _FAILURE_FAMILIES:
        raise ValueError(f"adjudication failure family is invalid: {candidate_id}")
    if chain["recoverability"] not in RECOVERABILITY_LEVELS:
        raise ValueError(f"adjudication recoverability is invalid: {candidate_id}")
    for field in ("error_signature", "diagnostic_evidence", "switch_decision",
                  "resolution_evidence"):
        if not isinstance(chain[field], str) or not chain[field].strip():
            raise ValueError(f"adjudication field is empty: {candidate_id}/{field}")

    evidence = chain["evidence_source_event_ids_by_field"]
    if not isinstance(evidence, Mapping) or not evidence:
        raise ValueError(f"adjudication evidence map is invalid: {candidate_id}")
    for field, raw_ids in evidence.items():
        if (not isinstance(field, str) or not isinstance(raw_ids, list) or not raw_ids
                or any(not isinstance(value, str) or value not in by_id for value in raw_ids)
                or len(set(raw_ids)) != len(raw_ids)):
            raise ValueError(f"adjudication evidence IDs are invalid: {candidate_id}/{field}")

    failed_id = str(adjudication.get("selected_failed_action_source_event_id") or "")
    replacement_id = str(
        adjudication.get("selected_replacement_action_source_event_id") or ""
    )
    for field, event_id in (("failed_action", failed_id),
                            ("replacement_action", replacement_id)):
        if (event_id not in by_id or by_id[event_id].get("kind") != "tool_call"
                or evidence.get(field) != [event_id] or event_id not in ordered):
            raise ValueError(
                f"adjudication selected action is invalid: {candidate_id}/{field}"
            )

    failed_call = by_id[failed_id]
    error_signature = chain["error_signature"]
    failure_results = [event for event in events
                       if event.get("kind") == "tool_result"
                       and event.get("call_id") == failed_call.get("call_id")
                       and position[str(event["source_event_id"])] > position[failed_id]]
    matching_failure_results = [event for event in failure_results
                                if error_signature in _event_text(event.get("content"))]
    error_evidence = evidence.get("error_signature")
    if (not matching_failure_results or not isinstance(error_evidence, list)
            or not any(str(event["source_event_id"]) in ordered
                       and str(event["source_event_id"]) in error_evidence
                       for event in matching_failure_results)):
        raise ValueError(
            f"adjudication error signature is not from the failed result: {candidate_id}"
        )

    normalized, normalization = _canonicalize_exact_tool_fields(candidate, chain)
    normalization.update(
        label_source="third_ai_adjudication",
        adjudicator=adjudicator,
    )
    normalized["evidence_source_event_ids_by_field"] = dict(evidence)
    return normalized, normalization


def merge_single_ai_reviews(
    candidates: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
    adjudications: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate and merge accepted reviews with the full frozen candidate rows."""

    candidate_by_id = {str(row.get("candidate_id") or ""): row for row in candidates}
    review_by_id = {str(row.get("candidate_id") or ""): row for row in reviews}
    adjudication_by_id = {
        str(row.get("candidate_id") or ""): row for row in adjudications
    }
    if ("" in candidate_by_id or "" in review_by_id
            or len(candidate_by_id) != len(candidates)
            or len(review_by_id) != len(reviews)
            or "" in adjudication_by_id
            or len(adjudication_by_id) != len(adjudications)):
        raise ValueError("candidate and review IDs must be unique and nonempty")
    if set(candidate_by_id) != set(review_by_id):
        raise ValueError("single-review file must cover the frozen candidate set exactly")
    if set(adjudication_by_id) - set(candidate_by_id):
        raise ValueError("adjudication contains an unknown candidate")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    annotators: set[str] = set()
    for candidate_id in sorted(candidate_by_id):
        candidate = candidate_by_id[candidate_id]
        review = review_by_id[candidate_id]
        for field in _IDENTITY_FIELDS:
            if str(candidate.get(field) or "") != str(review.get(field) or ""):
                raise ValueError(f"review metadata differs: {candidate_id}/{field}")
        adjudication = adjudication_by_id.get(candidate_id)
        annotator = str(
            (adjudication or {}).get("adjudicator") or review.get("annotator") or ""
        )
        if not annotator:
            raise ValueError(f"review annotator is missing: {candidate_id}")
        annotators.add(annotator)
        if adjudication is not None:
            normalized_chain, normalization = _adjudicated_chain(
                candidate, adjudication
            )
            merged = copy.deepcopy(dict(candidate))
            merged["failure_chain"] = normalized_chain
            merged["annotator"] = annotator
            merged["single_ai_protocol_normalization"] = normalization
            merged["development_label_source"] = "third_ai_adjudication"
            merged["annotation_status"] = "adjudicated"
            accepted.append(merged)
            continue
        status = str(review.get("annotation_status") or "")
        if status == "rejected":
            reason = str(review.get("reject_reason") or "")
            if not reason:
                raise ValueError(f"rejected review requires a reason: {candidate_id}")
            rejected.append({
                "candidate_id": candidate_id,
                "source": candidate["source"],
                "repository": candidate["repository"],
                "task_id": candidate["task_id"],
                "original_split": candidate["split"],
                "annotator": annotator,
                "reject_reason": reason,
            })
            continue
        if status != "annotated":
            raise ValueError(f"unsupported single-review status: {candidate_id}/{status}")
        chain = review.get("failure_chain")
        if not isinstance(chain, dict):
            raise ValueError(f"accepted review requires failure_chain: {candidate_id}")
        normalized_chain, normalization = _canonicalize_exact_tool_fields(candidate, chain)
        merged = copy.deepcopy(dict(candidate))
        merged["failure_chain"] = normalized_chain
        merged["annotator"] = annotator
        merged["single_ai_protocol_normalization"] = normalization
        merged["development_label_source"] = "single_ai_review"
        # import_adjudicated_real performs the canonical structural conversion.
        # This transport-only status never appears in the emitted provenance.
        merged["annotation_status"] = "adjudicated"
        accepted.append(merged)
    if (not adjudication_by_id and len(annotators) != 1) or len(annotators) > 2:
        raise ValueError("development label sources have unexpected annotator identities")
    return accepted, rejected


def build_single_ai_development_dataset(
    *,
    candidates_path: Path,
    reviews_path: Path,
    controlled_dataset: Path,
    output: Path,
    adjudication_path: Path | None = None,
) -> dict[str, Any]:
    """Create a dev-only dataset while preserving truthful label provenance."""

    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    verify_file_manifest(controlled_dataset)
    candidates = load_jsonl(candidates_path)
    reviews = load_jsonl(reviews_path)
    adjudications = load_jsonl(adjudication_path) if adjudication_path else []
    accepted_rows, rejected_rows = merge_single_ai_reviews(
        candidates, reviews, adjudications
    )

    with tempfile.TemporaryDirectory(prefix="tracegraph-single-ai-") as directory:
        temporary = Path(directory)
        write_rows(temporary / "adjudicated.jsonl", accepted_rows)
        prefixes, gold_rows, queries = import_adjudicated_real(temporary)

    original_split_by_candidate = {
        str(row["candidate_id"]): str(row["split"]) for row in accepted_rows
    }
    accepted_by_candidate = {
        str(row["candidate_id"]): row for row in accepted_rows
    }
    development_prefixes = []
    for prefix in prefixes:
        source_ref = dict(prefix.source_ref)
        candidate_id = str(source_ref["candidate_id"])
        original_split = original_split_by_candidate[candidate_id]
        accepted = accepted_by_candidate[candidate_id]
        source_ref.update(
            original_split=original_split,
            evaluation_split_override="dev",
            label_protocol=(
                "third_ai_adjudication_development_only"
                if accepted["development_label_source"] == "third_ai_adjudication"
                else "single_ai_glm_development_only"
            ),
        )
        development_prefixes.append(replace(prefix, split="dev", source_ref=source_ref))

    development_gold = []
    for gold in gold_rows:
        candidate_id = str(gold.annotation.get("candidate_id") or "")
        if not candidate_id:
            candidate_id = str(next(
                prefix.source_ref["candidate_id"]
                for prefix in development_prefixes
                if prefix.prefix_id == gold.prefix_id
            ))
        accepted = accepted_by_candidate[candidate_id]
        adjudicated = accepted["development_label_source"] == "third_ai_adjudication"
        annotation = {
            "gold_source": (
                "third_ai_adjudication" if adjudicated else "single_ai_glm_review"
            ),
            "human_annotated": False,
            "independent_reviewers": False,
            "adjudicated": adjudicated,
            "development_only": True,
            "annotator": str(accepted["annotator"]),
            "reviews_sha256": file_sha256(reviews_path),
            "adjudication_sha256": (
                file_sha256(adjudication_path) if adjudication_path else None
            ),
            "exact_tool_fields_source": "review_selected_source_events",
            "reviewer_exact_tool_paraphrases_used_as_gold": False,
        }
        development_gold.append(replace(gold, annotation=annotation))

    output.mkdir(parents=True)
    (output / "public").mkdir()
    (output / "private").mkdir()
    (output / "audit").mkdir()
    for relative in (
        "public/prefixes.jsonl",
        "public/queries.jsonl",
        "private/all_gold.jsonl",
    ):
        source = controlled_dataset / relative
        target = output / relative
        shutil.copyfile(source, target)
    write_rows(output / "public/real_prefixes.jsonl",
               (item.to_dict() for item in development_prefixes))
    write_rows(output / "public/real_queries.jsonl", (item.to_dict() for item in queries))
    write_rows(output / "private/real_all_gold.jsonl",
               (item.to_dict() for item in development_gold))
    write_rows(output / "audit/rejected.jsonl", rejected_rows)
    accepted_receipts = [{
        "candidate_id": row["candidate_id"],
        "prefix_id": prefix.prefix_id,
        "source": row["source"],
        "original_split": row["split"],
        "evaluation_split": "dev",
        "protocol_normalization": row["single_ai_protocol_normalization"],
    } for row, prefix in zip(accepted_rows, development_prefixes, strict=True)]
    write_rows(output / "audit/accepted.jsonl", accepted_receipts)

    source_counts = Counter(str(row["source"]) for row in accepted_rows)
    original_split_counts = Counter(str(row["split"]) for row in accepted_rows)
    recoverability_counts = Counter(
        str(row["failure_chain"]["recoverability"]) for row in accepted_rows
    )
    label_source_counts = Counter(
        str(row["development_label_source"]) for row in accepted_rows
    )
    manifest = {
        "schema_version": "compression_audit_ai_adjudicated_development_v1",
        "benchmark_id": "compression_audit_v1",
        "release": (
            "glm-third-adjudication-development-260916"
            if adjudications else "glm-single-review-development-260915"
        ),
        "development_only": True,
        "independent_validation": False,
        "formal_v1_ready": False,
        "human_validation_claim": False,
        "double_annotation_complete": False,
        "labels_visible_to_experimenter": True,
        "all_real_prefixes_relabelled_dev": True,
        "exact_tool_fields_derived_from_source_events": True,
        "reviewer_exact_tool_paraphrases_are_not_gold": True,
        "accepted_count": len(accepted_rows),
        "rejected_count": len(rejected_rows),
        "source_counts": dict(source_counts),
        "original_split_counts": dict(original_split_counts),
        "recoverability_counts": dict(recoverability_counts),
        "label_source_counts": dict(label_source_counts),
        "candidate_set_sha256": file_sha256(candidates_path),
        "single_ai_reviews_sha256": file_sha256(reviews_path),
        "third_ai_adjudication_sha256": (
            file_sha256(adjudication_path) if adjudication_path else None
        ),
        "controlled_dataset_manifest_sha256": file_sha256(
            controlled_dataset / "manifest.json"
        ),
        "data_revision": stable_digest({
            "candidate_set_sha256": file_sha256(candidates_path),
            "single_ai_reviews_sha256": file_sha256(reviews_path),
            "third_ai_adjudication_sha256": (
                file_sha256(adjudication_path) if adjudication_path else None
            ),
            "accepted_candidate_ids": [row["candidate_id"] for row in accepted_rows],
        }),
        "interpretation": (
            "This dataset is a rapid machine-labelled development artifact. It can test "
            "pipeline behavior and generate hypotheses, but cannot establish formal v1, "
            "inter-annotator reliability, held-out validation, or test-set performance."
        ),
    }
    write_json(output / "manifest.json", manifest)
    manifest["artifacts"] = write_file_manifest(output)
    write_json(output / "manifest.json", manifest)
    return manifest
